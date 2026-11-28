# -*- coding: utf-8 -*-
"""入金確認ツールの中身: 銀行の入出金リスト(PDF)を、文字として取り出して、1行ずつの表(日付・名義・入金・出金・残高)にする。
・文字入りのPDFは、文字そのものを取り出す(読み間違いがない)。画像だけのPDFは、読めないと知らせる。
・「残高のつながり」(前の残高 + 入金 - 出金 = 今の残高)で、全行を検算し、読み落とし・読み違いを見つける。
・名義は、全角/半角・ひらがな/カタカナ・株式会社/(株)/カ) の違いをそろえて探す。"""
import os, re, unicodedata, datetime, collections, traceback
import fitz  # PyMuPDF

LOGFILE = None


def log(msg):
    try:
        if LOGFILE:
            with open(LOGFILE, "a", encoding="utf-8") as f: f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 名前をそろえる
_SMALL = str.maketrans("ァィゥェォャュョッヮ", "アイウエオヤユヨツワ")
LEGAL = ("株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "一般社団法人", "一般財団法人", "社団法人", "財団法人", "医療法人", "学校法人", "社会福祉法人", "特定非営利活動法人", "農業協同組合")


def norm_name(s):
    """名義を、探しやすい形にそろえる。全角/半角・ひらがな/カタカナ・空白・株式会社/(株)/カ)・記号を、無視する"""
    s = unicodedata.normalize("NFKC", s or "")
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)   # ひらがな→カタカナ
    s = s.upper()
    for w in LEGAL: s = s.replace(w, "")
    s = re.sub(r"[\(（][^\)）]{1,3}[\)）]", "", s)      # (株) (カ) (ユ) など
    s = re.sub(r"(?:^|\s)[カユドゴシザイ]\)", " ", s)     # 銀行の略称 カ) ユ) が、頭についた形
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[^\w]", "", s)                          # 記号・長音以外の区切りを消す
    s = s.replace("ー", "")                                  # 長音(ー)の有無の違いも吸収
    s = s.translate(_SMALL)                                  # 小さい「ャュョッ」を大きい字に(銀行の名義は、小さい字を使わない)
    return s


# ---------------------------------------------------------------- 日付
def _era(y_era, letter):
    base = {"R": 2018, "H": 1988, "S": 1925}.get(letter)
    return base + y_era if base else None


DATE_RES = [
    (re.compile(r"(\d{4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})"), lambda m, y: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), lambda m, y: (int(m[1]), int(m[2]), int(m[3]))),   # 20261101 の形
    (re.compile(r"(令和|平成|昭和|[RHS])\s*(\d{1,2})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})"),
     lambda m, y: (_era(int(m[2]), {"令和": "R", "平成": "H", "昭和": "S"}.get(m[1], m[1])), int(m[3]), int(m[4]))),
    (re.compile(r"(?<!\d)(\d{2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?!\d)"), lambda m, y: (2000 + int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(?<!\d)(\d{1,2})\s*[/月]\s*(\d{1,2})\s*日?(?!\d)"), lambda m, y: (y, int(m[1]), int(m[2]))),
]


def parse_date(text, default_year=None):
    t = unicodedata.normalize("NFKC", text or "")
    for rx, f in DATE_RES:
        m = rx.search(t)
        if not m: continue
        try:
            y, mo, d = f(m, default_year)
            if y is None: continue
            return datetime.date(y, mo, d).isoformat()
        except Exception:
            continue
    return None


AMT_RE = re.compile(r"[△▲\-−]?[\d,]+円?-?")


def to_amount(text):
    t = unicodedata.normalize("NFKC", text or "").replace(",", "").replace("円", "").replace("¥", "").replace("\\", "").strip()
    neg = t.startswith(("-", "△", "▲", "−")) or t.endswith("-")
    t = t.lstrip("-△▲−").rstrip("-")
    if re.fullmatch(r"\d+", t): return -int(t) if neg else int(t)
    return None


# ---------------------------------------------------------------- 見出しから、列の位置を決める
HEAD = {
    "kind": ("区分", "入出金", "取引種別"),
    "date": ("日付", "取引日", "年月日", "取引年月日", "処理日", "お取引日", "月日"),
    "desc": ("摘要", "お取引内容", "取引内容", "内容", "取引先", "振込人", "お名前", "名義", "備考", "適用", "取引名"),
    "out": ("出金", "お支払", "お引出", "払戻", "支払金額", "引出", "お引き出し", "支払"),
    "in": ("入金", "お預り", "お預入", "預入", "お預かり", "預り金額", "預かり", "入金額"),
    "bal": ("残高",),
    "amt": ("金額", "取引金額"),
}


def _key_of(word):
    w = unicodedata.normalize("NFKC", word).replace(" ", "")
    for k, ws in HEAD.items():
        if any(x in w for x in ws): return k
    return None


def group_lines(words):
    """単語(x0,y0,x1,y1,text)を、同じ高さごとの行にする"""
    words = sorted(words, key=lambda w: (w[1] + w[3]) / 2)
    lines = []
    for w in words:
        h = max(3.0, w[3] - w[1]); yc = (w[1] + w[3]) / 2
        if lines and abs(lines[-1]["yc"] - yc) < 0.55 * h:
            L = lines[-1]; L["w"].append(w); L["yc"] = sum((x[1] + x[3]) / 2 for x in L["w"]) / len(L["w"])
        else:
            lines.append({"yc": yc, "w": [w]})
    for L in lines:
        L["w"].sort(key=lambda w: w[0]); L["y0"] = min(w[1] for w in L["w"]); L["y1"] = max(w[3] for w in L["w"])
    return lines


def find_header(lines):
    best = None
    for i, L in enumerate(lines):
        cols = {}
        for w in L["w"]:
            k = _key_of(w[4])
            if k and k not in cols: cols[k] = w
        if "bal" in cols and ("in" in cols or "out" in cols) and "date" in cols:
            best = (i, cols); break
        if "date" in cols and ("in" in cols or "out" in cols) and best is None:
            best = (i, cols)
    return best


def _col_of(w, cols):
    """単語が、どの列か(見出しの中心か、右端が、いちばん近い列)"""
    cx, rx = (w[0] + w[2]) / 2, w[2]
    best, bd = None, 1e9
    for k, h in cols.items():
        hc, hr = (h[0] + h[2]) / 2, h[2]
        d = min(abs(cx - hc), abs(rx - hr))
        if d < bd: best, bd = k, d
    return best


# ---------------------------------------------------------------- PDFを読む
def read_pdf(path, name=None, progress=None, cancel=None):
    name = name or os.path.basename(path)
    doc = {"name": name, "path": path, "pages": 0, "rows": [], "notes": [], "page_stats": [], "order": "asc", "swapped": False, "text_pages": 0}
    pdf = fitz.open(path)
    doc["pages"] = len(pdf)
    cols = None; cur = None; default_year = None
    m = re.search(r"(20\d{2})", name)
    if m: default_year = int(m.group(1))
    for pno in range(len(pdf)):
        if cancel and cancel.is_set(): break
        if progress: progress(pno, len(pdf))
        page = pdf[pno]; W, H = page.rect.width, page.rect.height
        words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
        st = {"page": pno + 1, "words": len(words), "rows": 0, "header": False, "note": ""}
        if not words:
            st["note"] = "文字が取れませんでした(画像だけのページかもしれません)"; doc["page_stats"].append(st); continue
        doc["text_pages"] += 1
        txt = " ".join(w[4] for w in words)
        ym = re.search(r"(20\d{2})\s*年\s*\d{1,2}\s*月", unicodedata.normalize("NFKC", txt)) or re.search(r"(令和|R)\s*(\d{1,2})\s*年", unicodedata.normalize("NFKC", txt))
        if ym:
            default_year = int(ym.group(1)) if ym.group(1).isdigit() else 2018 + int(ym.group(2))
        lines = group_lines(words)
        hd = find_header(lines)
        start = 0
        if hd:
            hi, hc = hd
            cols = hc; st["header"] = True; start = hi + 1
        elif cols is None:
            st["note"] = "見出し(日付・入金・出金・残高)が見つかりません"; doc["page_stats"].append(st); continue
        for L in lines[start:]:
            cw = [(w, _col_of(w, cols)) for w in L["w"]]
            # 金額の列に入った、数字でない文字は、長い名義のはみ出し(名義の続き)として、摘要に戻す
            cw = [(w, ("desc" if (k in ("out", "in", "bal") and not AMT_RE.fullmatch(unicodedata.normalize("NFKC", w[4]).strip())) else k)) for w, k in cw]
            dcol = " ".join(w[4] for w, k in cw if k == "date")
            iso = parse_date(dcol, default_year) if dcol else None
            def colval(key):
                return [w for w, k in cw if k == key]
            if iso:
                cur = {"date": iso, "desc": " ".join(w[4] for w in colval("desc")), "out": None, "in": None, "bal": None, "page": pno + 1,
                       "bbox": [min(w[0] for w in L["w"]) / W, L["y0"] / H, max(w[2] for w in L["w"]) / W, L["y1"] / H], "raw": " ".join(w[4] for w in L["w"])}
                for key in ("out", "in", "bal"):
                    v = to_amount("".join(w[4] for w in colval(key)))
                    cur[key] = v
                doc["rows"].append(cur); st["rows"] += 1
            elif cur and cur["page"] == pno + 1:
                # 日付の無い行: 名義が2行にまたがっている続き(摘要の列にだけ、文字がある)
                other = [w for w, k in cw if k not in ("desc", None)]
                extra = " ".join(w[4] for w in colval("desc"))
                if extra and not other and (L["y0"] - (cur["bbox"][3] * H)) < 14:
                    cur["desc"] = (cur["desc"] + " " + extra).strip(); cur["bbox"][3] = max(cur["bbox"][3], L["y1"] / H); cur["raw"] += " / " + extra
        doc["page_stats"].append(st)
    if progress: progress(len(pdf), len(pdf))
    if not doc["rows"]:
        doc["notes"].append("行を1つも読み取れませんでした。画像だけのPDFか、見出しの形が想定と違うかもしれません")
    check_balance(doc)
    return doc


def read_csv(path, name=None, progress=None, cancel=None, override=None):
    """銀行からダウンロードしたCSV(UTF-8 / Shift_JIS・カンマ/タブ区切り)を、PDFと同じ形の表にする。
    見出しの名前から、列(日付・名義・入金・出金・残高)を決める。名義の列が見つからないときは、推測する。
    override={'date':列番号,'desc':…,'in':…,'out':…,'bal':…}(-1=その列は無い)で、列の割り当てを、人が決め直せる。"""
    import csv
    name = name or os.path.basename(path)
    doc = {"name": name, "path": path, "pages": 0, "rows": [], "notes": [], "page_stats": [], "order": "asc", "swapped": False, "text_pages": 0, "kind": "csv"}
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "cp932", "utf-16"):
        try: text = raw.decode(enc); break
        except Exception: continue
    if text is None:
        text = raw.decode("utf-8", "replace"); doc["notes"].append("文字コードを判別できなかったので、文字化けがあるかもしれません")
    head = text[:4000]
    delim = max((",", "\t", ";"), key=lambda c: head.count(c))
    grid = [[c.strip() for c in r] for r in csv.reader(text.splitlines(), delimiter=delim)]
    hi, cols = None, None
    for i, r in enumerate(grid):
        cand = {}
        for j, c in enumerate(r):
            k = _key_of(c)
            if k and k not in cand: cand[k] = j
        if "date" in cand and (("in" in cand or "out" in cand) or ("amt" in cand)):
            hi, cols = i, cand; break
    if hi is None:
        doc["notes"].append("見出し行(日付・入金額・出金額など)が見つかりません。CSVの1行目(列の名前)を見せてもらえれば、読み方を合わせます"); return doc
    headers = list(grid[hi])
    default_year = None
    m = re.search(r"(20\d{2})", name)
    if m: default_year = int(m.group(1))
    guessed = False
    if override:
        for k, v in override.items():
            if v is None or v < 0: cols.pop(k, None)
            else: cols[k] = v
    body = [r for r in grid[hi + 1:] if any(r)]
    if "desc" in cols and not (override and "desc" in override):   # 「備考」など、見出しから決めた名義の列が、ほとんど空なら、別の列を探す
        j0 = cols["desc"]; filled = sum(1 for r in body if j0 < len(r) and r[j0].strip())
        if body and filled < 0.3 * len(body): cols.pop("desc")
    if "desc" not in cols:   # 名義の列が見つからない: 数字でも日付でもない文字が、いちばん長い列を、名義と推測する
        best, bl = None, 0
        for j in range(len(headers)):
            if j in cols.values(): continue
            vals = [r[j] for r in body[:200] if j < len(r) and r[j]]
            txt = [v for v in vals if not re.fullmatch(r"[\d,\-/.\s]+", unicodedata.normalize("NFKC", v))]
            if len(txt) >= max(1, len(vals) // 2):
                avg = sum(len(v) for v in txt) / max(1, len(txt))
                if avg > bl: best, bl = j, avg
        if best is not None:
            cols["desc"] = best; guessed = True
            doc["notes"].append(f"名義の列は、見出しからは決められなかったので、「{headers[best] or str(best + 1) + '列目'}」と推測しました。違うときは、下の「列の割り当て」で選び直してください")
        else:
            doc["notes"].append("名義(お客さんの名前)の列が見つかりません。下の「列の割り当て」で、名義の列を選んでください")
    doc["csv"] = {"headers": headers, "cols": dict(cols), "guessed": guessed}
    def cell(r, k): return r[cols[k]] if k in cols and cols[k] < len(r) else ""
    for i, r in enumerate(grid[hi + 1:], start=hi + 2):
        iso = parse_date(cell(r, "date"), default_year)
        if not iso: continue
        row = {"date": iso, "desc": cell(r, "desc"), "out": to_amount(cell(r, "out")), "in": to_amount(cell(r, "in")), "bal": to_amount(cell(r, "bal")),
               "page": 0, "bbox": None, "raw": delim.join(r), "csvline": i}
        if "amt" in cols and row["in"] is None and row["out"] is None:   # 金額1列 + 入出金の区分
            a = to_amount(cell(r, "amt")); kd = unicodedata.normalize("NFKC", cell(r, "kind"))
            if a is not None:
                if any(w in kd for w in ("入金", "預入", "預り", "入")) and "出" not in kd: row["in"] = abs(a)
                elif any(w in kd for w in ("出金", "支払", "引出", "払")): row["out"] = abs(a)
                else: row["in" if a >= 0 else "out"] = abs(a)
        doc["rows"].append(row)
    if not doc["rows"]: doc["notes"].append("行を1つも読み取れませんでした(日付の形が、想定と違うかもしれません)")
    check_balance(doc)
    return doc


def check_balance(doc):
    """残高のつながりで、全行を検算する(前の残高 + 入金 - 出金 = 今の残高)。合わない行に、印をつける"""
    rows = doc["rows"]
    if len(rows) < 2 or not any(r["bal"] is not None for r in rows):
        for r in rows: r["chk"] = "na"
        if rows and not any(r["bal"] is not None for r in rows):
            doc["no_balance"] = True   # 残高の列が無い(この明細は、入金額・出金額だけ): 検算はしない。件数と合計を、銀行の画面と見比べてもらう
        return
    ordered = rows if rows[0]["date"] <= rows[-1]["date"] else list(reversed(rows))
    doc["order"] = "asc" if ordered is rows else "desc"
    def run(swap):
        ok = bad = 0
        prev = None
        for r in ordered:
            if r["bal"] is None: prev = None; continue
            if prev is not None:
                net = (r["in"] or 0) - (r["out"] or 0)
                if swap: net = -net
                if prev + net == r["bal"]: ok += 1
                else: bad += 1
            prev = r["bal"]
        return ok, bad
    ok0, bad0 = run(False); ok1, bad1 = run(True)
    swap = (ok1 > ok0)   # 入金と出金の列が、逆に読めている
    doc["swapped"] = swap
    if swap: doc["notes"].append("入金と出金の列が、見出しと逆に読めている可能性があります(残高のつながりが、逆にしたほうが合うため)")
    prev = None; nok = nbad = nna = 0
    for r in ordered:
        if r["bal"] is None or prev is None:
            r["chk"] = "na"; nna += 1
        else:
            net = (r["in"] or 0) - (r["out"] or 0)
            if swap: net = -net
            if prev + net == r["bal"]: r["chk"] = "ok"; nok += 1
            else: r["chk"] = "ng"; nbad += 1
        prev = r["bal"] if r["bal"] is not None else None
    doc["balance"] = {"ok": nok, "ng": nbad, "na": nna}


# ---------------------------------------------------------------- 検索
def search(docs, query):
    """名前(複数は、空白・カンマ・読点で区切る=どれかに当たればよい)で探す。戻り: 名義のグループ一覧と、当たった行"""
    qs = [norm_name(q) for q in re.split(r"[\s,、，]+", unicodedata.normalize("NFKC", query or "").strip()) if q.strip()]
    qs = [q for q in qs if q]
    if not qs: return {"groups": [], "rows": [], "query": query}
    hits = []
    for di, d in enumerate(docs):
        for ri, r in enumerate(d["rows"]):
            n = norm_name(r["desc"])
            if n and any(q in n for q in qs): hits.append((di, ri, r, n))
    groups = collections.OrderedDict()
    for di, ri, r, n in hits:
        g = groups.setdefault(n, {"key": n, "names": collections.Counter(), "count": 0, "in": 0, "out": 0})
        g["names"][r["desc"]] += 1; g["count"] += 1; g["in"] += r["in"] or 0; g["out"] += r["out"] or 0
    glist = []
    for i, (n, g) in enumerate(groups.items()):
        glist.append({"id": i, "key": n, "name": g["names"].most_common(1)[0][0], "variants": list(g["names"].keys())[:5], "count": g["count"], "in": g["in"], "out": g["out"],
                      "exact": any(q == n for q in qs)})
    gid = {g["key"]: g["id"] for g in glist}
    rows = [{"doc": di, "i": ri, "g": gid[n], "date": r["date"], "desc": r["desc"], "in": r["in"], "out": r["out"], "bal": r["bal"], "page": r["page"], "chk": r.get("chk", "na"), "file": docs[di]["name"], "box": ([round(x, 4) for x in r["bbox"]] if r.get("bbox") else None), "raw": r.get("raw", "")}
            for di, ri, r, n in hits]
    rows.sort(key=lambda r: (r["date"], r["file"]))
    return {"groups": glist, "rows": rows, "query": query}
