"""お客さんの一覧の、区切り方の試験。使い方: python names_test.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import nk_core as N
N3 = ["キョウセラ", "ヤマダデンキ", "アオキセイサクショ"]
for label, t in {"改行": "キョウセラ\nヤマダデンキ\nアオキセイサクショ", "読点": "キョウセラ、ヤマダデンキ、アオキセイサクショ", "カンマ": "キョウセラ,ヤマダデンキ,アオキセイサクショ",
                 "全角カンマ": "キョウセラ，ヤマダデンキ，アオキセイサクショ", "空白(1行)": "キョウセラ ヤマダデンキ アオキセイサクショ", "全角空白(1行)": "キョウセラ　ヤマダデンキ　アオキセイサクショ",
                 "タブ": "キョウセラ\tヤマダデンキ\tアオキセイサクショ", "Excel列(CRLF)": "キョウセラ\r\nヤマダデンキ\r\nアオキセイサクショ\r\n", "混在": "キョウセラ, ヤマダデンキ、\nアオキセイサクショ"}.items():
    got = [e["name"] for e in N.parse_names(t)]
    assert got == N3, (label, got)
assert [e["name"] for e in N.parse_names("ヤマダ タロウ\nイトウ タロウ")] == ["ヤマダ タロウ", "イトウ タロウ"]   # 名前の中の空白は、改行区切りのとき、保つ
e = N.parse_names("京セラ=キョウセラ=ｷﾖｳｾﾗ\nヤマダデンキ")
assert e[0]["name"] == "京セラ" and len(e[0]["keys"]) == 2 and len(e) == 2   # 別の書き方(ｷﾖｳｾﾗ と キョウセラ は、同じ形にそろう)
assert len(N.parse_names("キョウセラ\nｷﾖｳｾﾗ\nきょうせら")) == 1                 # 同じ名前の、書き方違いは、1人にまとめる
print("names_test OK")
