"""京セラ用(注文番号で読む)の試験。自社請求書(日付/ご注文番号/品名/数量/単価/金額)と、京セラの検収明細表(注文番号/品名/入荷数/合格数/単価/検収金額)を、作って、
単価違い・個数違い・片方にしかない注文を、全部見つけることを確かめる。使い方: python ks_rows_test.py"""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_core as C, gen_ks
tmp = tempfile.mkdtemp(); os.environ["MC_WORK"] = tmp
for role in ("自社請求書", "京セラ請求書"): os.makedirs(os.path.join(tmp, "K", role))
gen_ks.make_ours(os.path.join(tmp, "K", "自社請求書", "請求書.pdf")); gen_ks.make_kyo(os.path.join(tmp, "K", "京セラ請求書", "検収明細.pdf"))
v = C.run_vendor(os.path.join(tmp, "K"), workers=1, name="K", mode="ks")
res = {r["model"].split()[0]: r for p in v["pairs"] for r in p["results"]}
assert len(res) == 13, len(res)
ok = [k for k, r in res.items() if r["status"] == "OK"]; assert len(ok) == 9, ok
assert "単価" in res["10077473640"]["msg"] and "185,000" in res["10077473640"]["msg"]
assert "個数" in res["10076774752"]["msg"]
assert res["10077999999"]["status"] == "DIFF" and res["10077661889"]["status"] == "DIFF"
shutil.rmtree(tmp, ignore_errors=True)
print("ks_rows_test OK")

# 京セラの書類の見た目: 桁区切り・小数点のあとに、すきまが空く(「1. 000」「112, 000. 00000」)
import ks_rows
assert [ks_rows.num(x) for x in ("10.000", "5,600.00000", "56,000.00")] == [10, 5600, 56000]
m = ks_rows._merge([("10.", 0, 20), ("000", 22, 40), ("5,", 100, 110), ("600.", 112, 135), ("00000", 137, 170)])
assert [t for t, *_ in m] == ["10.000", "5,600.00000"], m
class _S:  # 割り当てが、ずれた行の置き直し
    pass
row = {"qty": 56000, "price": 5600, "amount": None, "problems": []}
ks_rows._fix_by_arith(row, [("10077554560", 0, 1, 0, 1), ("10.000", 0, 1, 0, 1), ("5,600.00000", 0, 1, 0, 1), ("56,000.00", 0, 1, 0, 1), ("5,600.00", 0, 1, 0, 1)])
assert (row["qty"], row["price"], row["amount"]) == (10, 5600, 56000), row
print("ks_rows spaced OK")

# 通し(すきま入りの京セラ書類): 9件一致・単価違い・個数違い・無し・余分を見つける
import gen_ks2
tmp = tempfile.mkdtemp(); os.environ["MC_WORK"] = tmp
for role in ("自社請求書", "京セラ請求書"): os.makedirs(os.path.join(tmp, "K", role))
gen_ks.make_ours(os.path.join(tmp, "K", "自社請求書", "請求書.pdf")); gen_ks2.make_kyo2(os.path.join(tmp, "K", "京セラ請求書", "検収明細.pdf"))
v = C.run_vendor(os.path.join(tmp, "K"), workers=1, name="K", mode="ks")
res = {r["model"].split()[0]: r for p in v["pairs"] for r in p["results"]}
ok = [k for k, r in res.items() if r["status"] == "OK"]; assert len(ok) == 9, (len(ok), {k: r["status"] for k, r in res.items()})
assert "単価" in res["10077473640"]["msg"] and "個数" in res["10076774752"]["msg"]
shutil.rmtree(tmp, ignore_errors=True)
print("ks_rows spaced e2e OK")
