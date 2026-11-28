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
