# -*- coding: utf-8 -*-
"""入金確認ツールの本体(パソコンの中だけで動く小さなサーバー+操作画面)
・127.0.0.1(自分のパソコンだけ)で待ち受ける。外のネットには何も送らない。
・読み込んだPDFと、読み取った中身は、メモリと作業フォルダにだけ置き、「データを消して次の月へ」と、終了時に、全部消す。
"""
import os, sys, json, re, shutil, threading, time, base64, io, urllib.parse, secrets, traceback, faulthandler
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nk_core as N

VERSION = "v1"
try:
    with open(os.path.join(HERE, "BUILD.txt"), encoding="utf-8") as _f: VERSION = "build " + _f.read().strip()
except Exception:
    pass
TOKEN = secrets.token_urlsafe(18)
LAST_SEEN = [time.time()]
WORK_BASE = os.environ.get("NK_WORK") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "入金確認_作業フォルダ")
WORK = WORK_BASE
MAX_UPLOAD = 500 * 1024 * 1024
SETTINGS = os.environ.get("NK_SETTINGS") or os.path.join(os.path.dirname(WORK_BASE), "入金確認_設定")   # 登録のお客さんの一覧は、ここに残す(「データを消して次の月へ」では、消えない)
LAST = {"result": None}
LOCK = threading.Lock()
DOCS = []            # 読み取り済みの書類(名前・行の一覧・検算の結果)
JOB = {"status": "idle", "done": 0, "total": 0, "msg": "", "gen": 0, "ev": None, "sec": 0, "t0": 0}


def safe_name(n):
    n = os.path.basename(n.replace("\\", "/"))
    n = re.sub(r'[<>:"|?*\x00-\x1f]', "_", n).strip(" .")
    return n or "file"


def files():
    d = os.path.join(WORK, "pdf")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def customers_file():
    return os.path.join(SETTINGS, "お客さんの一覧.json")


def load_customers():
    try:
        return json.load(open(customers_file(), encoding="utf-8")).get("entries", [])
    except Exception:
        return []


def save_customers(entries):
    os.makedirs(SETTINGS, exist_ok=True)
    tmp = customers_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump({"entries": entries, "saved": time.strftime("%Y-%m-%d %H:%M")}, f, ensure_ascii=False)
    os.replace(tmp, customers_file())


