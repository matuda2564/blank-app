# -*- coding: utf-8 -*-
"""見積・発注 照合ツール 中心部分(試作 v2)

方針(松田さんの決裁):
  ・「確実に一致」と言い切れる行だけ OK を出す。
  ・読み取れない/2つの読み取りが食い違う/相手が見つからない行は、必ず「要確認」か「違い」に出す。黙って通さない。
  ・過去の書類を記憶しない。1回の実行で読むのは、渡されたフォルダの中だけ。(状態を持たない)
  ・OK は2段階: OK=文字入りPDF/Excel(読み取りの間違いが起きない)  OK_IMG=画像を2種類のOCRが一致して読んだ(ほぼ確実)
"""
import os, re, io, json, unicodedata, subprocess, tempfile, base64, time, gc, sys, traceback
from decimal import Decimal, InvalidOperation

import fitz  # PyMuPDF
import openpyxl
from PIL import Image


# ------------------------------------------------------------------ メモリとログ(落ちる原因を減らす・落ちたとき原因を残す)
MAX_PAGES = int(os.environ.get("MC_MAXPAGES") or 40)      # 1書類で読むページの上限(超えたら、読んでいないと明示する)
MAX_SIDE = int(os.environ.get("MC_MAXSIDE") or 3600)      # 画像の長い辺の上限(これより大きい写真は縮める)
MB_PER_WORKER = 800                                       # 同時に読む1本あたり、見込むメモリ(MB)
LOGFILE = None


def log(msg):
    """ログをファイルに残す(落ちた原因を、あとで調べるため)。書類の中身は、書かない"""
    if not LOGFILE: return
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def avail_mb():
    """いま、使えるメモリ(MB)。わからなければ None"""
    try:
        if os.name == "nt":
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MS(); st.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullAvailPhys // 2 ** 20)
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable"):
                    return int(ln.split()[1]) // 1024
    except Exception:
        pass
    return None


def auto_workers(max_w=4):
    """同時に読む数。パソコンのコア数と、空いているメモリから決める(足りないパソコンで落ちないように)"""
    w = min(max_w, max(1, os.cpu_count() or 2))
    a = avail_mb()
    if a is not None:
        w = min(w, max(1, int(a // MB_PER_WORKER)))
    return w


def tess_osd(im):
    """Tesseractの向き判定(OSD)。戻り: (直すために、時計回りに回す角度, 確信度) / 判定できなければ None"""
    try:
        small = im.copy(); small.thumbnail((1600, 1600))
        with tempfile.TemporaryDirectory(dir=safe_tmp_dir()) as d:
            pth = os.path.join(d, "a.png"); small.convert("L").save(pth)
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
            cp = subprocess.run([TESS_EXE, pth, "stdout", "--psm", "0"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, **kw)
        m1 = re.search(r"Rotate:\s*(\d+)", cp.stdout or ""); m2 = re.search(r"Orientation confidence:\s*([\d.]+)", cp.stdout or "")
        if not (m1 and m2): return None
        return int(m1.group(1)) % 360, float(m2.group(2) if False else m2.group(1))
    except Exception:
        return None


def best_rotation(im):
    """スキャン・FAXの向きを、判定する。戻り: (PILで反時計回りに回す角度 0/90/180/270, 参考情報)。
    Tesseractの向き判定を使う。回した結果を、もう一度、判定して、直ったことを確かめる(回す向きを、取り違えないため)"""
    r = tess_osd(im)
    if r is None: return 0, {"osd": "判定できなかった"}
    rot, conf = r
    if rot == 0 or conf < 2.0: return 0, {"osd": r}
    for ang in ((360 - rot) % 360, rot):   # まず、時計回りに rot 度(=反時計回りに 360-rot 度)。確かめて、だめなら、逆
        r2 = tess_osd(im.rotate(ang, expand=True))
        if r2 and r2[0] == 0: return ang, {"osd": r, "after": r2}
    return 0, {"osd": r, "after": "回しても、直らなかった"}


class Cancelled(Exception):
    """利用者が「中止」を押した"""


def safe_tmp_dir():
    """一時ファイルを置く場所。ユーザー名が日本語だと、Tesseractが、画像を開けないことがあるので、英数字だけの場所を選ぶ"""
    t = tempfile.gettempdir()
    try:
        t.encode("ascii"); return t
    except UnicodeEncodeError:
        pass
    for c in (os.environ.get("PUBLIC"), os.environ.get("ProgramData"), "C:\\Users\\Public", "C:\\ProgramData", "/tmp"):
        try:
            if c and c.encode("ascii") and os.path.isdir(c):
                d = os.path.join(c, "mitsumori_check_tmp"); os.makedirs(d, exist_ok=True); return d
        except Exception:
            continue
    return t


_LAST_IMG = {}


class PageImg:
    """ページの画像を、圧縮したまま持つ(1ページ数MB→数百KB。メモリ節約)。使うときだけ開く"""
    def __init__(self, im=None, png=None, size=None):
        if png is None:
            b = io.BytesIO(); im.convert("RGB").save(b, "PNG", compress_level=3); png = b.getvalue(); size = im.size
        self._b, (self.width, self.height) = png, size

    def get(self):
        k = id(self)
        if _LAST_IMG.get("k") != k:
            _LAST_IMG.clear(); _LAST_IMG["k"] = k
            _LAST_IMG["im"] = Image.open(io.BytesIO(self._b)).convert("RGB")
        return _LAST_IMG["im"]

    def crop(self, box): return self.get().crop(box)
    def resize(self, *a, **k): return self.get().resize(*a, **k)
    def convert(self, mode): return self.get().convert(mode)


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
VARIANT = {"单": "単", "价": "価", "俩": "価", "额": "額", "额": "額", "数": "数", "号": "号", "纳": "納", "规": "規", "称": "称", "名": "名"}
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
        t = "".join(VARIANT.get(ch, ch) for ch in norm(l["text"]))
        if "数量" in t and (_has_any(t, HDR["model"]) or _has_any(t, HDR["name"])):
            return i
    return None


def header_cols(line):
    """見出し行のかたまりを列にする。見出しの文字が読み違えられていても列の位置は使い、
    足りない見出しの数と読めない見出しの数が合うときだけ、標準の並び(型式・数量・単価・金額)で推定する"""
    tks = tokens_of(line)
    found, unk = {}, []
    for t, a, b in tks:
        nt = "".join(VARIANT.get(ch, ch) for ch in norm(t))   # OCRが中国語の字体で読んだ見出し(单位・金额…)を日本語の字体にそろえる
        exact = [k for k in HDR if k not in found and _has_any(nt, HDR[k])]
        part = [k for k in HDR if k not in found and 1 <= len(nt) <= 2 and any(w.startswith(nt) and len(nt) < len(w) for w in HDR[k])]
        # ちょうど当てはまるものがあればそれ。なければ、一部だけ読めた見出しが、ただ1つの見出しに当てはまるときだけ。あいまいなら決めない
        lab = exact[0] if exact else (part[0] if len(part) == 1 else None)
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
    """単価・金額の列が1つも読めない/合計が合わない書類は、全行を『確実』にしない。
    品名・単位・納期は、他の行に書いてあるのに、この行だけ空なら、『目で確認』の注意を付ける(soft)"""
    for r in rows: r.setdefault("soft", [])
    for k in ("name", "unit", "due"):
        if any(r.get(k) for r in rows):
            for r in rows:
                if not r.get(k) and r.get(k) is not None:
                    r["soft"].append(f"{JP[k]}が読めていない(他の行には書いてある)")
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
        self.lines = []      # 書類の全部の行 [{page,text,x0,y0,x1,y1,src}] (見積⇔見積で、型番らしい文字列を拾う元)
        self.lines2 = []     # 画像の2つ目の読み取り(Tesseract)の行
        self.rot = None      # スキャンの向きの補正(度)


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
    with tempfile.TemporaryDirectory(dir=safe_tmp_dir()) as d:
        p = os.path.join(d, "a.png"); big.save(p)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        try:
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # Windows: 黒い窓を出さない
            cp = subprocess.run([TESS_EXE, p, "stdout", "-l", "jpn+eng", "--psm", "6", "tsv"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=25, **kw)
            out = cp.stdout
            if not (out or "").strip():
                log(f"Tesseractの出力が空(終了コード{cp.returncode}): {(cp.stderr or '')[:300]} / exe={TESS_EXE} / tessdata={os.environ.get('TESSDATA_PREFIX')}")
        except subprocess.TimeoutExpired:
            log("Tesseractが時間切れ"); return []   # 時間切れ → 読み取りなし(=要確認になる。黙って通さない)
        except Exception as e:
            log(f"Tesseractが動かない: {type(e).__name__}: {e}"); return []
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
SOFT_KEYS = ("name", "unit", "due")


def merge_readers(readers):
    """複数の読み取り結果(各 rows のリスト)を行ごとに突き合わせる。readers[0] を基準にする。
    確実(exact)にする条件(松田決裁 2026-11-25):
      ①基準の読み取りに、数字の問題が無い(数量×単価=金額が合う等)
      ②他の読み取りのうち少なくとも1つが、型式と数量で一致し、単価・金額が食い違わない
      ※ 検算(数量×単価=金額)ができない行は、単価・金額も一致が必要
      ※ 品名・単位・納期は、読み取りが食い違っても確実さを下げない。代わりに『目で確認』の注意(soft)を付ける
    """
    base = readers[0]; out = []
    used = [set() for _ in readers]
    for a in base:
        a.setdefault("soft", [])
        ya = (a["y0"] + a["y1"]) / 2
        validated = a["qty"] is not None and a["price"] is not None and a["amount"] is not None   # 検算できた行(問題が無ければ、数字は一貫している)
        agree, conflict, others = False, [], []
        for ri in range(1, len(readers)):
            best = None
            for j, b in enumerate(readers[ri]):
                if j in used[ri]: continue
                yb = (b["y0"] + b["y1"]) / 2
                if abs(ya - yb) < 0.7 * max(a["y1"] - a["y0"], 8) and (best is None or abs(ya - yb) < best[0]):
                    best = (abs(ya - yb), j)
            if best is None:
                others.append(f"読み取り{ri+1}では、この行が見つからない"); continue
            used[ri].add(best[1]); b = readers[ri][best[1]]
            if a["model"] == b["model"] and a["qty"] == b["qty"]:
                numconf = [k for k in ("price", "amount") if a[k] is not None and b[k] is not None and a[k] != b[k]]
                need_full = not validated
                full_diff = [k for k in ("price", "amount") if a[k] != b[k]] if need_full else []
                if numconf: conflict.append(f"読み取り{ri+1}と{'・'.join(JP[k] for k in numconf)}が食い違う")
                elif full_diff: conflict.append(f"検算できない行で、読み取り{ri+1}と{'・'.join(JP[k] for k in full_diff)}が一致しない")
                else: agree = True
            else:
                diff = [JP[k] for k in ("model", "qty") if a[k] != b[k]]
                others.append(f"読み取り{ri+1}と食い違う({'・'.join(diff)}): 1={a['model_raw']}/{a['qty']}  {ri+1}={b['model_raw']}/{b['qty']}")
            sd = [JP[k] for k in SOFT_KEYS if a.get(k) != b.get(k)]
            if sd: a["soft"].append(f"読み取り{ri+1}と{'・'.join(sd)}が食い違う")
        unsure = list(conflict)
        if not agree: unsure += others or ["他の読み取りと一致しない"]
        a["exact"] = agree and not conflict and not a["problems"]
        a["problems"] = a["problems"] + unsure; a["src"] = "ocr"
        out.append(a)
    for ri in range(1, len(readers)):
        for j, b in enumerate(readers[ri]):
            if j not in used[ri]:
                b.setdefault("soft", []); b["exact"] = False; b["src"] = "ocr"; b["problems"].append("基準の読み取りでは、この行が見つからない"); out.append(b)
    return out


def read_doc(path, role, cancel=None):
    t0 = time.time()
    d = Doc(path, role)
    cancel = cancel or (lambda: False)
    ext = os.path.splitext(path)[1].lower()
    # メモリが足りないときは、少し待つ。それでも足りなければ、落ちずに「読めなかった」と出す
    for _ in range(30):
        a = avail_mb()
        if a is None or a >= 500: break
        time.sleep(2)
    else:
        d.notes.append("パソコンのメモリが足りず、読めなかった。他のアプリを閉じてから、もう一度やり直してください")
        log(f"メモリ不足で読めず: {d.name} (空き {avail_mb()}MB)")
        return d
    log(f"読み始め: {d.name} 空き{avail_mb()}MB")
    try:
        if ext in (".xlsx", ".xlsm"):
            read_excel(d)
        elif ext == ".pdf":
            pdf = fitz.open(path)
            npages = len(pdf)
            for pno, page in enumerate(pdf):
                if cancel(): raise Cancelled()
                if pno >= MAX_PAGES:
                    d.notes.append(f"ページが多いので、先頭{MAX_PAGES}ページだけ読みました(残り{npages - MAX_PAGES}ページは読んでいません。目視で確認してください)")
                    break
                try:
                    scanned = is_scanned_page(page)
                    tsegs = text_segs_of_page(page)
                    if not scanned and tsegs:
                        pm = page.get_pixmap(dpi=110)
                        d.pages.append((PageImg(png=pm.tobytes("png"), size=(pm.width, pm.height)), page_scale_dpi(110)))
                        d.kind = "文字入りPDF"
                        lines = group_lines(tsegs); d.text += " ".join(l["text"] for l in lines) + "\n"
                        d.lines += [{"page": pno, "text": l["text"], "x0": l["x0"], "y0": l["y0"], "x1": l["x1"], "y1": l["y1"], "src": "text", "toks": tokens_of(l)} for l in lines]
                        rows, total, err = parse_rows(lines)
                        for r in rows: r["exact"] = not r["problems"]; r["page"] = pno; r["src"] = "text"
                        d.rows += rows; d.total = total if total is not None else d.total
                        if err: d.notes.append(f"{pno+1}ページ: {err}")
                    else:
                        pm = page.get_pixmap(dpi=200)
                        im = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGB")
                        del pm
                        if getattr(d, "rot", None) is None:   # 向きは、最初のスキャンページで決めて、全ページに使う
                            d.rot, sc_ = best_rotation(im)
                            if d.rot: d.notes.append(f"画像が、{d.rot}度、傾いていたので、向きを直して読みました"); log(f"向きを補正: {d.name} {d.rot}度 {sc_}")
                        if d.rot: im = im.rotate(d.rot, expand=True)
                        d.kind = "スキャンPDF" if scanned else "画像PDF"
                        d.src = "ocr"
                        extra = None
                        if tsegs and not d.rot:  # スキャンに付いた文字層は、信用せず、3つ目の読み取りとして使う(食い違えば要確認)
                            sc = 200 / 72
                            ts = [Seg([(c[0], c[1] * sc, c[2] * sc) for c in s_.chars], s_.y0 * sc, s_.y1 * sc) for s_ in tsegs]
                            extra = parse_rows(group_lines(ts))[0]
                            for r in extra: r["exact"] = True; r["problems"] = []
                        scan_ocr(d, im, pno, extra)
                        d.pages.append((PageImg(im), 1.0))   # 読み終えたページは、圧縮して持つ(メモリ節約)
                        del im
                except Exception as e:   # 1ページの失敗で、書類全体を捨てない。そのページは、要確認に回す
                    d.notes.append(f"{pno+1}ページ: 読み込みに失敗: {type(e).__name__}: {e}")
                    log(f"ページ失敗: {d.name} p{pno+1} {type(e).__name__}: {e}")
                if pno % 5 == 4: gc.collect()
        elif ext in IMG_EXTS:
            from PIL import ImageOps
            im = Image.open(path)
            try: im = ImageOps.exif_transpose(im)   # スマホ写真の向きを直す
            except Exception: pass
            im = im.convert("RGB")
            if max(im.size) > MAX_SIDE:   # 大きすぎる写真は縮める(メモリと時間の節約)
                r = MAX_SIDE / max(im.size); im = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
                d.notes.append("画像が大きいので、縮めて読みました(細かい文字は、目視で確認してください)")
            d.rot, sc_ = best_rotation(im)
            if d.rot:
                im = im.rotate(d.rot, expand=True); d.notes.append(f"画像が、{d.rot}度、傾いていたので、向きを直して読みました"); log(f"向きを補正: {d.name} {d.rot}度 {sc_}")
            d.kind = "画像"; d.src = "ocr"
            scan_ocr(d, im, 0, None)
            d.pages = [(PageImg(im), 1.0)]
        else:
            d.notes.append("対応していない形式")
    except Cancelled:
        raise
    except Exception as e:
        d.notes.append(f"読み込みに失敗: {e}")
        log(f"読み込み失敗: {d.name} {type(e).__name__}: {e}")
    d.seconds = time.time() - t0
    gc.collect()
    log(f"読み終わり: {d.name} {d.seconds:.0f}秒 行{len(d.rows)} 空き{avail_mb()}MB")
    return d


def scan_ocr(d, im, pno, extra):
    sa, sb = ocr_rapid(im), ocr_tess(im)
    la, lb = group_lines(sa), group_lines(sb)
    d.lines += [{"page": pno, "text": l["text"], "x0": l["x0"], "y0": l["y0"], "x1": l["x1"], "y1": l["y1"], "src": "ocr", "toks": tokens_of(l)} for l in la]
    d.lines2 += [{"page": pno, "text": l["text"], "x0": l["x0"], "y0": l["y0"], "x1": l["x1"], "y1": l["y1"], "src": "ocr", "toks": tokens_of(l)} for l in lb]
    if sa and not sb:
        d.notes.append(f"{pno+1}ページ: 2つ目の読み取り(Tesseract)が動かなかった。確実とは言えないので、全部、人が確認してください")
    d.text += " ".join(l["text"] for l in la) + "\n"
    ra, ta, ea = parse_rows(la); rb, tb, eb = parse_rows(lb)
    if ea:   # 基準の読み取りが見出しを読めないときだけ、書類全体の注意にする(2つ目だけ読めない場合は、行の一致で扱う)
        d.notes.append(f"{pno+1}ページ: 見出しを読めない({ea})")
    readers = [ra, rb] + ([extra] if extra else [])
    rows = merge_readers([x for x in readers if x] or [ra])
    # 合計: 基準の読み取り(RapidOCR)の合計が、明細の金額の合計と合えば、それ自体が強い検算。2つ目が読めなくても問題にしない。
    # 合わなければ、読み落としか読み間違いなので、全行を確実にしない。基準が合計を読めなかったときは、検算できないと注意する。
    tot = ta
    for r in rows: r["page"] = pno; r["src"] = "ocr"
    if ta is None:
        d.notes.append(f"{pno+1}ページ: 合計を読めなかった(合計の検算ができない)")
    elif ra and all(r["amount"] is not None for r in ra) and sum(r["amount"] for r in ra) != ta:
        for r in rows:
            r["exact"] = False; r["problems"].append(f"明細の金額の合計{sum(x['amount'] for x in ra):,}と書類の合計{ta:,}が合わない(読み落としの可能性)")
    d.rows += rows
    d.total = tot


def read_excel(d):
    d.kind = "Excel"; d.src = "excel"
    wb = openpyxl.load_workbook(d.path, data_only=True)
    ws = wb.active
    grid = []
    for k, row in enumerate(ws.iter_rows(values_only=True)):
        if k >= 5000:
            d.notes.append("行が多すぎるので、先頭5,000行だけ読みました(残りは読んでいません。目視で確認してください)"); break
        grid.append([("" if c is None else str(c)) for c in row])
    d.text = "\n".join(" ".join(r) for r in grid)
    d.lines = [{"page": 0, "text": " ".join(r), "src": "excel"} for r in grid if any(r)]
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
            best = f"{cand[0][0]:.0%}" if cand and cand[0][0] < 2.0 else "0%"
            pairs.append((None, o, f"相手が決まらない。見積番号が一致せず、型式の重なりが最大でも {best}(組にする基準は、50%以上で、2番目より20ポイント以上高いこと)"))
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
            diffs, softdiffs = [], []
            for k in ("qty", "price", "amount", "name", "unit", "due"):
                if k != "qty" and k not in both: continue
                va, vb = a.get(k), b.get(k)
                if va != vb:
                    fm = (lambda v: f"{v:,}" if isinstance(v, (int, float)) and k in ("qty", "price", "amount") else str(v))
                    (softdiffs if k in SOFT_KEYS else diffs).append(f"{JP[k]}: 見積「{fm(va)}」⇔ 発注「{fm(vb)}」")
            probs = a["problems"] + b["problems"]
            sure = a["exact"] and b["exact"]
            ocr = a.get("src") == "ocr" or b.get("src") == "ocr"
            notes = [n for n in a.get("soft", []) + b.get("soft", [])]
            if softdiffs and ocr:   # 画像から読んだ品名・単位・納期の違いは、読み取りの誤りかもしれない → 要確認
                res.append({"status": "REVIEW", "model": a["model_raw"], "msg": "違いに見えるが、画像の読み取りが確実でない: " + " / ".join(diffs + softdiffs), "qrow": a, "orow": b})
                continue
            diffs = diffs + softdiffs
            if diffs and sure:
                res.append({"status": "DIFF", "model": a["model_raw"], "msg": " / ".join(diffs), "qrow": a, "orow": b})
            elif diffs:
                res.append({"status": "REVIEW", "model": a["model_raw"], "msg": "違いに見えるが、読み取りが確実でない: " + " / ".join(diffs), "qrow": a, "orow": b})
            elif probs or not sure:
                res.append({"status": "REVIEW", "model": a["model_raw"], "msg": "読み取りに自信がない: " + " / ".join(dict.fromkeys(probs or ["読み取りが確実でない"])), "qrow": a, "orow": b})
            else:
                msg = ("品名・単位・納期は、読み取りに自信がないので、画像を見て確認してください(" + " / ".join(dict.fromkeys(notes)) + ")") if (ocr and notes) else ""
                res.append({"status": "OK_IMG" if ocr else "OK", "model": a["model_raw"], "msg": msg, "qrow": a, "orow": b})
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


# ------------------------------------------------------------------ 型番らしい文字列を、書類全体から拾う(見積 ⇔ 見積用)
_UNIT_RE = re.compile(r"^\d+(MM|CM|KG|ML|HZ|PCS|MPA|KPA|PA|RPM|[MGLVAW])$")


def model_tokens(text):
    """1行の文字から、型番らしい文字列を拾う。英字と数字が混ざり、4文字以上。例: B9565AW-T / AW20-02BE-D / KQ2H06-M5。
    電話・FAX・インボイス番号・単位つきの数字(100V, 10KG など)は、型番ではないので、拾わない"""
    t = unicodedata.normalize("NFKC", text or "")
    for d_ in DASH: t = t.replace(d_, "-")
    out = []
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9\-\./]*", t):
        tok = m.group(0).strip("-./"); u = tok.upper(); k = re.sub(r"[^A-Z0-9]", "", u)
        if len(k) < 4 or not re.search(r"[A-Z]", k) or not re.search(r"\d", k): continue
        if u.startswith(("TEL", "FAX", "NO.", "WWW.", "HTTP")) or "@" in u: continue
        if re.search(r"\d/\d", u): continue   # 3/8 などの、インチの寸法(型番ではなく、品名の一部)
        if re.fullmatch(r"T\d{13}", k) or re.fullmatch(r"T?\d{7}", k) or _UNIT_RE.match(k): continue   # インボイス登録番号・郵便番号(〒がTに化ける)・単位つきの数字
        out.append(tok)
    return out


_QTY_WORDS = ("数量", "数", "単位", "単価", "金額")
_END_WORDS = ("以下余白", "合計", "小計", "頁計", "備考", "消費税")


def item_region(lines):
    """表の『型式・品名の列』だけを切り出す。戻り: (行のリスト, 説明, 見出しを見つけたか)
    ①ページごとに、見出し行(数量+品名/型式など)を探す ②見出しの『数量・単位・単価・金額』の列より左だけを、型式・品名の列として使う
    ③見出しの下から、合計・備考・以下余白の行の手前までを、明細として読む(宛先・日付・備考・住所は読まない)。
    見出しが見つからないページは、そのページ全体を使い、その旨を説明に残す"""
    out, notes, found_any, missing = [], [], False, []
    pages = sorted({L.get("page", 0) for L in lines})
    for pg in pages:
        pl = [L for L in lines if L.get("page", 0) == pg]
        hi = None
        for i, L in enumerate(pl):
            t = "".join(VARIANT.get(ch, ch) for ch in norm(L["text"]))
            if ("数量" in t or ("数" in t and "量" in t)) and _has_any(t, HDR["model"] + HDR["name"] + ("仕様", "仕樣", "摘要")):
                hi = i; break
        if hi is None or not pl[hi].get("toks"):
            out += pl; missing.append(pg + 1); continue
        found_any = True
        qx = None
        for t, a, b in pl[hi]["toks"]:
            nt = "".join(VARIANT.get(ch, ch) for ch in norm(t))
            if any(w in nt for w in _QTY_WORDS) or nt in ("数", "量"):
                qx = a if qx is None else min(qx, a)
        end = len(pl)
        for j in range(hi + 1, len(pl)):
            tk = pl[j].get("toks") or []
            lead = [t for t, a, b in tk if qx is None or (a + b) / 2 < qx] or [t for t, _, _ in tk[:1]]   # 左側の文字(なければ、行の先頭)だけで、終わりの印を探す(右の列の備考に、惑わされない)
            nt = "".join(VARIANT.get(ch, ch) for ch in norm(" ".join(lead) if tk else pl[j]["text"]))
            if _has_any(nt, _END_WORDS) and not _has_any(nt, HDR["model"] + HDR["name"]): end = j; break   # 2段目の見出し(型式 備考)は、終わりの印ではない
        for L in pl[hi + 1:end]:
            if qx is None or not L.get("toks"):
                out.append(L); continue
            keep = [(t, a, b) for t, a, b in L["toks"] if (a + b) / 2 < qx]
            if not keep: continue
            L2 = dict(L); L2["text"] = " ".join(t for t, _, _ in keep); L2["x1"] = min(L["x1"], max(b for _, _, b in keep)); out.append(L2)
    if found_any: notes.append("表の見出しを見つけたので、数量・単価・金額より左(型式・品名の列)だけを読んだ")
    if missing: notes.append(f"見出しが見つからなかったページ({','.join(map(str, missing))})は、そのページ全体から拾った(精度が下がる)")
    return out, " / ".join(notes), found_any


def tkey(tok):
    return re.sub(r"[^A-Z0-9]", "", tok.upper())


def doc_tokens(d):
    """{照合用の鍵: {raw, line, sure}}。
    文字入りPDF・Excel: 見つかった型番は、全部「確実」。
    画像: 2種類の読み取り(RapidOCR と Tesseract)の、どちらかで見つかった型番を候補にする(片方が読み落としても、拾えるように)。
          両方で見つかった型番だけを「確実」とし、片方でしか見つからないものは、「確実でない」(必ず人が見る)とする"""
    def collect(lines):
        out = {}
        if d.src != "excel": lines, _, _ = item_region(lines)
        for L in lines:
            for tok in model_tokens(L["text"]):
                k = tkey(tok)
                if k not in out: out[k] = {"raw": tok, "line": L}
        return out
    m1 = collect(d.lines)
    if d.src != "ocr":
        return {k: {"raw": v["raw"], "line": v["line"], "sure": True, "noise": False} for k, v in m1.items()}
    m2 = collect(d.lines2)
    res = {}
    def glued(long_k, short_k):   # 「XYZ-100A 5本入」を「XYZ-100A5本入」と、くっつけて読んだ型(末尾に、数字が1〜2桁、余分についた)
        return len(short_k) >= 5 and long_k.startswith(short_k) and 1 <= len(long_k) - len(short_k) <= 2 and long_k[len(short_k):].isdigit()
    for k in set(m1) | set(m2):
        src = m1.get(k) or m2.get(k)
        sure = (k in m1 and k in m2)
        raw = src["raw"]
        # 画像の読み取りが、片方でしか見つからず、ハイフンがない・小文字が混ざる・長すぎる → 文字の読み間違い(ゴミ)の可能性が高い
        noise = (not sure) and (("-" not in raw) or any(c.islower() for c in raw) or len(k) > 20)
        res[k] = {"raw": raw, "line": src["line"], "sure": sure, "noise": noise}
    for k in list(res):
        for k2 in list(res):
            if k != k2 and k in res and k2 in res and glued(k2, k):
                other = (k2 in m1 and k in m2 and k2 not in m2 and k not in m1) or (k2 in m2 and k in m1 and k2 not in m1 and k not in m2)
                if other:   # 別々の読み取りが、同じ型番(の、くっつき違い)を読んでいる → 短い方に、まとめて、確実とする
                    res[k]["sure"] = True; res[k]["noise"] = False; del res[k2]
    return res


def _tok_row(d, k, v):
    L = v["line"]
    r = {"model_raw": v["raw"], "model": k, "raw": L["text"], "name": L["text"], "page": L["page"], "src": L["src"], "exact": v["sure"], "problems": []}
    if "x0" in L: r.update(x0=L["x0"], y0=L["y0"], x1=L["x1"], y1=L["y1"])
    return r


def compare_qq(a, b, with_qty=False):
    """見積同士を比べる(a=客先に出した見積 / b=仕入先の見積)。
    表の見出しから「型式・品名の列(数量・単価・金額より左)」だけを切り出し(見出しが無いページは、そのページ全体)、その中から型番らしい文字列を拾って、比べる。
    品名・数量・単価・金額・合計・納期・単位は、書類ごとに違って当たり前なので、比べない。
    「確実」と言い切れる型番だけ OK を出す。自信がない・近い型番・片方にしかない型番は、必ず人に見せる(黙って通さない)。"""
    res = []
    ta, tb = doc_tokens(a), doc_tokens(b)
    ign = {"q": [], "o": []}
    def _similar(k, other): return k in other or any(lev(k, kk) <= 2 for kk in other)
    for side, tk, ot in (("q", ta, tb), ("o", tb, ta)):   # ゴミの疑いが強く、相手の書類に似たものもない文字列は、1行ずつは出さない
        for k in list(tk):
            if tk[k].get("noise") and not _similar(k, ot):
                ign[side].append(tk[k]["raw"]); del tk[k]
    for k in sorted(set(ta) | set(tb)):
        x, y = ta.get(k), tb.get(k)
        if x and y:
            ocr = a.src == "ocr" or b.src == "ocr"
            ra, rb = _tok_row(a, k, x), _tok_row(b, k, y)
            if x["sure"] and y["sure"]:
                res.append({"status": "OK_IMG" if ocr else "OK", "model": x["raw"], "msg": "", "qrow": ra, "orow": rb})
            else:
                res.append({"status": "REVIEW", "model": x["raw"], "msg": "型番は一致して見えるが、画像の読み取りが確実でない(2種類の読み取りの片方でしか、見つからなかった)", "qrow": ra, "orow": rb})
        else:
            one_doc, one_tok, other = (a, x, tb) if x else (b, y, ta)
            where = "客先の見積にだけある" if x else "仕入先の見積にだけある"
            near = [kk for kk in other if lev(k, kk) <= 2]
            row = _tok_row(one_doc, k, one_tok)
            qr, orr = (row, None) if x else (None, row)
            if near:
                gl = [kk for kk in near if (k.startswith(kk) and k[len(kk):].isdigit()) or (kk.startswith(k) and kk[len(k):].isdigit())]
                hint = "(型番の後ろに、数量や規格の数字が、くっついて読めた可能性があります。例: 「B9565AW-T 10冊入」→「B9565AW-T10」。画像を見て、確認してください)" if gl else "(1〜2文字の違い。見間違いか、本当の違いか、確認してください)"
                res.append({"status": "REVIEW", "model": one_tok["raw"], "msg": f"{where}型番。近い型番: {', '.join(other[kk]['raw'] for kk in near)}{hint}", "qrow": qr, "orow": orr})
            else:
                st = "DIFF" if one_tok["sure"] else "REVIEW"
                res.append({"status": st, "model": one_tok["raw"], "msg": f"{where}型番" + ("" if st == "DIFF" else "(画像の読み取りが確実でないので、断定しない)"), "qrow": qr, "orow": orr})
    for d, tk, nm, sd in ((a, ta, "客先見積", "q"), (b, tb, "仕入先見積", "o")):
        if ign[sd]:
            ex = "、".join(ign[sd][:4]) + ("…" if len(ign[sd]) > 4 else "")
            res.append({"status": "REVIEW", "model": "(書類全体)", "msg": f"{nm}: 画像から、型番かどうか判断できない文字列を、{len(ign[sd])}個、読み取りましたが、無視しています(例: {ex})。画像の汚れによる読み間違いの可能性が高いものです。型番が、ほかにないか、画像を見て確認してください(「読み取った中身を見る」に、全部あります)", "qrow": None, "orow": None, "doc": sd})
        notes = [n for n in d.notes if "合計を読めなかった" not in n and "見出しを読めない" not in n and "見出し行が見つからない" not in n]   # 型番を拾うのに、表の見出しは、要らない
        if not tk:
            res.append({"status": "REVIEW", "model": "(書類全体)", "msg": f"{nm}書から、型番らしい文字列を、1つも見つけられなかった(英字と数字が混ざった4文字以上の文字列を探しています)。" + (" / ".join(notes) if notes else ""), "qrow": None, "orow": None, "doc": "q" if nm == "客先見積" else "o"})
        elif notes:
            res.append({"status": "REVIEW", "model": "(書類全体)", "msg": f"{nm}: " + " / ".join(notes), "qrow": None, "orow": None, "doc": "q" if nm == "客先見積" else "o"})
    return res, ["型番(英字と数字の文字列)"], []


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
IMG_EXTS = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".tif", ".tiff", ".bmp")
EXTS = (".pdf", ".xlsx", ".xlsm") + IMG_EXTS


def _read_job(args, cancel=None):
    return read_doc(args[0], args[1], cancel)


def read_many(jobs, workers=4, progress=None, cancel=None):
    """書類を並列に読む(画像のOCRが遅いので)。progress(done, total) があれば進み具合を知らせる"""
    out = [None] * len(jobs)
    if workers <= 1 or len(jobs) <= 1:
        for i, j in enumerate(jobs):
            if cancel and cancel(): raise Cancelled()
            out[i] = _read_job(j, cancel)
            if progress: progress(i + 1, len(jobs))
        return out
    import concurrent.futures as cf
    done = 0
    ex = cf.ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {ex.submit(_read_job, j, cancel): i for i, j in enumerate(jobs)}
        for f in cf.as_completed(futs):
            if cancel and cancel(): raise Cancelled()
            out[futs[f]] = f.result(); done += 1
            if progress: progress(done, len(jobs))
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)   # 中止・失敗のとき、まだ始めていない書類は、読まない
        raise
    ex.shutdown(wait=True)
    return out


ROLES_BY_MODE = {"qo": ("見積", "発注"), "qq": ("客先見積", "仕入先見積")}   # qo=見積⇔発注 / qq=見積⇔見積(客先と仕入先)
SKIPNOTE_QQ = "品名・数量・単価・金額・合計・納期・単位(書類ごとに違って当たり前なので、比べません)"


def doc_info(d, mode="qo"):
    """画面に出す『ツールが、この書類から、何を読んだか』(判断の基準が、見えるように)"""
    if d is None: return None
    if mode == "qq":
        tk = doc_tokens(d)
        return {"name": d.name, "kind": d.kind, "notes": d.notes[:6], "mode": "qq", "nlines": len(d.lines), "region": item_region(d.lines)[1] if d.src != "excel" else "Excelは全セルから拾った",
                "rows": [{"m": v["raw"], "n": v["line"]["text"][:90], "q": None, "sure": v["sure"], "noise": bool(v.get("noise"))} for v in list(tk.values())[:300]]}
    return {"name": d.name, "kind": d.kind, "notes": d.notes[:6], "mode": "qo",
            "rows": [{"m": r.get("model_raw"), "n": r.get("name"), "q": r.get("qty")} for r in d.rows[:300]]}


def pair_docs_qq(A, B):
    """見積⇔見積: 型番の重なりで、組を決める(客先の見積は、仕入先の見積より、品目が多いことがあるので、基準を、やや低くする)"""
    pairs, used = [], set()
    for b in B:
        kb = list(doc_tokens(b))
        cand = sorted(((jaccard(list(doc_tokens(a)), kb), i) for i, a in enumerate(A) if i not in used), reverse=True)
        if cand and cand[0][0] >= 0.3 and (len(cand) == 1 or cand[0][0] - cand[1][0] >= 0.15):
            used.add(cand[0][1]); pairs.append((A[cand[0][1]], b, f"型番の重なり {cand[0][0]:.0%}"))
        else:
            best = f"{cand[0][0]:.0%}" if cand else "0%"
            pairs.append((None, b, f"相手が決まらない。型番の重なりが最大でも {best}(組にする基準は、30%以上で、2番目より15ポイント以上高いこと)"))
    for i, a in enumerate(A):
        if i not in used: pairs.append((a, None, "対応する仕入先の見積がない"))
    return pairs


def run_vendor(vdir, workers=4, progress=None, name=None, mode="qo", with_qty=False, cancel=None):
    roles = ROLES_BY_MODE[mode]
    out = {"vendor": name or os.path.basename(vdir), "pairs": [], "warnings": []}
    jobs = []
    for role in roles:
        rd = os.path.join(vdir, role)
        if not os.path.isdir(rd): continue
        for fn in sorted(os.listdir(rd)):
            if fn.lower().endswith(EXTS):
                jobs.append((os.path.join(rd, fn), role))
    docs = {roles[0]: [], roles[1]: []}
    for d in read_many(jobs, workers, progress, cancel):
        k = kind_of(d.text)
        if mode == "qo":
            if k and k != d.role:
                out["warnings"].append(f"{d.role}フォルダの「{d.name}」は、中身が「{k}」と読める。入れる場所を間違えていないか確認してください" + ("(見積同士を比べるときは、上の「見積 ⇔ 見積」のタブを使います)" if k == "見積" and d.role == "発注" else ""))
        elif k == "発注":
            out["warnings"].append(f"{d.role}フォルダの「{d.name}」は、中身が「発注書」と読める。見積書を入れる場所です。間違えていないか確認してください")
        docs[d.role].append(d)
    out["seconds"] = sum(d.seconds for r in docs.values() for d in r)
    A, B = docs[roles[0]], docs[roles[1]]
    if mode == "qq" and len(A) == 1 and len(B) == 1:
        pairs = [(A[0], B[0], "1対1で、そのまま組にした")]   # 見積番号が違うのが普通なので、1枚ずつなら、迷わず組にする
    else:
        pairs = pair_docs_qq(A, B) if mode == "qq" else pair_docs(A, B)
    for q, o, why in pairs:
        item = {"q": q, "o": o, "why": why, "results": [], "compared": [], "skipped": []}
        item["mode"] = mode
        if mode == "qq": item["skipnote"] = SKIPNOTE_QQ
        if q and o:
            if mode == "qq": item["results"], item["compared"], item["skipped"] = compare_qq(q, o, with_qty)
            else: item["results"], item["compared"], item["skipped"] = compare(q, o)
        out["pairs"].append(item)
    return out


def run_all(root, workers=4):
    vendors = [os.path.join(root, d) for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]
    return [run_vendor(v, workers) for v in vendors]


def health():
    """読み取りの部品が、このパソコンで、動くか(起動時に1回。画面に、動かないものを、出す)"""
    out = {"rapid": False, "tess": False, "msg": ""}
    try:
        from PIL import ImageDraw
        im = Image.new("RGB", (700, 160), "white"); dr = ImageDraw.Draw(im)
        for k in range(3): dr.text((20, 20 + 40 * k), "AW20-02BE-D  10  3,400  34,000", fill="black")
        try: out["rapid"] = len(ocr_rapid(im)) >= 1
        except Exception as e: out["msg"] += f"RapidOCR: {type(e).__name__}: {e}  "
        try: out["tess"] = len(ocr_tess(im)) >= 1
        except Exception as e: out["msg"] += f"Tesseract: {type(e).__name__}: {e}  "
    except Exception as e:
        out["msg"] += f"自己診断の失敗: {type(e).__name__}: {e}"
    out["ok"] = out["rapid"] and out["tess"]
    return out
