"""見積⇔発注: 何枚でもまとめて入れたときの組の決め方と、組にならなかったときの理由の出し方。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mc_core as C

def doc(role, name, text, models):
    d = C.Doc("/x/" + name, role); d.text = text
    d.rows = [{"model_raw": m, "model": C.norm(m), "problems": [], "exact": True, "src": "text"} for m in models]
    return d

# 同じ型番 B9565AW-T が、2枚の見積に入っている。発注書は、見積番号で決める
q1 = doc("見積", "q1.pdf", "見積書No:316386-1", ["B9565AW-T"])
q2 = doc("見積", "q2.pdf", "見積No. 1252033", ["B9565AW-T"])
o1 = doc("発注", "o1.pdf", "貴社見積No.316386-1の件です", ["B9565AW-T"])
o2 = doc("発注", "o2.pdf", "お見積りNO.NO.1251369", ["AE20-133"])
assert C.quote_no("お見積りNO.NO.1251369") == "1251369"
pairs = C.pair_docs([q1, q2], [o1, o2])
got = {(p[0].name if p[0] else None, p[1].name if p[1] else None) for p in pairs}
assert (("q1.pdf", "o1.pdf")) in got, got                     # 見積番号の書いてある発注書と、組になる
assert (None, "o2.pdf") in got and ("q2.pdf", None) in got, got   # 対応のないものは、組にしない
why = [p[2] for p in pairs if p[1] is o2][0]
assert "1251369" in why and "1252033" in why, why             # 理由に、見積番号が出る
# 別の社の枠に、相手がいるとき、気づかせる
vA = {"vendor": "A社", "pairs": [{"q": q2, "o": None, "why": "対応する発注書がない"}]}
o3 = doc("発注", "o3.pdf", "見積No. 1252033", ["B9565AW-T"])
vB = {"vendor": "B社", "pairs": [{"q": None, "o": o3, "why": "相手が決まらない"}]}
C.cross_hints([vA, vB])
assert "B社" in vA["pairs"][0]["why"] and "A社" in vB["pairs"][0]["why"], (vA, vB)
print("qo_pair_test OK")