def desktop_dir():
    env = os.environ.get("NK_DESKTOP")
    if env and os.path.isdir(env): return env
    for c in (os.path.join(os.environ.get("OneDrive") or "", "Desktop"), os.path.join(os.path.expanduser("~"), "Desktop"), os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")):
        if c and os.path.isdir(c): return c
    return os.path.expanduser("~")


def doc_public(d):
    return {"name": d["name"], "pages": d["pages"], "kind": d.get("kind", "pdf"), "rows": len(d["rows"]), "no_balance": bool(d.get("no_balance")), "csv": d.get("csv"),
            "in_total": sum(r["in"] or 0 for r in d["rows"]), "out_total": sum(r["out"] or 0 for r in d["rows"]), "notes": d["notes"], "balance": d.get("balance"), "order": d["order"], "text_pages": d["text_pages"],
            "page_stats": d["page_stats"], "range": [min((r["date"] for r in d["rows"]), default=None), max((r["date"] for r in d["rows"]), default=None)]}


def state():
    j = {k: JOB[k] for k in ("status", "done", "total", "msg")}
    j["sec"] = round(time.time() - JOB["t0"]) if JOB["status"] == "running" else JOB["sec"]
    return {"version": VERSION, "files": files(), "docs": [doc_public(d) for d in DOCS], "job": j, "total_rows": sum(len(d["rows"]) for d in DOCS)}


def run_job(gen, ev):
    global DOCS
    try:
        names = files(); total = 0; done = 0
        import fitz
        pc = {}
        for n in names:
            try: pc[n] = 1 if n.lower().endswith(".csv") else len(fitz.open(os.path.join(WORK, "pdf", n)))
            except Exception: pc[n] = 0
        total = sum(pc.values()) or 1
        new = []
        for n in names:
            if ev.is_set(): return
            base = done
            def prog(p, t, base=base):
                if JOB["gen"] == gen: JOB.update(done=base + p, total=total, msg=f"{n} の {p}/{t} ページを読んでいます")
            try:
                new.append((N.read_csv if n.lower().endswith(".csv") else N.read_pdf)(os.path.join(WORK, "pdf", n), n, prog, ev))
            except Exception as e:
                N.log(f"PDFを読めなかった: {n}: {traceback.format_exc()}")
                new.append({"name": n, "path": "", "pages": 0, "rows": [], "notes": [f"このPDFを読めませんでした({type(e).__name__}: {e})。壊れているか、パスワード付きかもしれません"], "page_stats": [], "order": "asc", "swapped": False, "text_pages": 0})
            done += pc.get(n, 0)
        if ev.is_set() or JOB["gen"] != gen: return
        with LOCK: DOCS = new
        JOB.update(status="done", done=total, total=total, msg="", sec=round(time.time() - JOB["t0"]))
    except Exception:
        N.log("読み込みの失敗: " + traceback.format_exc())
        JOB.update(status="error", msg="読み込みに失敗しました(ログに残しました)")


def page_png(doc_i, pno, box=None):
    import fitz
    d = DOCS[doc_i]
    if d.get("kind") == "csv": raise ValueError("CSVには、元のページがありません")
    pdf = fitz.open(d["path"]); pg = pdf[pno - 1]
    pix = pg.get_pixmap(dpi=110)
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    if box:
        dr = ImageDraw.Draw(im); W, H = im.size
        dr.rectangle([box[0] * W - 4, box[1] * H - 3, box[2] * W + 4, box[3] * H + 3], outline=(220, 40, 40), width=3)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass

    def _guard(self):
        u = urllib.parse.urlparse(self.path)
        tok = self.headers.get("X-NK-Token") or dict(urllib.parse.parse_qsl(u.query)).get("t")
        if not tok or not secrets.compare_digest(tok, TOKEN):
            self._send(403, {"error": "合言葉がありません。アプリから開き直してください"}); return False
        LAST_SEEN[0] = time.time(); return True

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if not self._guard(): return
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
                if not os.path.exists(p): p = os.path.join(HERE, "index.html")
                return self._send(200, open(p, "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/state": return self._send(200, state())
            if u.path == "/api/customers": return self._send(200, {"entries": load_customers(), "file": customers_file()})
            if u.path == "/api/search":
                with LOCK: docs = list(DOCS)
                return self._send(200, N.search(docs, q.get("q", "")))
            if u.path == "/api/page":
                box = [float(x) for x in q["box"].split(",")] if q.get("box") else None
                return self._send(200, page_png(int(q["doc"]), int(q["p"]), box), "image/png")
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guard(): return
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD: return self._send(413, {"error": f"ファイルが大きすぎます({n // 2**20}MB)"})
        body = self.rfile.read(n) if n else b""
        global DOCS
        try:
            if u.path == "/api/upload":
                if JOB["status"] == "running": return self._send(409, {"error": "読み込み中です。終わってからにしてください"})
                name = safe_name(q.get("name", "file.pdf"))
                if not name.lower().endswith((".pdf", ".csv")): return self._send(400, {"error": "PDFかCSVを入れてください(銀行からダウンロードした、入出金のリストです)"})
                if not body: return self._send(400, {"error": "中身が空のファイルです"})
                d = os.path.join(WORK, "pdf"); os.makedirs(d, exist_ok=True)
                p = os.path.join(d, name); k = 1
                while os.path.exists(p):
                    b, e = os.path.splitext(name); p = os.path.join(d, f"{b}({k}){e}"); k += 1
                open(p, "wb").write(body)
                if JOB["status"] in ("done", "error"): JOB.update(status="idle")
                return self._send(200, {"ok": True})
            if u.path == "/api/remove":
                if JOB["status"] == "running": return self._send(409, {"error": "読み込み中です"})
                p = os.path.join(WORK, "pdf", safe_name(q["name"]))
                if os.path.exists(p): os.remove(p)
                if JOB["status"] != "running": JOB.update(status="idle")
                with LOCK: DOCS = []
                return self._send(200, {"ok": True})
            if u.path == "/api/load":
                if JOB["status"] == "running": return self._send(409, {"error": "読み込み中です"})
                if not files(): return self._send(400, {"error": "PDFが入っていません"})
                JOB["gen"] += 1; ev = threading.Event()
                JOB.update(status="running", done=0, total=0, msg="", t0=time.time(), sec=0, ev=ev)
                threading.Thread(target=run_job, args=(JOB["gen"], ev), daemon=True).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/recolumns":   # CSVの、列の割り当てを、人が決め直す(名義・日付・入金・出金・残高)
                name = safe_name(q["name"])
                ov = {k: int(q[k]) for k in ("date", "desc", "in", "out", "bal") if k in q}
                with LOCK:
                    for i, d in enumerate(DOCS):
                        if d["name"] == name and d.get("kind") == "csv":
                            DOCS[i] = N.read_csv(d["path"], name, override=ov)
                            return self._send(200, {"ok": True})
                return self._send(404, {"error": "そのCSVは、読み込まれていません"})
            if u.path == "/api/parsenames":   # 貼り付けた文字を、1人ずつに分けた結果(画面の、確認用)
                return self._send(200, {"entries": N.parse_names(body.decode("utf-8", "replace"))})
            if u.path == "/api/customers":   # 登録のお客さんを、置き換える
                ents = N.parse_names(body.decode("utf-8", "replace"))
                save_customers(ents)
                return self._send(200, {"entries": ents})
            if u.path == "/api/customers_clear":
                save_customers([]); return self._send(200, {"entries": []})
            if u.path == "/api/batch":   # お客さんを、まとめて探す(who=registered: 登録の人 / それ以外: 貼り付けた人)
                ents = load_customers() if q.get("who") == "registered" else N.parse_names(body.decode("utf-8", "replace"))
                if not ents: return self._send(400, {"error": "お客さんが、1人も入っていません"})
                with LOCK: docs = list(DOCS)
                if not docs: return self._send(400, {"error": "先に、入出金リストを読み込んでください"})
                res = N.search_many(docs, ents); LAST["result"] = res
                return self._send(200, res)
            if u.path == "/api/savecsv":   # まとめて探した結果を、デスクトップにCSVで保存する(専用の窓では、ダウンロードが動かないことがあるため)
                if not LAST["result"]: return self._send(400, {"error": "保存する結果がありません。先に、まとめて探してください"})
                p = os.path.join(desktop_dir(), "入金一覧_" + time.strftime("%Y%m%d_%H%M%S") + ".csv")
                with open(p, "w", encoding="utf-8-sig", newline="") as f: f.write(N.csv_text(LAST["result"]))
                return self._send(200, {"ok": True, "path": p})
            if u.path == "/api/cancel":
                JOB["gen"] += 1
                if JOB.get("ev"): JOB["ev"].set()
                JOB.update(status="idle", done=0, total=0, msg="")
                return self._send(200, {"ok": True})
            if u.path == "/api/reset":   # 次の月へ: 読み込んだPDFも、読み取った中身も、全部消す
                if JOB["status"] == "running":
                    JOB["gen"] += 1
                    if JOB.get("ev"): JOB["ev"].set()
                with LOCK: DOCS = []
                shutil.rmtree(os.path.join(WORK, "pdf"), ignore_errors=True)
                JOB.update(status="idle", done=0, total=0, msg="", sec=0)
                return self._send(200, {"ok": True})
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})


def clean_old_runs():
    try:
        for n in os.listdir(WORK_BASE):
            p = os.path.join(WORK_BASE, n)
            if n.startswith("run_") and os.path.isdir(p) and n != f"run_{os.getpid()}" and time.time() - os.path.getmtime(p) > 3600: shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def setup_log():
    try:
        N.LOGFILE = os.path.join(os.path.dirname(WORK_BASE), "nyukin_check_log.txt")
        if os.path.exists(N.LOGFILE) and os.path.getsize(N.LOGFILE) > 2_000_000: os.replace(N.LOGFILE, N.LOGFILE + ".old")
        N.log(f"=== 起動 {VERSION} ===")   # 書類の中身(名前・金額)は、ログに書かない
        fh = open(N.LOGFILE, "a", encoding="utf-8"); faulthandler.enable(file=fh, all_threads=True)
        sys.excepthook = lambda a, b, c: N.log("想定外の例外: " + "".join(traceback.format_exception(a, b, c)))
    except Exception:
        pass


def start(port=0):
    global WORK
    os.makedirs(WORK_BASE, exist_ok=True)
    WORK = os.path.join(WORK_BASE, f"run_{os.getpid()}")
    setup_log(); clean_old_runs()
    shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def url(port):
    return f"http://127.0.0.1:{port}/?t={TOKEN}"


if __name__ == "__main__":
    srv, port = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8790)
    print(f"{url(port)}  作業フォルダ: {WORK}", flush=True)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass
