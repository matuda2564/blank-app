# -*- coding: utf-8 -*-
"""見積・発注 照合ツール 中心部分(試作 v2)

方針(松田さんの決裁):
  ・「確実に一致」と言い切れる行だけ OK を出す。
  ・読み取れない/2つの読み取りが食い違う/相手が見つからない行は、必ず「要確認」か「違い」に出す。黙って通さない。
  ・過去の書類を記憶しない。1回の実行で読むのは、渡されたフォルダの中だけ。(状態を持たない)
  ・OK は2段階: OK=文字入りPDF/Excel(読み取りの間違いが起きない)  OK_IMG=画像を2種類のOCRが一致して読んだ(ほぼ確実)
"""
import os, re, io, json, unicodedata, subprocess, tempfile, base64, time
from decimal import Decimal, InvalidOperation

import fitz  # PyMuPDF
import openpyxl
from PIL import Image

DASH = "‐‑‒–—―−ー－─"
UNITS = "個枚本箱セット式台袋缶ケース組巻冊"


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    for d in DASH:
        s = s.replace(d, "-")
    return re.sub(r"\s+", "", s).upper()


def to_num(s):
    """数字だけの文字列なら数値(整数/小数)と、末尾の単位。読めなければ (None, None)(推測しない)"""
    t = unicodedata.normalize("NFKC", s or "").replace(",", "").replace("¥", "").replace("円", "").replace(" ", "").strip()
    if re.fullmatch(r"\d{1,3}\.\d{3}", t):   # 1.200 はカンマの読み違いかもしれない → 推測しない
        return None, None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([" + UNITS + r"]|PCS|pcs|PC|pc|EA|ea)?", t)
    if not m:
        return None, None
    v = Decimal(m.group(1))
    v = int(v) if v == v.to_integral_value() else float(v)
    return v, (norm(m.group(2)) if m.group(2) else None)


HDR = {
    "model": ("型式", "品番", "型番", "規格"),
    "name": ("品名", "商品名", "名称", "品目", "摘要"),
    "qty": ("数量",),
    "unit": ("単位",),
    "price": ("単価",),
    "amount": ("金額",),
    "due": ("納期",),
}
CORE = ["model", "qty", "price", "amount"]
JP = {"model": "型式", "name": "品名", "qty": "数量", "unit": "単位", "price": "単価", "amount": "金額", "due": "納期"}


class Seg:
    """1つの文字のかたまり(文字ごとのx位置つき)"""
    def __init__(self, chars, y0, y1):
        self.chars = chars  # [(ch, x0, x1)]
        self.y0, self.y1 = y0, y1

    @property
    def text(self):
        return "".join(c[0] for c in self.chars)

    @property
    def yc(self):
        return (self.y0 + self.y1) / 2


def seg_from_box(text, x0, y0, x1, y1):
    n = max(1, len(text)); w = (x1 - x0) / n
    return Seg([(ch, x0 + i * w, x0 + (i + 1) * w) for i, ch in enumerate(text)], y0, y1)


def group_lines(segs):
    segs = sorted(segs, key=lambda s: s.yc)
    lines = []
    for s in segs:
        h = max(4.0, s.y1 - s.y0)
        if lines and abs(lines[-1]["yc"] - s.yc) < 0.55 * h:
            lines[-1]["segs"].append(s)
            lines[-1]["yc"] = sum(t.yc for t in lines[-1]["segs"]) / len(lines[-1]["segs"])
        else:
            lines.append({"yc": s.yc, "segs": [s]})
    for l in lines:
        l["segs"].sort(key=lambda s: s.chars[0][1])
        l["text"] = " ".join(s.text for s in l["segs"])
        l["y0"] = min(s.y0 for s in l["segs"]); l["y1"] = max(s.y1 for s in l["segs"])
        l["x0"] = min(s.chars[0][1] for s in l["segs"]); l["x1"] = max(s.chars[-1][2] for s in l["segs"])
    return lines


def tokens_of(line):
    toks = []
    for s in line["segs"]:
        cur = []
        for ch, a, b in s.chars:
            if ch.isspace():
                if cur: toks.append(cur); cur = []
            else:
                cur.append((ch, a, b))
        if cur: toks.append(cur)
    return [("".join(c[0] for c in t), t[0][1], t[-1][2]) for t in toks]


def _has_any(nt, words):
    return any(w in nt for w in words)


def find_header(lines):
    for i, l in enumerate(lines):
        t = norm(l["text"])
        if "数量" in t and (_has_any(t, HDR["model"]) or _has_any(t, HDR["name"])):
            return i
    return None


def header_cols(line):
    """見出し行のかたまりを列にする。見出しの文字が読み違えられていても列の位置は使い、
    足りない見出しの数と読めない見出しの数が合うときだけ、標準の並び(型式・数量・単価・金額)で推定する"""
    tks = tokens_of(line)
    found, unk = {}, []
    for t, a, b in tks:
        nt = norm(t); lab = None
        for key in HDR:
            if key not in found and _has_any(nt, HDR[key]):
                lab = key; break
        if lab: found[lab] = (a, b)
        else: unk.append((a, b))
    guessed = []
    missing = [k for k in CORE if k not in found and not (k == "model" and "name" in found)]
    if unk and len(unk) == len(missing):
        for k, (a, b) in zip(missing, sorted(unk)):
            found[k] = (a, b); guessed.append(k)
        unk = []
    return found, unk, guessed


def parse_rows(lines):
    """行のリスト → (明細行, 合計, エラー)"""
    hi = find_header(lines)
    if hi is None:
        return [], None, "見出し行(型式・数量…)が見つからない"
    cols, others, guessed = header_cols(lines[hi])
    if "qty" not in cols or ("model" not in cols and "name" not in cols):
        return [], None, "見出しに型式(または品名)か数量が無い"
    name_only = "model" not in cols
    items = [(k, v) for k, v in cols.items()] + [("other%d" % i, v) for i, v in enumerate(others)]
    order = sorted(items, key=lambda kv: kv[1][0])
    bounds = []
    for i, (k, (a, b)) in enumerate(order):
        lo = -1e9 if i == 0 else (order[i - 1][1][1] + a) / 2
        hi_ = 1e9 if i == len(order) - 1 else (b + order[i + 1][1][0]) / 2
        bounds.append((k, lo, hi_))
    rows, total = [], None
    for l in lines[hi + 1:]:
        tks = tokens_of(l)
        if not tks:
            continue
        cell = {k: [] for k, _, _ in bounds}
        for t, a, b in tks:
            c = (a + b) / 2
            for k, lo, hi_ in bounds:
                if lo <= c < hi_:
                    cell[k].append(t); break
        ntext = norm(l["text"])
        if "合計" in ntext or "小計" in ntext:
            if "amount" in cell:
                v, _ = to_num("".join(cell["amount"]))
                if v is not None and "合計" in ntext: total = v
            continue
        mkey = "name" if name_only else "model"
        model = "".join(cell.get(mkey, []))
        if not model:
            continue
        nm = unicodedata.normalize("NFKC", model)
        if not name_only and not (re.search(r"[A-Za-z]", nm) and re.search(r"\d", nm)):
            continue
        r = {"model_raw": model, "model": norm(model), "y0": l["y0"], "y1": l["y1"], "x0": l["x0"], "x1": l["x1"], "raw": l["text"], "problems": [],
             "name": norm("".join(cell.get("name", []))) if "name" in cell and not name_only else None,
             "due": norm("".join(cell.get("due", []))) if "due" in cell else None,
             "unit": norm("".join(cell.get("unit", []))) if "unit" in cell else None}
        for k in ("qty", "price", "amount"):
            if k in cell:
                s = "".join(cell[k]); v, u = to_num(s)
                r[k] = v
                if k == "qty" and u:
                    if r["unit"] and r["unit"] != u: r["problems"].append(f"数量に付いた単位({u})が単位欄({r['unit']})と違う")
                    r["unit"] = r["unit"] or u
                if v is None:
                    r["problems"].append(f"{JP[k]}が数字に読めない({s or '空欄'})")
            else:
                r[k] = None
        if r.get("qty") and r.get("price") and r.get("amount") is not None and r["qty"] * r["price"] != r["amount"]:
            r["problems"].append(f"数量×単価={r['qty']*r['price']:,}が金額{r['amount']:,}と合わない")
        elif guessed and not (r.get("qty") and r.get("price") and r.get("amount") is not None):
            r["problems"].append("見出しが読めず列を推定したが、数量×単価=金額の検算ができない")
        rows.append(r)
    _finish_rows(rows, total)
    return rows, total, None


def _finish_rows(rows, total):
    """単価・金額の列が1つも読めない/合計が合わない書類は、全行を『確実』にしない"""
    if rows and all(r["price"] is None and r["amount"] is None for r in rows):
        for r in rows: r["problems"].append("単価・金額の列を読めていない(比べられない)")
    if total is not None and rows and all(r["amount"] is not None for r in rows):
        s = sum(r["amount"] for r in rows)
        if s != total:
            for r in rows: r["problems"].append(f"明細の金額の合計{s:,}が書類の合計{total:,}と合わない(読み落としの可能性)")


# ------------------------------------------------------------------ 書類の読み取り
class Doc:
    def __init__(self, path, role):
        self.path, self.role = path, role
        self.name = os.path.basename(path)
        self.rows, self.notes, self.kind = [], [], None
        self.text = ""
        self.pages = []      # 根拠表示用の画像 [(PIL.Image, scale)]
        self.total = None
        self.seconds = 0.0
        self.src = "text"    # text / excel / ocr


def page_scale_dpi(dpi):
    return dpi / 72


def is_scanned_page(page):
    try:
        area = sum((i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) for i in page.get_image_info())
        return area > 0.5 * page.rect.get_area()
    except Exception:
        return False


def text_segs_of_page(page):
    raw = page.get_text("rawdict"); segs = []
    for blk in raw["blocks"]:
        for ln in blk.get("lines", []):
            for sp in ln["spans"]:
                chars = [(c["c"], c["bbox"][0], c["bbox"][2]) for c in sp["chars"]]
                if chars:
                    segs.append(Seg(chars, sp["bbox"][1], sp["bbox"][3]))
    return segs


import threading, shutil
_TL = threading.local()
TESS_EXE = os.environ.get("MC_TESSERACT") or shutil.which("tesseract") or "tesseract"


def ocr_rapid(img):
    from rapidocr_onnxruntime import RapidOCR
    import numpy as np
    if getattr(_TL, "rapid", None) is None:
        _TL.rapid = RapidOCR()
    res, _ = _TL.rapid(np.array(img.convert("RGB")))
    segs = []
    for box, text, conf in (res or []):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        s = seg_from_box(text, min(xs), min(ys), max(xs), max(ys)); s.conf = float(conf)
        segs.append(s)
    return segs


_EASY = None
_EASY_LOCK = threading.Lock()


def ocr_easy(img):
    """EasyOCR(重い・ニューラルネットの読み取り)。モデルは初回に読み込む"""
    global _EASY
    import numpy as np, easyocr
    with _EASY_LOCK:
        if _EASY is None:
            _EASY = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
        res = _EASY.readtext(np.array(img.convert("RGB")), paragraph=False)
    segs = []
    for box, text, conf in res:
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        s = seg_from_box(text, min(xs), min(ys), max(xs), max(ys)); s.conf = float(conf)
        segs.append(s)
    return segs


def otsu_bw(img):
    """白黒にする(大津の方法)。Tesseractはこの前処理でかなり正確になる"""
    import numpy as np
    a = np.array(img.convert("L"))
    hist = np.bincount(a.ravel(), minlength=256).astype(float)
    tot = a.size; sumall = (np.arange(256) * hist).sum()
    wb = sb = 0.0; best = (0, 127)
    for t in range(256):
        wb += hist[t]
        if wb == 0 or wb == tot: continue
        sb += t * hist[t]
        mb = sb / wb; mf = (sumall - sb) / (tot - wb)
        var = wb * (tot - wb) * (mb - mf) ** 2
        if var > best[0]: best = (var, t)
    return Image.fromarray(((a > best[1]) * 255).astype("uint8"))


def ocr_tess(img):
    from PIL import ImageFilter
    big = otsu_bw(img.convert("L").filter(ImageFilter.MedianFilter(3)))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.png"); big.save(p)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        try:
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # Windows: 黒い窓を出さない
            out = subprocess.run([TESS_EXE, p, "stdout", "-l", "jpn+eng", "--psm", "6", "tsv"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=25, **kw).stdout
        except subprocess.TimeoutExpired:
            return []   # 時間切れ → 読み取りなし(=要確認になる。黙って通さない)
    rows = {}
    for ln in out.splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        rows.setdefault((f[2], f[3], f[4]), []).append((int(f[6]), int(f[7]), int(f[8]), int(f[9]), f[11], float(f[10])))
    groups = []
    for ws in rows.values():
        ws.sort(); cur = [ws[0]]
        for w in ws[1:]:
            prev = cur[-1]
            if w[0] - (prev[0] + prev[2]) > 0.8 * max(prev[3], 8):
                groups.append(cur); cur = [w]
            else:
                cur.append(w)
        groups.append(cur)
    segs = []
    for grp in groups:
        chars = []
        for (x, y, w, h, t, c) in grp:
            n = max(1, len(t)); chars += [(ch, x + i * w / n, x + (i + 1) * w / n) for i, ch in enumerate(t)]
        sg = Seg(chars, min(g[1] for g in grp), max(g[1] + g[3] for g in grp)); sg.conf = min(g[5] for g in grp) / 100.0
        segs.append(sg)
    return segs


COMPARE_KEYS = ("model", "name", "qty", "unit", "price", "amount", "due")


def merge_readers(readers):
    """複数の読み取り結果(各 rows のリスト)を行ごとに突き合わせる。全員が一致した行だけ確実。
    readers[0] を基準にする。"""
    base = readers[0]; out = []
    used = [set() for _ in readers]
    for a in base:
        ya = (a["y0"] + a["y1"]) / 2; unsure = []
        for ri in range(1, len(readers)):
            best = None
            for j, b in enumerate(readers[ri]):
                if j in used[ri]: continue
                yb = (b["y0"] + b["y1"]) / 2
                if abs(ya - yb) < 0.7 * max(a["y1"] - a["y0"], 8) and (best is None or abs(ya - yb) < best[0]):
                    best = (abs(ya - yb), j)
            if best is None:
                unsure.append(f"読み取り{ri+1}では、この行が見つからない"); continue
            used[ri].add(best[1]); b = readers[ri][best[1]]
            diff = [k for k in COMPARE_KEYS if a.get(k) != b.get(k)]
            if diff:
                unsure.append(f"読み取り{ri+1}と食い違う({'・'.join(JP[k] for k in diff)}): 1={a['model_raw']}/{a['qty']}/{a['price']}  {ri+1}={b['model_raw']}/{b['qty']}/{b['price']}")
            if b["problems"]: unsure.append(f"読み取り{ri+1}: " + " / ".join(b["problems"]))
        a["exact"] = not unsure and not a["problems"]
        a["problems"] = a["problems"] + unsure; a["src"] = "ocr"
        out.append(a)
    for ri in range(1, len(readers)):
        for j, b in enumerate(readers[ri]):
            if j not in used[ri]:
                b["exact"] = False; b["src"] = "ocr"; b["problems"].append("基準の読み取りでは、この行が見つからない"); out.append(b)
    return out


def read_doc(path, role):
    t0 = time.time()
    d = Doc(path, role)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            read_excel(d)
        elif ext == ".pdf":
            pdf = fitz.open(path)
            for pno, page in enumerate(pdf):
                scanned = is_scanned_page(page)
                tsegs = text_segs_of_page(page)
                if not scanned and tsegs:
                    pm = page.get_pixmap(dpi=110)
                    d.pages.append((Image.open(io.BytesIO(pm.tobytes("png"))), page_scale_dpi(110)))
                    d.kind = "文字入りPDF"
                    lines = group_lines(tsegs); d.text += " ".join(l["text"] for l in lines) + "\n"
                    rows, total, err = parse_rows(lines)
                    for r in rows: r["exact"] = not r["problems"]; r["page"] = pno; r["src"] = "text"
                    d.rows += rows; d.total = total if total is not None else d.total
                    if err: d.notes.append(f"{pno+1}ページ: {err}")
                else:
                    pm = page.get_pixmap(dpi=200)
                    im = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGB")
                    d.pages.append((im, 1.0)); d.kind = "スキャンPDF" if scanned else "画像PDF"
                    d.src = "ocr"
                    extra = None
                    if tsegs:  # スキャンに付いた文字層は、信用せず、3つ目の読み取りとして使う(食い違えば要確認)
                        sc = 200 / 72
                        ts = [Seg([(c[0], c[1] * sc, c[2] * sc) for c in s.chars], s.y0 * sc, s.y1 * sc) for s in tsegs]
                        extra = parse_rows(group_lines(ts))[0]
                        for r in extra: r["exact"] = True; r["problems"] = []
                    scan_ocr(d, im, pno, extra)
        elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
            im = Image.open(path).convert("RGB"); d.pages = [(im, 1.0)]; d.kind = "画像"; d.src = "ocr"
            scan_ocr(d, im, 0, None)
        else:
            d.notes.append("対応していない形式")
    except Exception as e:
        d.notes.append(f"読み込みに失敗: {e}")
    d.seconds = time.time() - t0
    return d


def scan_ocr(d, im, pno, extra):
    sa, sb = ocr_rapid(im), ocr_tess(im)
    la, lb = group_lines(sa), group_lines(sb)
    d.text += " ".join(l["text"] for l in la) + "\n"
    ra, ta, ea = parse_rows(la); rb, tb, eb = parse_rows(lb)
    if ea or eb:
        d.notes.append(f"{pno+1}ページ: 見出しを読めない({ea or '○'}/{eb or '○'})")
    readers = [ra, rb] + ([extra] if extra else [])
    rows = merge_readers([x for x in readers if x] or [ra])
    tot = ta if ta is not None and ta == tb else None
    if (ta is None) != (tb is None) or (ta is not None and ta != tb):
        d.notes.append(f"{pno+1}ページ: 合計の読み取りが一致しない/読めない({ta}/{tb})")
    for r in rows: r["page"] = pno; r["src"] = "ocr"
    if tot is not None and rows and all(r["amount"] is not None for r in rows) and sum(r["amount"] for r in rows) != tot:
        for r in rows:
            r["exact"] = False; r["problems"].append(f"明細の金額の合計と書類の合計{tot:,}が合わない(読み落としの可能性)")
    d.rows += rows
    d.total = tot


def read_excel(d):
    d.kind = "Excel"; d.src = "excel"
    wb = openpyxl.load_workbook(d.path, data_only=True)
    ws = wb.active
    grid = [[("" if c is None else str(c)) for c in row] for row in ws.iter_rows(values_only=True)]
    d.text = "\n".join(" ".join(r) for r in grid)
    hi = None
    for i, r in enumerate(grid):
        t = norm("".join(r))
        if "数量" in t and (_has_any(t, HDR["model"]) or _has_any(t, HDR["name"])):
            hi = i; break
    if hi is None:
        d.notes.append("見出し行が見つからない"); return
    idx = {}
    for j, c in enumerate(grid[hi]):
        for key in HDR:
            if key not in idx and _has_any(norm(c), HDR[key]): idx[key] = j; break
    name_only = "model" not in idx
    rows, total = [], None
    for i, r in enumerate(grid[hi + 1:], start=hi + 2):
        joined = norm("".join(r))
        if "合計" in joined or "小計" in joined:
            if "合計" in joined and "amount" in idx and idx["amount"] < len(r):
                v, _ = to_num(r[idx["amount"]]); total = v if v is not None else total
            continue
        mk = "name" if name_only else "model"
        m = r[idx[mk]] if mk in idx and idx[mk] < len(r) else ""
        nm = unicodedata.normalize("NFKC", m)
        if not m or (not name_only and not (re.search(r"[A-Za-z]", nm) and re.search(r"\d", nm))):
            continue
        row = {"model_raw": m, "model": norm(m), "raw": " | ".join(r), "problems": [], "cell": f"{i}行目", "page": 0, "src": "excel",
               "name": norm(r[idx["name"]]) if "name" in idx and not name_only and idx["name"] < len(r) else None,
               "due": norm(r[idx["due"]]) if "due" in idx and idx["due"] < len(r) else None,
               "unit": norm(r[idx["unit"]]) if "unit" in idx and idx["unit"] < len(r) else None}
        for k in ("qty", "price", "amount"):
            if k in idx and idx[k] < len(r):
                v, u = to_num(r[idx[k]]); row[k] = v
                if k == "qty" and u: row["unit"] = row["unit"] or u
                if v is None: row["problems"].append(f"{JP[k]}が数字に読めない({r[idx[k]]!r})")
            else:
                row[k] = None
        if row["qty"] and row["price"] and row["amount"] is not None and row["qty"] * row["price"] != row["amount"]:
            row["problems"].append(f"数量×単価={row['qty']*row['price']:,}が金額{row['amount']:,}と合わない")
        rows.append(row)
    _finish_rows(rows, total)
    for r in rows: r["exact"] = not r["problems"]
    d.rows = rows; d.total = total


# ------------------------------------------------------------------ 種類の判定・組み合わせ
def kind_of(text):
    t = norm(text)
    if "注文書" in t or "発注書" in t or "ご注文" in t: return "発注"
    if "見積" in t: return "見積"
    return None


def quote_no(text):
    m = re.search(r"見積(?:書)?(?:番号|No\.?|NO\.?)[:：]?\s*([A-Za-z0-9\-]+)", unicodedata.normalize("NFKC", text))
    return norm(m.group(1)) if m else None


def lev(a, b):
    if abs(len(a) - len(b)) > 3: return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def jaccard(a, b):
    """型式の重なり。1〜2文字違いの型式は0.7個ぶんとして数える"""
    a, b = set(a), set(b)
    if not (a | b): return 0.0
    ex = a & b
    ra, rb = sorted(a - ex), sorted(b - ex)
    near = 0
    for x in ra:
        for y in list(rb):
            if lev(x, y) <= 2:
                rb.remove(y); near += 1; break
    return (len(ex) + 0.7 * near) / (len(a) + len(b) - len(ex) - near)


def pair_docs(quotes, orders):
    pairs, used = [], set()
    for o in orders:
        on = quote_no(o.text)
        cand = []
        for i, q in enumerate(quotes):
            qn = quote_no(q.text)
            if on and qn and on == qn and i not in used:
                cand.append((2.0, i, "見積番号が一致"))
            else:
                sc = jaccard([r["model"] for r in q.rows], [r["model"] for r in o.rows])
                cand.append((sc, i, f"型式の重なり {sc:.0%}"))
        cand.sort(reverse=True)
        if cand and cand[0][0] >= 0.5 and (len(cand) == 1 or cand[0][0] - cand[1][0] >= 0.2 or cand[0][0] >= 2.0):
            used.add(cand[0][1]); pairs.append((quotes[cand[0][1]], o, cand[0][2]))
        else:
            pairs.append((None, o, "対応する見積書が決まらない"))
    for i, q in enumerate(quotes):
        if i not in used:
            pairs.append((q, None, "対応する発注書がない"))
    return pairs


# ------------------------------------------------------------------ 照合
def fields_of(d):
    f = set()
    for r in d.rows:
        for k in ("name", "unit", "due", "price", "amount"):
            if r.get(k) not in (None, ""): f.add(k)
    return f


def compare(q, o):
    """1組を比べる。戻り: (結果リスト, 比べた項目, 比べなかった項目)"""
    res = []
    fq, fo = fields_of(q), fields_of(o)
    both = [k for k in ("name", "unit", "price", "amount", "due") if k in fq and k in fo]
    skipped = [JP[k] for k in ("name", "unit", "price", "amount", "due") if (k in fq) != (k in fo)]
    qmap, omap = {}, {}
    for r in q.rows: qmap.setdefault(r["model"], []).append(r)
    for r in o.rows: omap.setdefault(r["model"], []).append(r)
    for mp, side in ((qmap, "q"), (omap, "o")):
        for m, rs in mp.items():
            if len(rs) > 1:
                for r in rs: res.append({"status": "REVIEW", "model": r["model_raw"], "msg": "同じ型式が複数行ある(自動では照合しない)", "qrow": r if side == "q" else None, "orow": r if side == "o" else None})
    for m in sorted(set(qmap) | set(omap)):
        if len(qmap.get(m, [])) > 1 or len(omap.get(m, [])) > 1:
            continue
        a = qmap.get(m, [None])[0]; b = omap.get(m, [None])[0]
        if a and b:
            diffs = []
            for k in ("qty", "price", "amount", "name", "unit", "due"):
                if k != "qty" and k not in both: continue
                va, vb = a.get(k), b.get(k)
                if va != vb:
                    fm = (lambda v: f"{v:,}" if isinstance(v, (int, float)) and k in ("qty", "price", "amount") else str(v))
                    diffs.append(f"{JP[k]}: 見積「{fm(va)}」⇔ 発注「{fm(vb)}」")
            probs = a["problems"] + b["problems"]
            sure = a["exact"] and b["exact"]
            ocr = a.get("src") == "ocr" or b.get("src") == "ocr"
            if diffs and sure:
                res.append({"status": "DIFF", "model": a["model_raw"], "msg": " / ".join(diffs), "qrow": a, "orow": b})
            elif diffs:
                res.append({"status": "REVIEW", "model": a["model_raw"], "msg": "違いに見えるが、読み取りが確実でない: " + " / ".join(diffs), "qrow": a, "orow": b})
            elif probs or not sure:
                res.append({"status": "REVIEW", "model": a["model_raw"], "msg": "読み取りに自信がない: " + " / ".join(dict.fromkeys(probs or ["読み取りが確実でない"])), "qrow": a, "orow": b})
            else:
                res.append({"status": "OK_IMG" if ocr else "OK", "model": a["model_raw"], "msg": "", "qrow": a, "orow": b})
        else:
            side = "見積にだけある" if a else "発注にだけある"
            one = a or b
            other = omap if a else qmap
            near = [x for x in other if lev(m, x) <= 3]
            note = f"{side}行" + (f"。近い型式: {', '.join(other[x][0]['model_raw'] for x in near)}(型式の1〜数文字の違いかもしれない)" if near else "")
            sure = one["exact"] and all(other[n][0]["exact"] for n in near)
            res.append({"status": "DIFF" if sure else "REVIEW", "model": one["model_raw"], "msg": note + ("" if sure else "(読み取りが確実でないので、断定しない)"), "qrow": a, "orow": b})
    for d, nm in ((q, "見積"), (o, "発注")):
        if not d.rows:
            res.append({"status": "REVIEW", "model": "(書類全体)", "msg": f"{nm}書から明細行を1行も読めなかった: " + " / ".join(d.notes or ["原因不明"]), "qrow": None, "orow": None, "doc": "q" if nm == "見積" else "o"})
        elif d.notes:
            res.append({"status": "REVIEW", "model": "(書類全体)", "msg": f"{nm}書: " + " / ".join(d.notes), "qrow": None, "orow": None, "doc": "q" if nm == "見積" else "o"})
    return res, [JP[k] for k in ["model", "qty"] + both], skipped


# ------------------------------------------------------------------ 根拠の画像
def crop_b64(doc, row):
    try:
        if not doc.pages or "y0" not in row: return None
        im, sc = doc.pages[row.get("page", 0)]
        f = sc if doc.kind == "文字入りPDF" else 1.0
        box = (max(0, int((row["x0"] - 20) * f)), max(0, int((row["y0"] - 8) * f)), min(im.width, int((row["x1"] + 20) * f)), min(im.height, int((row["y1"] + 8) * f)))
        c = im.crop(box)
        b = io.BytesIO(); c.save(b, "PNG")
        return base64.b64encode(b.getvalue()).decode()
    except Exception:
        return None


def row_box(doc, row):
    """行の位置を、ページ画像に対する割合(0〜1)で返す。Excelなど、画像の無い書類は None"""
    try:
        if not doc.pages or "y0" not in row: return None
        im, sc = doc.pages[row.get("page", 0)]
        f = sc if doc.kind == "文字入りPDF" else 1.0
        x0, y0, x1, y1 = (row["x0"] - 6) * f, (row["y0"] - 4) * f, (row["x1"] + 6) * f, (row["y1"] + 4) * f
        return [max(0, x0 / im.width), max(0, y0 / im.height), min(1, x1 / im.width), min(1, y1 / im.height)]
    except Exception:
        return None


def page_jpeg_b64(doc, pno, maxw=1000):
    """ページ全体の画像(JPEG・幅を縮める)。画面に出す用"""
    im, _ = doc.pages[pno]
    if im.width > maxw:
        im = im.resize((maxw, int(im.height * maxw / im.width)), Image.LANCZOS)
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=72)
    return base64.b64encode(b.getvalue()).decode()


# ------------------------------------------------------------------ 1社ぶん・全社ぶん
EXTS = (".pdf", ".xlsx", ".xlsm", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def _read_job(args):
    return read_doc(*args)


def read_many(jobs, workers=4, progress=None):
    """書類を並列に読む(画像のOCRが遅いので)。progress(done, total) があれば進み具合を知らせる"""
    out = [None] * len(jobs)
    if workers <= 1 or len(jobs) <= 1:
        for i, j in enumerate(jobs):
            out[i] = _read_job(j)
            if progress: progress(i + 1, len(jobs))
        return out
    import concurrent.futures as cf
    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_read_job, j): i for i, j in enumerate(jobs)}
        for f in cf.as_completed(futs):
            out[futs[f]] = f.result(); done += 1
            if progress: progress(done, len(jobs))
    return out


def run_vendor(vdir, workers=4, progress=None, name=None):
    out = {"vendor": name or os.path.basename(vdir), "pairs": [], "warnings": []}
    jobs = []
    for role in ("見積", "発注"):
        rd = os.path.join(vdir, role)
        if not os.path.isdir(rd): continue
        for fn in sorted(os.listdir(rd)):
            if fn.lower().endswith(EXTS):
                jobs.append((os.path.join(rd, fn), role))
    docs = {"見積": [], "発注": []}
    for d in read_many(jobs, workers, progress):
        k = kind_of(d.text)
        if k and k != d.role:
            out["warnings"].append(f"{d.role}フォルダの「{d.name}」は、中身が「{k}」と読める。入れる場所を間違えていないか確認してください")
        docs[d.role].append(d)
    out["seconds"] = sum(d.seconds for r in docs.values() for d in r)
    for q, o, why in pair_docs(docs["見積"], docs["発注"]):
        item = {"q": q, "o": o, "why": why, "results": [], "compared": [], "skipped": []}
        if q and o: item["results"], item["compared"], item["skipped"] = compare(q, o)
        out["pairs"].append(item)
    return out


def run_all(root, workers=4):
    vendors = [os.path.join(root, d) for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]
    return [run_vendor(v, workers) for v in vendors]
