"""まとめ入れ試験の、採点。 python eval_multi.py <データフォルダ(gen_multi.pyの出力)>
採点: 発注書の各行について、正解と、ツールの判定を、突き合わせる。
  ・OK(確実/画像で一致)にしてよいのは、正解が『同じ』の行だけ。**違う行・新品目をOKにしたら、見逃し(最重要)**。
  ・正解が『数量が違う』『見積に無い』行は、DIFF か REVIEW に出ること。
  ・見積にあるのに、発注されなかった品目は、何らかの形で知らせること(黙らない)。"""
import sys, os, json, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mc_core as C

data = sys.argv[1]
T = json.load(open(os.path.join(data, "truth.json"), encoding="utf-8"))
root = os.path.join(data, "mixed")
v = C.run_vendor(root, workers=2, name="X", mode="qo")
po_res = collections.defaultdict(list)   # (発注書, 型番) -> [(判定, 文言, 組の見積名)]
q_res = collections.defaultdict(list)    # (見積, 型番) -> [(判定, 文言)]
unpaired_po, unpaired_q = [], []
for p in v["pairs"]:
    qn = p["q"].name if p["q"] else None; on = p["o"].name if p["o"] else None
    if not (qn and on):
        (unpaired_po if on else unpaired_q).append(on or qn)
    for r in p["results"]:
        m = C.tkey(r["model"]) if r["model"] and not r["model"].startswith("(") else None
        if not m: continue
        if r.get("orow") is not None and on: po_res[(on, m)].append((r["status"], r["msg"], qn))
        if r.get("qrow") is not None and qn: q_res[(qn, m)].append((r["status"], r["msg"]))
stat = collections.Counter(); bad = []
OKS = ("OK", "OK_IMG")
def nm(f):   # スキャン化したときは、拡張子が .png になる
    return f if (f, ) and os.path.exists(os.path.join(root, "発注", f)) or os.path.exists(os.path.join(root, "見積", f)) else f.replace(".pdf", ".png")
for key, tr in T["po"].items():
    f, m = key.split("|"); f = nm(f); k = (f, C.tkey(m)); rs = po_res.get(k, [])
    st = [x[0] for x in rs]
    if tr["expect"] == "OK":
        if rs and all(s in OKS for s in st) and (tr["q"] is None or any(x[2] == nm(tr["q"]) for x in rs)): stat["同じ→OK ○"] += 1
        elif any(s in OKS for s in st): stat["同じ→OKだが、組の見積が違う"] += 1; bad.append(("組違い", k, [(a, b[:40], c) for a, b, c in rs]))
        elif rs: stat["同じ→要確認/違いに出た(安全側)"] += 1; bad.append(("過剰警告", k, [(a, b[:50]) for a, b, c in rs]))
        else: stat["同じ→結果に出ない(黙った)"] += 1; bad.append(("黙った", k, rs))
    else:
        if any(s in OKS for s in st): stat[f"{tr['expect']}→OKにした=見逃し🚨"] += 1; bad.append(("見逃し", k, [(a, b[:50]) for a, b, c in rs]))
        elif rs: stat[f"{tr['expect']}→知らせた ○"] += 1
        else: stat[f"{tr['expect']}→結果に出ない(黙った)🚨"] += 1; bad.append(("黙った", k, rs))
n_not = 0; n_not_ok = 0
for key, tr in T["q"].items():
    f, m = key.split("|"); k = (f, C.tkey(m)); rs = q_res.get(k, [])
    if tr == "NOT_ORDERED":
        n_not += 1
        if any(x[0] != "OK" for x in rs) or f in unpaired_q: n_not_ok += 1   # 見積ごと、発注が無いものは、「対応する発注書がない」として、知らせている
        else: bad.append(("発注されなかった品目を黙った", k, rs))
print("== 判定の結果(発注書の行ごと) ==")
for k_, n in sorted(stat.items()): print(f"  {n:3d}  {k_}")
print(f"== 見積にあって発注されなかった品目: {n_not}件中、知らせた {n_not_ok}件")
print(f"== 組にならなかった書類: 発注書 {len(unpaired_po)}枚 / 見積 {len(unpaired_q)}枚")
print("== 問題の例 ==")
for b in bad[:16]: print("  ", b)
