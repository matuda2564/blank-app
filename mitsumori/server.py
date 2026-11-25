# -*- coding: utf-8 -*-
"""見積・発注 照合ツールの本体(パソコンの中だけで動く小さなサーバー+操作画面)
・127.0.0.1(自分のパソコンだけ)で待ち受ける。外のネットには何も送らない。
・書類は作業フォルダに置き、リセットで全部消す。過去の書類を覚えない。
"""
import os, sys, json, re, shutil, threading, time, base64, io, urllib.parse, tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_core as C

VERSION = "v1"
WORK = os.environ.get("MC_WORK") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "見積照合_作業フォルダ")
NSLOT = 4
ROLES = ("見積", "発注")
LOCK = threading.Lock()
JOB = {"status": "idle", "done": 0, "total": 0, "msg": "", "result": None, "t0": 0, "sec": 0}
NAMES = {i: f"{'ABCD'[i-1]}社" for i in range(1, NSLOT + 1)}


def slot_dir(i, role=None):
    p = os.path.join(WORK, f"slot{i}")
    return os.path.join(p, role) if role else p


def safe_name(n):
    n = os.path.basename(n.replace("\\", "/"))
    n = re.sub(r'[<>:"|?*\x00-\x1f]', "_", n).strip(" .")
    return n or "file"


def state():
    slots = []
    for i in range(1, NSLOT + 1):
        d = {"id": i, "name": NAMES[i]}
        for r in ROLES:
            p = slot_dir(i, r)
            d[r] = sorted(os.listdir(p)) if os.path.isdir(p) else []
        slots.append(d)
    j = {k: JOB[k] for k in ("status", "done", "total", "msg", "sec")}
    if JOB["status"] == "running": j["sec"] = round(time.time() - JOB["t0"])
    return {"version": VERSION, "slots": slots, "job": j}


def serialize(vendors):
    """結果を画面用にまとめる。根拠の画像は、要確認・違い・画像一致の行だけ付ける(軽くするため)。
    書類全体の画像(確認ビュー用)は、ページごとに1回だけ入れて、行からは番号で参照する"""
    out, pages, pid = [], {}, {}

    def page_id(doc, pno):
        k = (doc.path, pno)
        if k not in pid:
            pid[k] = f"p{len(pid)}"
            try: pages[pid[k]] = "data:image/jpeg;base64," + C.page_jpeg_b64(doc, pno)
            except Exception: pages[pid[k]] = None
        return pid[k] if pages.get(pid[k]) else None

    for v in vendors:
        sv = {"vendor": v["vendor"], "warnings": v["warnings"], "pairs": [], "seconds": round(v.get("seconds", 0), 1),
              "count": {"OK": 0, "OK_IMG": 0, "DIFF": 0, "REVIEW": 0, "NOPAIR": 0}}
        for p in v["pairs"]:
            q, o = p["q"], p["o"]
            sp = {"q": q.name if q else None, "o": o.name if o else None, "why": p["why"], "compared": p["compared"], "skipped": p["skipped"], "rows": []}
            if not (q and o):
                sv["count"]["NOPAIR"] += 1
            for r in p["results"]:
                sv["count"][r["status"]] += 1
                row = {"status": r["status"], "model": r["model"], "msg": r["msg"]}
                if r["status"] != "OK":
                    row["qimg"] = C.crop_b64(q, r["qrow"]) if q and r["qrow"] else None
                    row["oimg"] = C.crop_b64(o, r["orow"]) if o and r["orow"] else None
                    row["qraw"] = r["qrow"]["raw"] if r["qrow"] else None
                    row["oraw"] = r["orow"]["raw"] if r["orow"] else None
                    for side, d, rr in (("q", q, r["qrow"]), ("o", o, r["orow"])):
                        box = C.row_box(d, rr) if d and rr else None
                        row[side + "box"] = box
                        row[side + "page"] = page_id(d, rr.get("page", 0)) if box else None
                    if r.get("doc"):   # 書類全体の問題: 両方の書類の1ページ目を、枠なしで見せる
                        for sd, dd in (("q", q), ("o", o)):
                            if dd and dd.pages and not row.get(sd + "page"): row[sd + "page"] = page_id(dd, 0)
                    row["docflag"] = r.get("doc")
                sp["rows"].append(row)
            sv["pairs"].append(sp)
        out.append(sv)
    return {"vendors": out, "pages": pages}


