# -*- coding: utf-8 -*-
"""エンジンの組み合わせごとに、『一致した行(確実)』の数と、その中の間違いの数を測る。間違いは0でなければならない。"""
import os, sys, json, glob, io, time, copy, pickle
sys.path.insert(0, os.path.dirname(__file__))
import mc_core as C
from PIL import Image
import fitz
root = sys.argv[1]
meta = json.load(open(os.path.join(root, "_meta.json"), encoding="utf-8"))
ENG = {"R": C.ocr_rapid, "T": C.ocr_tess, "E": C.ocr_easy}
COMBOS = ["R+T", "R+E", "R+T+E"]
res = {c: {} for c in COMBOS}
def keys(lay): return ["model", "qty", "price", "amount"] + (["name", "unit", "due"] if lay == "L7" else [])
for name, m in sorted(meta.items()):
    vd = os.path.join(root, name); tr = json.load(open(os.path.join(vd, "_rows.json"), encoding="utf-8"))
    for role, rows in (("見積", tr["q"]), ("発注", tr["o"])):
        f = glob.glob(os.path.join(vd, role, "*"))[0]
        if f.endswith(".pdf"): im = Image.open(io.BytesIO(fitz.open(f)[0].get_pixmap(dpi=200).tobytes("png"))).convert("RGB")
        else: im = Image.open(f).convert("RGB")
        parsed = {}
        for en, fn in ENG.items():
            ck = os.path.join(root, "_ocr_cache", f"{name}_{role}_{en}.pkl"); os.makedirs(os.path.dirname(ck), exist_ok=True)
            if os.path.exists(ck): segs = pickle.load(open(ck, "rb"))
            else: segs = fn(im); pickle.dump(segs, open(ck, "wb"))
            r, _, _ = C.parse_rows(C.group_lines(segs)); parsed[en] = (r, 0)
        truth = {C.norm(r["model"]): r for r in rows}
        for combo in COMBOS:
            rd = [copy.deepcopy(parsed[e][0]) for e in combo.split("+")]
            merged = C.merge_readers(rd)
            s = res[combo].setdefault(m["mod"], dict(rows=len(0 for _ in ()) if False else 0, exact=0, wrong=0, covered=0))
            s["rows"] += len(truth)
            for g in merged:
                if not g.get("exact"): continue
                t_ = truth.get(g["model"])
                s["exact"] += 1
                want = dict(model=C.norm(t_["model"]) if t_ else None, qty=t_["qty"] if t_ else None, price=t_["price"] if t_ else None, amount=t_.get("amount", t_["qty"] * t_["price"]) if t_ else None)
                if not t_ or any(g.get(k) != v for k, v in want.items()):
                    s["wrong"] += 1
                if m["lay"] == "L7" and t_ and any(g.get(k) != v for k, v in dict(name=C.norm(t_["name"]), unit=C.norm(t_["unit"]), due=C.norm(t_["due"])).items()):
                    s["soft_wrong"] = s.get("soft_wrong", 0) + 1
print("組み合わせ  読み取り : 行数  『確実(一致)』の行  そのうち間違い  カバー率")
for combo in COMBOS:
    for mod, s in sorted(res[combo].items()):
        print(f"{combo:<7} {mod:<6}: {s['rows']:>4}   {s['exact']:>4}   {s['wrong']:>3}   {100*s['exact']/max(1,s['rows']):.0f}%   (品名等の誤読を含む行 {s.get('soft_wrong',0)})")
    tot = sum(s["exact"] for s in res[combo].values()); wr = sum(s["wrong"] for s in res[combo].values()); rw = sum(s["rows"] for s in res[combo].values())
    print(f"== {combo} 合計: 確実 {tot}/{rw} ({100*tot/rw:.0f}%)  間違った『確実』= {wr}\n")
