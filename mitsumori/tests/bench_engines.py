# -*- coding: utf-8 -*-
"""3つのOCRエンジンを、1つずつ単独で、正解の明細と比べる。 使い方: python bench_engines.py <ケースのフォルダ>"""
import os, sys, json, time, glob
sys.path.insert(0, os.path.dirname(__file__))
import mc_core as C
from PIL import Image
import fitz, io
root = sys.argv[1]
meta = json.load(open(os.path.join(root, "_meta.json"), encoding="utf-8"))
ENG = {"Rapid": C.ocr_rapid, "Tess": C.ocr_tess, "Easy": C.ocr_easy}
if os.environ.get("MC_ENGINES"): ENG = {k: v for k, v in ENG.items() if k in os.environ["MC_ENGINES"].split(",")}
stat = {}
def key(m): return C.norm(m)
for name, m in sorted(meta.items()):
    vd = os.path.join(root, name); tr = json.load(open(os.path.join(vd, "_rows.json"), encoding="utf-8"))
    for role, rows in (("見積", tr["q"]), ("発注", tr["o"])):
        f = glob.glob(os.path.join(vd, role, "*"))[0]
        if f.endswith(".pdf"):
            pg = fitz.open(f)[0]; im = Image.open(io.BytesIO(pg.get_pixmap(dpi=200).tobytes("png"))).convert("RGB")
        else: im = Image.open(f).convert("RGB")
        truth = {key(r["model"]): r for r in rows}
        for en, fn in ENG.items():
            t0 = time.time(); segs = fn(im); dt = time.time() - t0
            got, _, err = C.parse_rows(C.group_lines(segs))
            gm = {}
            for g in got: gm.setdefault(g["model"], []).append(g)
            s = stat.setdefault((m["mod"], m["lay"], en), dict(rows=0, exact=0, model_ok=0, missing=0, wrong_fields=0, sec=0.0, docs=0, nohdr=0))
            s["docs"] += 1; s["sec"] += dt
            if err: s["nohdr"] += 1
            for k, tr_ in truth.items():
                s["rows"] += 1
                g = gm.get(k)
                if not g: s["missing"] += 1; continue
                g = g[0]; s["model_ok"] += 1
                want = dict(qty=tr_["qty"], price=tr_["price"], amount=tr_.get("amount", tr_["qty"] * tr_["price"]))
                if m["lay"] == "L7": want.update(name=C.norm(tr_["name"]), unit=C.norm(tr_["unit"]), due=C.norm(tr_["due"]))
                ok = all(g.get(a) == b for a, b in want.items())
                if ok: s["exact"] += 1
                else: s["wrong_fields"] += 1
print("読み取り / 配置 / エンジン :  行数  全項目まで正確  型式は読めた  行が見つからない  項目違い  見出し読めず書類  1枚秒")
for (mod, lay, en), s in sorted(stat.items()):
    print(f"{mod:<6}{lay} {en:<6}: {s['rows']:>4}  {s['exact']:>4} ({100*s['exact']/max(1,s['rows']):.0f}%)  {s['model_ok']:>4}  {s['missing']:>4}  {s['wrong_fields']:>4}  {s['nohdr']:>3}/{s['docs']}  {s['sec']/s['docs']:.1f}")
tot = {}
for (mod, lay, en), s in stat.items():
    t = tot.setdefault(en, dict(rows=0, exact=0)); t["rows"] += s["rows"]; t["exact"] += s["exact"]
print("\n== 合計(全部の画像) ==")
for en, t in tot.items(): print(f"{en:<6}: 全項目まで正確 {t['exact']}/{t['rows']} = {100*t['exact']/t['rows']:.1f}%")
