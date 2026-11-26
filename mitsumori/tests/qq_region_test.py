"""見積⇔見積: 型式・品名の列(数量より左)だけを読むことの試験。備考・宛先・納期など、ほかの場所の文字は拾わない。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mc_core as C

def line(y, parts):
    toks, x = [], 10.0
    for t in parts:
        w = max(20.0, 9.0 * len(t)); toks.append((t, x, x + w)); x += w + 12
    return {"page": 0, "text": " ".join(parts), "x0": toks[0][1], "y0": y, "x1": toks[-1][2], "y1": y + 10, "src": "text", "toks": toks}

# 列: 品名(左) / 数量 / 単価 / 備考(右)。右の備考に、型番らしい文字が、混ざっている
hdr = {"page": 0, "text": "品名 数量 単価 備考", "x0": 10, "y0": 100, "x1": 300, "y1": 110, "src": "text",
       "toks": [("品名", 10, 50), ("数量", 200, 230), ("単価", 240, 270), ("備考", 280, 310)]}
body = {"page": 0, "text": "AW20-02BE-D 圧力計 2 500 備考ZZZ-999X", "x0": 10, "y0": 120, "x1": 400, "y1": 130, "src": "text",
        "toks": [("AW20-02BE-D", 10, 110), ("圧力計", 115, 160), ("2", 205, 215), ("500", 245, 265), ("備考ZZZ-999X", 285, 380)]}
head_txt = {"page": 0, "text": "TEL:092-432-0700 納入期日 Q12-ABC", "x0": 10, "y0": 10, "x1": 300, "y1": 20, "src": "text",
            "toks": [("TEL:092-432-0700", 10, 120), ("納入期日", 130, 180), ("Q12-ABC", 190, 260)]}
end = {"page": 0, "text": "合計 1,000", "x0": 10, "y0": 140, "x1": 100, "y1": 150, "src": "text", "toks": [("合計", 10, 40), ("1,000", 50, 90)]}
tail = {"page": 0, "text": "※型式 KQ2-10A に変更", "x0": 10, "y0": 160, "x1": 200, "y1": 170, "src": "text", "toks": [("※型式", 10, 50), ("KQ2-10A", 55, 120)]}
reg, note, ok = C.item_region([head_txt, hdr, body, end, tail])
toks = [t for L in reg for t in C.model_tokens(L["text"])]
assert ok and toks == ["AW20-02BE-D"], toks
# 見出しが無い書類は、全体から拾い、その旨を説明に残す
reg2, note2, ok2 = C.item_region([head_txt, body])
assert not ok2 and "見出しが見つからなかった" in note2
# 2段目の見出し(型式 備考)で、明細を終わらせない / インチの寸法は型番にしない
assert C.model_tokens("AT3/8-100 AE20-133*0.6MPA") == ["AE20-133"]
print("qq_region_test OK")
