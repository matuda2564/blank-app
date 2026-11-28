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

# 写真(2026-11-28)の見た目: 見出しが2段・区分/発注要求元が注文番号の真下・上段に注文番号と金額、下段に個数と単価。+ 自社請求書の『0円の内訳の行』
import gen_ks3, fitz
tmp = tempfile.mkdtemp(); os.environ["MC_WORK"] = tmp
for role in ("自社請求書", "京セラ請求書"): os.makedirs(os.path.join(tmp, "K", role))
gen_ks.make_ours(os.path.join(tmp, "K", "自社請求書", "請求書.pdf")); gen_ks3.make_kyo3(os.path.join(tmp, "K", "京セラ請求書", "検収明細.pdf"))
# 自社の請求書に、『セットの部品(0円)』を足した版
pg = fitz.open(os.path.join(tmp, "K", "自社請求書", "請求書.pdf")); page = pg[0]
y = 222 + 22 * 12 + 4
for k, nm in enumerate(("IDEC 押ボタンスイッチ HW1B-M110G", "IDEC ランプ HW1P-1QMPW")):
    page.insert_text((50, y), "11/11", fontname="helv", fontsize=9); page.insert_text((100, y), "10076598393", fontname="helv", fontsize=9)
    page.insert_text((210, y), nm, fontname="helv", fontsize=8); page.insert_text((388, y), "1", fontname="helv", fontsize=9)
    page.insert_text((470 - fitz.get_text_length("0", "helv", 9), y), "0", fontname="helv", fontsize=9); page.insert_text((535 - fitz.get_text_length("0", "helv", 9), y), "0", fontname="helv", fontsize=9); y += 22
pg.save(os.path.join(tmp, "K", "自社請求書", "請求書2.pdf")); pg.close(); os.remove(os.path.join(tmp, "K", "自社請求書", "請求書.pdf")); os.rename(os.path.join(tmp, "K", "自社請求書", "請求書2.pdf"), os.path.join(tmp, "K", "自社請求書", "請求書.pdf"))
v = C.run_vendor(os.path.join(tmp, "K"), workers=1, name="K", mode="ks")
allr = [r for p in v["pairs"] for r in p["results"]]
res = {r["model"].split()[0] + str(i): r for i, r in enumerate(allr)}
ok = [r for r in allr if r["status"] == "OK"]
assert len(ok) == 9 + 2, (len(ok), [(r["model"], r["status"], r["msg"][:30]) for r in allr])
assert sum(1 for r in allr if r["status"] != "OK") == 4, [(r["model"], r["status"]) for r in allr]
shutil.rmtree(tmp, ignore_errors=True)
print("ks_rows real-layout OK")

# 読み取りの確認(書類の『当月検収額』と、読み取った金額の合計の突き合わせ)
import gen_ks3
tmp = tempfile.mkdtemp(); os.environ["MC_WORK"] = tmp
gen_ks3.make_kyo3(os.path.join(tmp, "k.pdf"))
dk = C.read_doc(os.path.join(tmp, "k.pdf"), "京セラ請求書"); rk = ks_rows.extract(dk)
st, msg = ks_rows.read_check(dk, rk); assert st == "ok", (st, msg, ks_rows.stated_total(dk))
st, msg = ks_rows.read_check(dk, rk[:-2]); assert st == "ng" and "差" in msg, (st, msg)   # 2行 読み漏らしたら、警告
shutil.rmtree(tmp, ignore_errors=True)
print("ks_rows read-check OK")

# 自社請求書の品名(『メーカー / 品名』と見出しが分かれていても読める)・読み取り結果のCSV → 書類の代わりに読み込める
tmp = tempfile.mkdtemp(); os.environ["MC_WORK"] = tmp
gen_ks.make_ours(os.path.join(tmp, "o.pdf")); gen_ks3.make_kyo3(os.path.join(tmp, "k.pdf"))
do = C.read_doc(os.path.join(tmp, "o.pdf"), "自社請求書"); ro = ks_rows.extract(do)
assert all(r["name"] for r in ro), [r["model"] for r in ro if not r["name"]]
dk = C.read_doc(os.path.join(tmp, "k.pdf"), "京セラ請求書"); rk = ks_rows.extract(dk)
import csv
with open(os.path.join(tmp, "k.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f); w.writerow(["注文番号", "品名", "数量", "単価", "金額"]); [w.writerow([r["model"], r["name"], r["qty"], r["price"], r["amount"]]) for r in rk]
dc = C.read_doc(os.path.join(tmp, "k.csv"), "京セラ請求書"); rc = ks_rows.extract(dc)
assert [(r["model"], r["qty"], r["price"], r["amount"]) for r in rc] == [(r["model"], r["qty"], r["price"], r["amount"]) for r in rk]
shutil.rmtree(tmp, ignore_errors=True)
print("ks_rows csv-roundtrip OK")