def run_job():
    try:
        vendors = []
        slots = [i for i in range(1, NSLOT + 1) if any(os.path.isdir(slot_dir(i, r)) and os.listdir(slot_dir(i, r)) for r in ROLES)]
        total = sum(len(os.listdir(slot_dir(i, r))) for i in slots for r in ROLES if os.path.isdir(slot_dir(i, r)))
        JOB.update(total=total, done=0)
        base = {"n": 0}
        for i in slots:
            def prog(done, tot, base=base):
                JOB["done"] = base["n"] + done
            n_in = sum(len(os.listdir(slot_dir(i, r))) for r in ROLES if os.path.isdir(slot_dir(i, r)))
            vendors.append(C.run_vendor(slot_dir(i), workers=4, progress=prog, name=NAMES[i]))
            base["n"] += n_in; JOB["done"] = base["n"]
        JOB["result"] = serialize(vendors)
        JOB["status"] = "done"
    except Exception as e:  # 失敗は隠さず画面に出す
        JOB["status"] = "error"; JOB["msg"] = f"{type(e).__name__}: {e}"
    JOB["sec"] = round(time.time() - JOB["t0"])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if not os.path.exists(p): p = os.path.join(HERE, "index.html")
            return self._send(200, open(p, "rb").read(), "text/html; charset=utf-8")
        if u.path == "/api/state": return self._send(200, state())
        if u.path == "/api/result": return self._send(200, {"status": JOB["status"], "result": JOB["result"], "msg": JOB["msg"], "sec": JOB["sec"]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        try:
            if u.path == "/api/upload":
                i, role = int(q["slot"]), q["role"]
                assert 1 <= i <= NSLOT and role in ROLES
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です。終わってからにしてください"})
                d = slot_dir(i, role); os.makedirs(d, exist_ok=True)
                name = safe_name(q.get("name", "file")); p = os.path.join(d, name); k = 1
                while os.path.exists(p):
                    b, e = os.path.splitext(name); p = os.path.join(d, f"{b}({k}){e}"); k += 1
                open(p, "wb").write(body)
                if JOB["status"] in ("done", "error"): JOB.update(status="idle", result=None)   # 書類が変わったら、前の結果は消す
                return self._send(200, {"ok": True})
            if u.path == "/api/remove":
                i, role = int(q["slot"]), q["role"]; p = os.path.join(slot_dir(i, role), safe_name(q["name"]))
                if os.path.exists(p): os.remove(p)
                JOB.update(status="idle", result=None) if JOB["status"] != "running" else None
                return self._send(200, {"ok": True})
            if u.path == "/api/name":
                i = int(q["slot"]); NAMES[i] = (q.get("name") or NAMES[i])[:20]; return self._send(200, {"ok": True})
            if u.path == "/api/run":
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です"})
                JOB.update(status="running", done=0, total=0, msg="", result=None, t0=time.time(), sec=0)
                threading.Thread(target=run_job, daemon=True).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/reset":   # リセット: 書類も結果も、全部消す
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です"})
                if "slot" in q: shutil.rmtree(slot_dir(int(q["slot"])), ignore_errors=True)
                else: shutil.rmtree(WORK, ignore_errors=True)
                JOB.update(status="idle", done=0, total=0, msg="", result=None, sec=0)
                if "slot" not in q:
                    for i in NAMES: NAMES[i] = f"{'ABCD'[i-1]}社"
                return self._send(200, {"ok": True})
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})


def start(port=0):
    os.makedirs(WORK, exist_ok=True)
    shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK, exist_ok=True)   # 起動時にも前回の書類は消す(覚えない)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    srv, port = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8780)
    print(f"http://127.0.0.1:{port}  作業フォルダ: {WORK}", flush=True)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass
