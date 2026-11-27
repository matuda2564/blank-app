"""見積⇔発注エンジンの、試験(合成データ)。 まとめ入れ(5社)・分納・合算・改訂見積・型番の書き方違い・曖昧さ。"""
import sys, os, subprocess, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tempfile
out = tempfile.mkdtemp(prefix="qo_engine_")
subprocess.check_call([sys.executable, os.path.join(HERE, "gen_multi.py"), out], stdout=subprocess.DEVNULL)
r = subprocess.run([sys.executable, os.path.join(HERE, "eval_multi.py"), out], capture_output=True, text=True)
print(r.stdout)
assert "見逃し" not in r.stdout.split("== 問題の例")[0], "違う行・新品目を、OKにした(見逃し)"
assert "黙った" not in r.stdout.split("== 問題の例")[0], "結果に出ない行がある"
assert "組の見積が違う" not in r.stdout, "別の見積に、ひもづけた"
assert "過剰警告" not in r.stdout and "同じ→要確認" not in r.stdout, "同じ行を、要確認にした(文字入りPDFでは、起きないはず)"
print("qo_engine_test OK")

# --- 単体: 曖昧さ(同じ内容の見積が2枚・発注書に会社名も見積番号も無い)・分納(1行が2枚の発注書に分かれる)
import mc_core as C, qo_engine as E
def doc(role, name, rows, text=""):
    d = C.Doc("/x/" + name, role); d.src = "text"; d.kind = "文字入りPDF"; d.text = text; d.lines = []; d.lines2 = []
    d.rows = [dict({"model_raw": m, "model": C.norm(m), "name": n, "problems": [], "soft": [], "exact": True, "src": "text", "left": f"{n} {m}", "unit": None, "due": None}, qty=q, price=p, amount=q * p) for m, n, q, p in rows]
    return d
q1 = doc("見積", "q1.pdf", [("AW20-02BE-D", "レギュレータ", 2, 4200)]); q2 = doc("見積", "q2.pdf", [("AW20-02BE-D", "レギュレータ", 2, 4200)])
o1 = doc("発注", "o1.pdf", [("AW20-02BE-D", "レギュレータ", 2, 4200)])
out = E.build([q1, q2], [o1])
res = [r for _, _, _, rs, _, _ in out for r in rs]
assert res and all(r["status"] != "OK" for r in res) and any("ほかにもある" in r["msg"] for r in res), res
# 分納: 見積10個 → 発注 6個+4個(別々の発注書)
q = doc("見積", "q.pdf", [("KQ2H06-M5", "継手", 10, 380)]); oa = doc("発注", "oa.pdf", [("KQ2H06-M5", "継手", 6, 380)]); ob = doc("発注", "ob.pdf", [("KQ2H06-M5", "継手", 4, 380)])
out = E.build([q], [oa, ob])
res = [r for _, _, _, rs, _, _ in out for r in rs]
assert res and not any(r["status"] == "DIFF" for r in res) and any("分かれている" in r["msg"] for r in res), res
# 数量が足りない分納(6+3=9個): 違いとして出る
ob2 = doc("発注", "ob2.pdf", [("KQ2H06-M5", "継手", 3, 380)])
out = E.build([q], [oa, ob2]); res = [r for _, _, _, rs, _, _ in out for r in rs]
assert any(r["status"] == "DIFF" and "数量" in r["msg"] for r in res), res
print("qo_engine_unit OK")
