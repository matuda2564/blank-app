# -*- coding: utf-8 -*-
"""ストレステスト: 起こりそうな間違いを、わざと入れた見積・発注書を大量に作り、見逃し(違いがあるのにOK)を数える。
使い方: python stress.py <作業フォルダ> [速さだけ=quick]
"""
import os, sys, io, re, json, random, shutil, time, zlib, unicodedata, concurrent.futures as cf, multiprocessing as mp
import fitz, openpyxl
from PIL import Image, ImageFilter
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import mc_core as C

POOL_MODELS = ["SY5120-5LZD-01", "AW20-02BE-D", "KQ2H06-M5", "VM130-02-34GA", "CDQ2B32-50DZ", "TJ-800", "TJ-350", "MR-J4-10A", "FR-D720-0.75K", "LRD-15", "XT-2200", "GP-30B"]
POOL_NAMES = ["ボルト", "ベアリング", "ソレノイドバルブ", "フィルタ", "ポンプ", "ホース", "シリンダ", "インバータ", "サーボアンプ", "ケーブル", "電磁弁", "圧力計", "継手", "六角ナット", "減圧弁", "配管用チューブ"]
UNITS_ = ["個", "本", "台", "セット", "箱"]
KATA = {"ボ": "ポ", "ポ": "ボ", "ベ": "ペ", "ペ": "ベ", "シ": "ツ", "ツ": "シ", "ソ": "ン", "ン": "ソ", "ブ": "プ", "プ": "ブ", "バ": "パ", "パ": "バ", "フ": "ブ", "ホ": "ボ"}
KANJI = {"電磁弁": "電動弁", "圧力計": "圧力針", "継手": "継首", "六角ナット": "六角ナツト", "減圧弁": "減圧升", "配管用チューブ": "配管用チユーブ"}
CONF = {"3": "8", "8": "3", "1": "7", "7": "1", "5": "6", "6": "5", "0": "6", "9": "4", "4": "9", "2": "7"}

KINDS_OK = ["none", "fullwidth", "space", "rowswap"]  # 違いなし(OKになるべき)
KINDS_BAD = ["qty_digit", "qty_noamt", "qty_x10", "price_digit", "price_x10", "model_1char", "model_O0", "model_I1", "model_hyphen", "model_swap",
             "name_kata", "name_kanji", "unit", "due", "row_del", "row_add", "row_dup", "total_wrong"]
KINDS = KINDS_OK + KINDS_BAD

LAYOUTS = {
    "L4": dict(cols=[("型式", 60), ("数量", 300), ("単価", 370), ("金額", 460)], fs=11, dy=26),
    "L7": dict(cols=[("品名", 45), ("型式", 150), ("数量", 290), ("単位", 325), ("単価", 365), ("金額", 430), ("納期", 495)], fs=10, dy=24),
}


def alnum(s):
    return re.sub(r"[^A-Z0-9]", "", C.norm(s))


def mk_rows(rng, kind):
    n = rng.randint(4, 6)
    models = rng.sample(POOL_MODELS, n)
    rows = []
    for m in models:
        rows.append(dict(model=m, name=rng.choice(POOL_NAMES), qty=rng.randint(1, 60), price=rng.choice([180, 250, 1200, 3400, 5600, 9800, 12500, 48000, 98000]),
                         unit=rng.choice(UNITS_), due=f"12/{rng.randint(10, 28)}"))
    # その種類の間違いを入れられるよう、1行目を整える
    if kind in ("model_O0", ) and "0" not in rows[0]["model"]: rows[0]["model"] = "KQ2H06-M5"
    if kind == "model_I1" and "1" not in rows[0]["model"]: rows[0]["model"] = "SY5120-5LZD-01"
    if kind == "model_hyphen" and "-" not in rows[0]["model"]: rows[0]["model"] = "AW20-02BE-D"
    if kind == "model_swap" and not re.search(r"(\d)(?!\1)(\d)", rows[0]["model"]): rows[0]["model"] = "SY5120-5LZD-01"
    if kind == "name_kanji": rows[0]["name"] = rng.choice(list(KANJI))
    if kind == "name_kata" and not any(c in KATA for c in rows[0]["name"]): rows[0]["name"] = "ボルト"
    if kind == "unit": rows[0]["unit"] = "個"
    # 型式が重複しないように
    seen = set(); out = []
    for r in rows:
        if r["model"] in seen: continue
        seen.add(r["model"]); out.append(r)
    return out


