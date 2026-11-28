# -*- coding: utf-8 -*-
"""京セラ用: 『注文番号』を鍵にして、書類から行を読む。
・自社の請求書(日付 / ご注文番号 / メーカー・品名 / 数量 / 単価 / 金額)と、
  京セラの検収明細表(注文番号/支給No. / 品名 / 入荷数 / 合格数 / 単価 / 検収金額 …)の、どちらにも、型番の列は無い。
  共通の鍵は、11桁(10で始まる)の注文番号(10077590787 など)。これを、行の『型番』の代わりにして、
  既存の照合エンジン(行ごとのひもづけ・個数・単価・金額の比較)に、そのまま渡す。
・文字入りPDF・OCR(画像)は、見出し(注文番号・品名・数量/合格数・単価・金額/検収金額)の位置から列を決める。CSV・Excelは、見出しの名前から。"""
import re, unicodedata

ORDER_RE = re.compile(r"^10\d{8,10}$")


def _nf(s):
    return unicodedata.normalize("NFKC", s or "")


def num(t):
    """数字(カンマ・小数あり)→ 整数/小数。読めなければ None(推測しない)"""
    t = _nf(t).replace(",", "").replace(" ", "").replace("円", "").replace("¥", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", t): return None
    v = float(t)
    return int(v) if v == int(v) else round(v, 2)


def _merge(toks):
    """『112, 000. 00000』『10. 000』のように、カンマや小数点のあとで、数字が分かれて読めた断片を、1つの数にする。toks=[(text,x0,x1)]
    (京セラの書類は、桁区切りと小数点のあとに、すきまが空いて見える。何度でも、つなぐ)"""
    out = []
    for t, a, b in toks:
        nt = _nf(t)
        if out:
            pt = _nf(out[-1][0]); near = a - out[-1][2] < 9
            if near and re.fullmatch(r"[\d,.]*[,.]", pt) and re.match(r"\d", nt):
                out[-1] = (out[-1][0] + t, out[-1][1], b); continue
            if near and re.fullmatch(r"\d[\d,.]*", pt) and re.fullmatch(r"[,.]\d[\d,.]*", nt):
                out[-1] = (out[-1][0] + t, out[-1][1], b); continue
        out.append((t, a, b))
    return out


HEADS = {   # 見出しの言葉 → 列の役割(上にあるものほど、優先)
    "order": ("注文番号", "発注番号", "オーダー"),
    "name": ("品名", "メーカー", "品目名", "商品名"),
    "qty": ("合格数", "入荷数", "数量", "個数"),
    "price": ("単価",),
    "amount": ("検収金額", "金額"),
}
SINKS = ("消費税", "税率", "単位", "規格", "品目コード", "取引先", "入荷日", "検収日", "提供", "引渡", "区分", "No.", "NO.", "SEQ", "発注/要求", "要求元", "支給", "請求", "残高")


def _col_of(word):
    w = _nf(word)
    if "消費税" in w: return "sink"
    for role, ws in HEADS.items():
        if any(x in w for x in ws):
            if role == "amount" and "税" in w: return "sink"
            return role
    if any(x in w for x in SINKS): return "sink"
    return None


def _fix_by_arith(row, span):
    """列の割り当てが、ずれて読めたときの保険。京セラの行は『個数 × 単価 = 金額』が必ず成り立つので、
    割り当てた3つが合わないときだけ、行の中の数字から、合う組(個数, 単価, 金額)を探して、置き直す(合う組が1つに決まらなければ、触らない)"""
    q, p, a = row["qty"], row["price"], row["amount"]
    if None not in (q, p, a) and abs(q * p - a) <= max(1.0, abs(a) * 1e-6): return
    vals = []
    for t, x0, x1, yc, h in sorted(span, key=lambda x: x[1]):
        v = num(t)
        if v is not None and v > 0 and not ORDER_RE.fullmatch(_nf(t).replace(" ", "")): vals.append(v)
    found = set()
    for i, qq in enumerate(vals):
        if qq > 100000: continue
        for j, pp in enumerate(vals):
            if j == i: continue
            for k, aa in enumerate(vals):
                if k in (i, j): continue
                if abs(qq * pp - aa) <= max(1.0, aa * 1e-6) and aa >= 1: found.add((qq, pp, aa))
    if not found: return
    best = max(found, key=lambda t: t[2])   # 税額(金額の1割)と取り違えないよう、金額が大きい組
    same = [t for t in found if t[2] == best[2]]
    if len({(t[0], t[1]) for t in same}) > 1:
        same2 = [t for t in same if t[0] == (q if q is not None else t[0]) or t[1] == (p if p is not None else t[1])]
        if len(same2) != 1: return
        best = same2[0]
    row["qty"], row["price"], row["amount"] = best
    row["problems"].append("列がずれていたので、個数×単価=金額 で置き直した")


def extract(d):
    """書類 d から、注文番号を鍵にした行の一覧を返す。注文番号が見つからなければ None(従来の読み方に戻す)"""
    if d.src == "excel": return _from_grid(d)
    lines = [L for L in d.lines if L.get("toks") and "y0" in L]
    if not lines: return None
    pages = sorted({L.get("page", 0) for L in lines})
    rows = []; cols_prev = None
    for pno in pages:
        PL = [L for L in lines if L.get("page", 0) == pno]
        toks = []   # (text, x0, x1, yc, h)
        for L in PL:
            yc = (L["y0"] + L["y1"]) / 2; h = max(4.0, L["y1"] - L["y0"])
            for t, a, b in _merge(L["toks"]): toks.append((t, a, b, yc, h))
        # 見出し(このページ、または、前のページと同じ)
        cols = {}
        # 見出しは、「数 量」「単 価」「金 額」のように、字の間が空いて、別々の語に読めることがある → 隣り合う2〜3語を、くっつけた候補も使う
        cand = list(toks)
        srt = sorted(toks, key=lambda x: (round(x[3] / 6), x[1]))
        for i in range(len(srt)):
            for n in (2, 3):
                grp = srt[i:i + n]
                if len(grp) < n or any(abs(g[3] - grp[0][3]) > 4 for g in grp) or any(len(g[0]) > 3 for g in grp): continue
                if any(grp[k + 1][1] - grp[k][2] > 14 for k in range(n - 1)): continue
                cand.append(("".join(g[0] for g in grp), grp[0][1], grp[-1][2], grp[0][3], grp[0][4]))
        for t, a, b, yc, h in cand:
            role = _col_of(t)
            if role and role != "sink":
                if role == "amount" and "検収金額" not in _nf(t) and "amount" in cols and "検収金額" in cols["amount"][2]: continue
                if role == "qty" and "qty" in cols:
                    # 優先: 合格数 > 入荷数 > 数量
                    pri = lambda x: 0 if "合格数" in _nf(x) else (1 if "入荷数" in _nf(x) else 2)
                    if pri(t) >= pri(cols["qty"][2]): continue
                if role == "name" and "name" in cols and not ("品名" in _nf(t) and "品名" not in _nf(cols["name"][2])): continue
                if role in ("price", "order") and role in cols: continue
                cols[role] = ((a + b) / 2, yc, t)
        hdr_y = max((v[1] for k, v in cols.items() if k in ("price", "amount", "order")), default=None)
        if "order" not in cols:
            if cols_prev is None: continue
            cols = cols_prev
            hdr_y = None
        else:
            cols_prev = cols
        centers = {k: v[0] for k, v in cols.items()}
        name_x1 = min([v for k, v in centers.items() if k in ("qty", "price", "amount")] or [1e9])
        name_x0 = min([a for t, a, b, yc, h in cand if "name" in cols and t == cols["name"][2]] or [-1e9])
        sinks = [((a + b) / 2) for t, a, b, yc, h in cand if _col_of(t) == "sink" and (hdr_y is None or abs(yc - hdr_y) < 22)]
        def assign(x):
            best, bd = None, 1e9
            for k, c in centers.items():
                if abs(x - c) < bd: best, bd = k, abs(x - c)
            for c in sinks:   # 『その他の列』が勝つのは、はっきり近いときだけ(同じ位置に並ぶ『消費税額』と『検収金額』は、本物の列を優先)
                if abs(x - c) + 8 < bd: best, bd = "sink", abs(x - c)
            return best
        anchors = sorted([(yc, a, b, _nf(t).replace(" ", "")) for t, a, b, yc, h in toks
                          if ORDER_RE.fullmatch(_nf(t).replace(" ", "")) and (hdr_y is None or yc > hdr_y - 2)])   # 注文番号は、11桁の形だけで決める(見出しが2段で『区分』『発注/要求元』が真下に並んでも、迷わない)
        if not anchors: continue
        hh = sum(h for *_, h in toks) / max(1, len(toks))
        for i, (ay, ax0, ax1, onum) in enumerate(anchors):
            top = ay - 0.7 * hh
            bot = (anchors[i + 1][0] - 0.7 * hh) if i + 1 < len(anchors) else ay + 4.2 * hh
            span = [x for x in toks if top <= x[3] < bot]
            row = {"model_raw": onum, "model": onum, "left": onum, "name": "", "qty": None, "price": None, "amount": None, "unit": None,
                   "problems": [], "page": pno, "src": d.src, "exact": d.src != "ocr", "raw": " ".join(x[0] for x in span)}
            name_parts = []
            for t, a, b, yc, h in sorted(span, key=lambda x: (round(x[3] / (0.8 * hh)), x[1])):
                role = assign((a + b) / 2); v = num(t)
                if role in ("qty", "price", "amount") and v is not None:
                    if row[role] is None: row[role] = v
                elif v is None and _nf(t) not in ("#", "-", "−", "ー") and not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", _nf(t)) and a >= name_x0 - 15 and (
                        (role == "name") or (abs(yc - ay) < 0.6 * hh and a < name_x1)):
                    name_parts.append(t)   # 品名の列の左端より左(日付・取引先の列)のものは、品名に入れない。注文番号と同じ段の文字は、見出しの『提供引渡日』が真下にあっても、品名とする(個数の列より左まで)
            row["name"] = "".join(name_parts)[:80]
            xs = [x[1] for x in span] + [x[2] for x in span]; ys = [x[3] - x[4] / 2 for x in span] + [x[3] + x[4] / 2 for x in span]
            row["x0"], row["x1"], row["y0"], row["y1"] = min(xs), max(xs), min(ys), max(ys)
            _fix_by_arith(row, span)
            if row["amount"] is None or row["price"] is None: row["problems"].append("単価または金額を読み取れなかった")
            rows.append(row)
    return rows or None


def _from_grid(d):
    """CSV・Excel(表)から。見出しの名前で、列を決める"""
    grid = getattr(d, "grid", None)
    if not grid: return None
    hi, cols = None, None
    for i, r in enumerate(grid):
        cand = {}
        for j, c in enumerate(r):
            role = _col_of(c)
            if role and role != "sink" and role not in cand: cand[role] = j
        if "order" in cand and ("amount" in cand or "price" in cand):
            hi, cols = i, cand; break
    if hi is None: return None
    rows = []
    for i, r in enumerate(grid[hi + 1:], start=hi + 2):
        def cell(k): return r[cols[k]] if k in cols and cols[k] < len(r) else ""
        onum = re.sub(r"\D", "", _nf(cell("order")))
        if not ORDER_RE.fullmatch(onum): continue
        row = {"model_raw": onum, "model": onum, "left": onum, "name": cell("name")[:80], "qty": num(cell("qty")), "price": num(cell("price")), "amount": num(cell("amount")),
               "unit": None, "problems": [], "page": 0, "src": "excel", "exact": True, "raw": " | ".join(r), "cell": f"{i}行目"}
        rows.append(row)
    return rows or None


def stated_total(d):
    """書類の見出しに書いてある『当月検収額』(京セラの検収明細表)。読み取った行の金額の合計と突き合わせて、読み取りが足りているかを確かめる。無ければ None"""
    txt = _nf(getattr(d, "text", "") or "")
    m = re.search(r"当月検収額\s*([\d][\d,\s]*\.?\s*\d*)", txt)
    if not m: return None
    return num(re.sub(r"\s", "", m.group(1)))


def read_check(d, rows):
    """読み取りの確認。(状態, 文) を返す。状態: ok / ng / none(確かめる材料が無い)"""
    tot = stated_total(d)
    if tot is None or not rows: return "none", ""
    got = sum((r.get("amount") or 0) for r in rows)
    if abs(got - tot) < 1:
        return "ok", f"読み取った{len(rows)}行の金額の合計 {got:,.0f}円 = 書類の『当月検収額』{tot:,.0f}円 で一致(読み取りは足りています)"
    return "ng", f"読み取った{len(rows)}行の金額の合計 {got:,.0f}円 ≠ 書類の『当月検収額』{tot:,.0f}円(差 {tot - got:,.0f}円)。読み取れていない行がある可能性があります。このあとの『京セラに無い』は、読み取り漏れかもしれません"