def fw(s):
    t = {ord(c): ord(c) + 0xFEE0 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
    t[ord("-")] = ord("－")
    return s.translate(t)


def mutate(rows, kind, rng):
    """発注側の明細を作る。戻り: (発注の明細, 触れた型式のリスト, 発注側の合計の加算(間違った合計), 期待:違いとして検出されるべきか)"""
    o = [dict(r) for r in rows]; touched = []; tot_add = 0
    r = o[0]; orig = r["model"]
    if kind in ("none",): pass
    elif kind == "fullwidth": r["model"] = fw(r["model"])
    elif kind == "space":
        p = len(r["model"]) // 2; r["model"] = r["model"][:p] + " " + r["model"][p:]
    elif kind == "rowswap": o[0], o[1] = o[1], o[0]
    elif kind in ("qty_digit", "qty_noamt"):
        s = str(r["qty"]); i = rng.randrange(len(s)); s2 = s[:i] + CONF[s[i]] + s[i + 1:]
        if s2.startswith("0") or s2 == s: s2 = str(r["qty"] + 5)
        r["qty"] = int(s2); touched.append(orig)
        if kind == "qty_noamt": r["amount_override"] = rows[0]["qty"] * rows[0]["price"]
    elif kind == "qty_x10": r["qty"] *= 10; touched.append(orig)
    elif kind == "price_digit":
        s = str(r["price"]); i = rng.randrange(len(s)); s2 = s[:i] + CONF[s[i]] + s[i + 1:]
        if s2.startswith("0") or s2 == s: s2 = str(r["price"] + 50)
        r["price"] = int(s2); touched.append(orig)
    elif kind == "price_x10": r["price"] *= 10; touched.append(orig)
    elif kind == "model_1char":
        m = r["model"]; i = max(0, len(m) - 1)
        c = m[i]; r["model"] = m[:i] + (chr(ord(c) + 1) if c.isalpha() else CONF.get(c, "1")) + m[i + 1:]; touched += [orig, r["model"]]
    elif kind == "model_O0":
        r["model"] = r["model"].replace("0", "O", 1); touched += [orig, r["model"]]
    elif kind == "model_I1":
        r["model"] = r["model"].replace("1", "I", 1); touched += [orig, r["model"]]
    elif kind == "model_hyphen":
        r["model"] = r["model"].replace("-", "", 1); touched += [orig, r["model"]]
    elif kind == "model_swap":
        m = r["model"]
        for i in range(len(m) - 1):
            if m[i].isdigit() and m[i + 1].isdigit() and m[i] != m[i + 1]:
                r["model"] = m[:i] + m[i + 1] + m[i] + m[i + 2:]; break
        touched += [orig, r["model"]]
    elif kind == "name_kata":
        nm = r["name"]
        for i, c in enumerate(nm):
            if c in KATA: r["name"] = nm[:i] + KATA[c] + nm[i + 1:]; break
        touched.append(orig)
    elif kind == "name_kanji": r["name"] = KANJI[r["name"]]; touched.append(orig)
    elif kind == "unit": r["unit"] = "箱"; touched.append(orig)
    elif kind == "due":
        d = r["due"]; r["due"] = d[:-1] + CONF[d[-1]]; touched.append(orig)
    elif kind == "row_del": o = o[1:]; touched.append(orig)
    elif kind == "row_add":
        extra = [m for m in POOL_MODELS if m not in [x["model"] for x in o]][0]
        o.append(dict(model=extra, name="フィルタ", qty=3, price=1200, unit="個", due="12/20")); touched.append(extra)
    elif kind == "row_dup": o.append(dict(o[0])); touched.append(orig)
    elif kind == "total_wrong": tot_add = 1000; touched.append("__TOTAL__")
    return o, touched, tot_add, kind in KINDS_BAD


# ---------------------------------------------------------------- 書類の描画
def draw(page, title, qno, rows, layout, total, invisible=False):
    L = LAYOUTS[layout]; fs = L["fs"]; rm = 3 if invisible else 0
    def t(x, y, s, size=fs):
        # 英数字だけの文字列は英数字用フォント、日本語を含むものは日本語フォント(実際の書類に近づける)
        page.insert_text((x, y), s, fontname=("helv" if s.isascii() else "japan"), fontsize=size, render_mode=rm)
    t(200, 70, title, 20); t(60, 110, "宛先: ダミー商事 御中", 11)
    if qno: t(60, 130, f"見積番号: {qno}", 11)
    y = 200
    for h, x in L["cols"]: t(x, y, h)
    if not invisible: page.draw_line((40, y + 6), (550, y + 6))
    for r in rows:
        y += L["dy"]
        amt = r.get("amount_override", r["qty"] * r["price"])
        vals = {"型式": r["model"], "品名": r["name"], "数量": str(r["qty"]), "単位": r["unit"], "単価": f"{r['price']:,}", "金額": f"{amt:,}", "納期": r["due"]}
        for h, x in L["cols"]: t(x, y, vals[h])
    t(365 if layout == "L7" else 370, y + 40, "合計")
    t(430 if layout == "L7" else 460, y + 40, f"{total:,}")


def row_amount(r): return r.get("amount_override", r["qty"] * r["price"])


def render_text_pdf(path, title, qno, rows, layout, total):
    d = fitz.open(); p = d.new_page(width=595, height=842); draw(p, title, qno, rows, layout, total); d.save(path)


def render_image(title, qno, rows, layout, total, dpi, noise, blur, rot, bw=False):
    d = fitz.open(); p = d.new_page(width=595, height=842); draw(p, title, qno, rows, layout, total)
    pm = d[0].get_pixmap(dpi=dpi); im = Image.open(io.BytesIO(pm.tobytes("png"))).convert("L")
    if rot: im = im.rotate(rot, expand=False, fillcolor=255)
    if blur: im = im.filter(ImageFilter.GaussianBlur(blur))
    a = np.array(im).astype(float)
    if noise: a += np.random.normal(0, noise, a.shape)
    a = np.clip(a, 0, 255)
    if bw: a = (a > 150) * 255
    return Image.fromarray(a.astype("uint8")).convert("RGB")


def write_xlsx(path, title, qno, rows, layout, total):
    wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = title; ws["A2"] = f"見積番号: {qno}"; ws.append([])
    hdr = [h for h, _ in LAYOUTS[layout]["cols"]]; ws.append(hdr)
    for r in rows:
        vals = {"型式": r["model"], "品名": r["name"], "数量": r["qty"], "単位": r["unit"], "単価": r["price"], "金額": row_amount(r), "納期": r["due"]}
        ws.append([vals[h] for h in hdr])
    last = [""] * len(hdr); last[hdr.index("型式") if layout == "L4" else hdr.index("単価")] = "合計"; last[hdr.index("金額")] = total
    ws.append(last); wb.save(path)


def write_doc(modality, path_base, title, qno, rows, layout, total, layer_rows=None, rng=None):
    """modality: TXT / XLS / SCAN / SCAN2 / BAD / FAX / LAYER。書いたファイルのパスを返す"""
    if modality == "TXT":
        p = path_base + ".pdf"; render_text_pdf(p, title, qno, rows, layout, total); return p
    if modality == "XLS":
        p = path_base + ".xlsx"; write_xlsx(p, title, qno, rows, layout, total); return p
    prm = {"SCAN": (150, 6, 0, 0.3, False), "SCAN2": (200, 10, 0.5, 0.3, False), "BAD": (150, 26, 1.4, 0.8, False), "FAX": (100, 0, 0.3, 0, True), "LAYER": (200, 8, 0.3, 0.2, False)}[modality]
    im = render_image(title, qno, rows, layout, total, *prm)
    if modality in ("SCAN", "BAD", "FAX"):
        p = path_base + ".png"; im.save(p); return p
    # PDF(画像だけ)。LAYERは、見えない文字層を付ける(中身は layer_rows = 元の見積と同じ内容=スキャナーの誤読/コピーを想定)
    p = path_base + ".pdf"; d = fitz.open(); pg = d.new_page(width=595, height=842)
    b = io.BytesIO(); im.save(b, "PNG"); pg.insert_image(pg.rect, stream=b.getvalue())
    if modality == "LAYER" and layer_rows is not None:
        draw(pg, title, qno, layer_rows, layout, sum(row_amount(r) for r in layer_rows), invisible=True)
    d.save(p); return p


# ---------------------------------------------------------------- ケース作り
def build_cases(root, quick=False):
    shutil.rmtree(root, ignore_errors=True); os.makedirs(root)
    plan = []  # (modality, layout, kind, seed)
    def add(mod, lays, kinds, seeds):
        for lay in lays:
            for k in kinds:
                if lay == "L4" and k in ("name_kata", "name_kanji", "unit", "due"): continue
                for s in seeds: plan.append((mod, lay, k, s))
    add("TXT", ["L4", "L7"], KINDS, [1, 2])
    add("XLS", ["L4", "L7"], KINDS, [1])
    scan_kinds = [k for k in KINDS if k not in ("fullwidth",)]
    L4_NO = ("name_kata", "name_kanji", "unit", "due")  # L4には品名・単位・納期の列が無い
    add("SCAN", ["L4", "L7"], scan_kinds, [1])
    add("SCAN2", ["L4", "L7"], scan_kinds, [1])
    add("FAX", ["L4"], scan_kinds, [1])
    add("BAD", ["L4"], scan_kinds, [1])
    add("LAYER", ["L4"], ["none", "qty_digit", "price_digit", "model_1char", "qty_x10", "row_del"], [1, 2])
    if quick:
        random.Random(0).shuffle(plan); plan = plan[:40]
    if os.environ.get("MC_BENCH"):   # エンジン比較用の小さな集合: 画像だけ・数種類の間違い
        plan = [p for p in plan if p[0] in ("SCAN", "SCAN2", "FAX", "BAD") and p[2] in ("none", "qty_digit", "model_O0", "name_kata")]
    meta = {}
    for i, (mod, lay, kind, seed) in enumerate(plan):
        rng = random.Random(f"{seed}-{kind}-{lay}-{mod}")
        np.random.seed(zlib.crc32(f"{seed}-{kind}-{lay}-{mod}".encode()) % (2 ** 31))
        rows = mk_rows(rng, kind)
        orows, touched, tot_add, expect = mutate(rows, kind, rng)
        qno = f"Q-{i:04d}"
        vd = os.path.join(root, f"case{i:03d}_{mod}_{lay}_{kind}")
        os.makedirs(os.path.join(vd, "見積")); os.makedirs(os.path.join(vd, "発注"))
        qtot = sum(row_amount(r) for r in rows)
        # 発注の合計: 明細の合計(total_wrong だけ間違った合計)
        otot = sum(row_amount(r) for r in orows) + tot_add
        write_doc(mod, os.path.join(vd, "見積", "q"), "御 見 積 書", qno, rows, lay, qtot, rng=rng)
        write_doc(mod, os.path.join(vd, "発注", "o"), "注 文 書", qno, orows, lay, otot, layer_rows=rows if mod == "LAYER" else None, rng=rng)
        json.dump({"q": rows, "o": [dict(r, amount=row_amount(r)) for r in orows]}, open(os.path.join(vd, "_rows.json"), "w", encoding="utf-8"), ensure_ascii=False)
        meta[os.path.basename(vd)] = dict(mod=mod, lay=lay, kind=kind, expect=expect, touched=[alnum(t) if t != "__TOTAL__" else t for t in touched])
    json.dump(meta, open(os.path.join(root, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return meta


def eval_case(vdir):
    t0 = time.time()
    out = C.run_vendor(vdir, workers=1)
    res = []; unpaired = False; docsec = out.get("seconds", 0.0)
    for p in out["pairs"]:
        if not (p["q"] and p["o"]): unpaired = True
        for r in p["results"]:
            res.append((r["status"], alnum(r["model"]), r["msg"][:160], r["model"] == "(書類全体)"))
    return os.path.basename(vdir), res, unpaired, docsec, time.time() - t0


def main(root, quick=False):
    w0 = time.time()
    meta = json.load(open(os.path.join(root, "_meta.json"), encoding="utf-8")) if (os.environ.get("MC_REUSE") and os.path.exists(os.path.join(root, "_meta.json"))) else build_cases(root, quick)
    print(f"ケース数 {len(meta)} 作成 {time.time()-w0:.0f}秒", flush=True)
    vdirs = sorted(os.path.join(root, d) for d in meta)
    w1 = time.time()
    with cf.ProcessPoolExecutor(max_workers=4, mp_context=mp.get_context("fork")) as ex:
        results = list(ex.map(eval_case, vdirs))
    wall = time.time() - w1
    print(f"照合の実行 {wall:.0f}秒(4並列)  書類の読み取り合計 {sum(r[3] for r in results):.0f}秒", flush=True)
    stat = {}
    misses, falarms, weak = [], [], []
    for name, res, unpaired, docsec, _ in results:
        m = meta[name]; key = m["mod"]
        s = stat.setdefault(key, dict(cases=0, bad_cases=0, caught_row=0, caught_doc=0, caught_unpaired=0, missed=0, ok_cases=0, false_alarm=0, ok_ok=0, docsec=0.0, diff_exact=0, review_for_bad=0))
        s["cases"] += 1; s["docsec"] += docsec
        nonok = [r for r in res if r[0] in ("DIFF", "REVIEW")]
        if m["expect"]:
            s["bad_cases"] += 1
            if m["kind"] == "total_wrong":
                caught = bool(nonok); how = "row" if any(not r[3] for r in nonok) else ("doc" if nonok else None)
            else:
                how = None
                # 型式を変える間違い(元の型式と、変えた型式)は、どちらかの行が要確認/違いになれば検出(OCRが同じ型式と読めば、変えた型式の行は出ない)
                groups = [m["touched"]] if m["kind"].startswith("model_") else [[t] for t in m["touched"]]
                for grp in groups:
                    hit = [r for r in nonok if not r[3] and any(t in r[1] for t in grp)]
                    if hit: how = how or "row"
                    elif any(r[3] for r in nonok): how = how or "doc"
                    elif unpaired: how = how or "unpaired"
                    else: how = "MISS"; break
                caught = how != "MISS"
            if how == "MISS" or not nonok and not unpaired:
                s["missed"] += 1; misses.append((name, [r for r in res][:8]))
            elif how == "row": s["caught_row"] += 1
            elif how == "doc": s["caught_doc"] += 1
            else: s["caught_unpaired"] += 1
            if m["mod"] in ("TXT", "XLS") and how == "row" and not any(r[0] == "DIFF" for r in nonok if not r[3]):
                s["review_for_bad"] += 1; weak.append((name, nonok[:3]))
        else:
            s["ok_cases"] += 1
            if nonok or unpaired:
                s["false_alarm"] += 1; falarms.append((name, nonok[:3], unpaired))
            else:
                s["ok_ok"] += 1
    print("\n=== 結果(種類ごと) ===")
    print("読み取り      件数  違いあり(正しく検出 / 見逃し)  違いなし(OK / 誤警報)  1書類の秒数")
    tot_miss = 0
    names = {"TXT": "文字入りPDF", "XLS": "Excel", "SCAN": "画像(150dpi)", "SCAN2": "スキャンPDF(200dpi)", "FAX": "FAX風(白黒100dpi)", "BAD": "粗い画像", "LAYER": "文字層つきスキャン(層は元のまま)"}
    for k in ("TXT", "XLS", "SCAN", "SCAN2", "FAX", "BAD", "LAYER"):
        if k not in stat: continue
        s = stat[k]; tot_miss += s["missed"]
        print(f"{names[k]:<22}{s['cases']:>4}   違い{s['bad_cases']:>3}件: 検出{s['bad_cases']-s['missed']:>3}(行で{s['caught_row']}/書類全体{s['caught_doc']}/組なし{s['caught_unpaired']}) 見逃し{s['missed']:>2}   違いなし{s['ok_cases']:>3}件: OK{s['ok_ok']:>3} 誤警報{s['false_alarm']:>3}   {s['docsec']/(2*s['cases']):.1f}秒")
    print(f"\n★見逃し(違いがあるのにOKと出した): 合計 {tot_miss} 件")
    for name, res in misses[:30]:
        print("  🚨", name, meta[name]["touched"], res[:4])
    print(f"\n誤警報(違いが無いのに要確認/違いと出した): {len(falarms)} 件のうち、画像以外: {sum(1 for n,_,_ in falarms if meta[n]['mod'] in ('TXT','XLS'))}")
    for name, nonok, unp in falarms[:40]:
        print("  ・", name, "組なし" if unp else "", [(a, b, c[:70]) for a, b, c, d in nonok][:2])
    print(f"\n文字入り/Excelなのに『違い』ではなく『要確認』止まり: {len(weak)} 件")
    for name, nonok in weak[:10]:
        print("  ・", name, [(a, b, c[:90]) for a, b, c, d in nonok][:2])
    json.dump(dict(stat=stat, misses=[m[0] for m in misses], falarms=[f[0] for f in falarms], wall=wall), open(os.path.join(root, "_result.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return tot_miss


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], quick=(len(sys.argv) > 2 and sys.argv[2] == "quick")) else 0)
