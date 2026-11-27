# -*- coding: utf-8 -*-
"""見積・発注 照合ツールの本体(パソコンの中だけで動く小さなサーバー+操作画面)
・127.0.0.1(自分のパソコンだけ)で待ち受ける。外のネットには何も送らない。
・書類は作業フォルダに置き、リセットで全部消す。過去の書類を覚えない。
"""
import os, sys, json, re, shutil, threading, time, base64, io, urllib.parse, tempfile, secrets, traceback, faulthandler
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_core as C

VERSION = "v1"
try:   # ビルド番号(どの版を使っているか、画面で分かるように)
    with open(os.path.join(HERE, "BUILD.txt"), encoding="utf-8") as _f: VERSION = "build " + _f.read().strip()
except Exception:
    pass
TOKEN = secrets.token_urlsafe(18)   # 起動ごとに変わる合言葉。これを持たない要求は断る(他のサイトからの勝手な操作を防ぐ)
PORT = {"n": 0}
LAST_SEEN = [time.time()]   # 画面(ブラウザ)が、最後に、問い合わせてきた時刻。画面が開いているかを知るため
WORK_BASE = os.environ.get("MC_WORK") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "見積照合_作業フォルダ")
WORK = WORK_BASE   # start() で、このアプリ専用の場所(run_<番号>)に切り替わる
MAX_UPLOAD = 300 * 1024 * 1024   # 1ファイルの上限(300MB)
NSLOT = 4
ROLES = ("見積", "発注")
MODES = C.ROLES_BY_MODE   # qo=見積⇔発注 / qq=見積⇔見積(客先と仕入先)


def _mode(q):
    m = (q or {}).get("mode", "qo")
    if m not in MODES: raise ValueError("使い方の指定が違います")
    return m
LOCK = threading.Lock()
JOB = {"status": "idle", "done": 0, "total": 0, "msg": "", "result": None, "t0": 0, "sec": 0, "gen": 0, "ev": None}
HEALTH = {"state": "checking"}
NAMES = {m: {i: f"{'ABCD'[i-1]}社" for i in range(1, NSLOT + 1)} for m in MODES}


def slot_dir(i, role=None):
    p = os.path.join(WORK, f"slot{i}")
    return os.path.join(p, role) if role else p


def safe_name(n):
    n = os.path.basename(n.replace("\\", "/"))
    n = re.sub(r'[<>:"|?*\x00-\x1f]', "_", n).strip(" .")
    return n or "file"


def state(mode="qo"):
    slots = []
    for i in range(1, NSLOT + 1):
        d = {"id": i, "name": NAMES[mode][i]}
        for r in MODES[mode]:
            p = slot_dir(i, r)
            d[r] = sorted(os.listdir(p)) if os.path.isdir(p) else []
        slots.append(d)
    j = {k: JOB[k] for k in ("status", "done", "total", "msg", "sec")}; j["mode"] = JOB.get("mode")
    if JOB["status"] == "running": j["sec"] = round(time.time() - JOB["t0"])
    return {"version": VERSION, "slots": slots, "job": j}


def unsupported_msg(ext):
    hint = {".xls": "古い形式のExcelです。Excelで開いて、「名前を付けて保存」から「.xlsx」で保存し直してください",
            ".doc": "Wordの書類は、読めません。PDFにして、入れてください", ".docx": "Wordの書類は、読めません。PDFにして、入れてください",
            ".heic": "iPhoneのHEIC画像は、読めません。JPEGで保存し直してください", ".csv": "CSVは、読めません。Excel(.xlsx)で保存し直してください",
            ".zip": "zipは、解凍してから、中のファイルを入れてください"}.get(ext)
    return f"この形式は、読めません({ext or '拡張子なし'})。" + (hint + "。" if hint else "") + "対応: PDF・Excel(.xlsx)・画像(png / jpg / tif / bmp / webp)"


def desktop_dir():
    for c in (os.path.join(os.environ.get("OneDrive") or "", "Desktop"), os.path.join(os.path.expanduser("~"), "Desktop"), os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")):
        if c and os.path.isdir(c): return c
    return os.path.expanduser("~")


def save_html_to_desktop(body):
    d = desktop_dir()
    p = os.path.join(d, "照合結果_" + time.strftime("%Y%m%d_%H%M%S") + ".html")
    with open(p, "wb") as f: f.write(body)
    return p


def _pid_alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h: return False
            ctypes.windll.kernel32.CloseHandle(h); return True
        os.kill(pid, 0); return True
    except PermissionError:
        return True
    except Exception:
        return False


def clean_old_runs():
    """前に動いていた(もう閉じた)アプリの作業フォルダを消す。動いている別のアプリの分は、消さない"""
    try:
        for n in os.listdir(WORK_BASE):
            if n.startswith("run_"):
                try: pid = int(n[4:])
                except ValueError: continue
                if pid != os.getpid() and not _pid_alive(pid):
                    shutil.rmtree(os.path.join(WORK_BASE, n), ignore_errors=True)
    except Exception:
        pass


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
        sv = {"vendor": v["vendor"], "slot": v.get("slot"), "warnings": v["warnings"], "pairs": [], "seconds": round(v.get("seconds", 0), 1),
              "count": {"OK": 0, "OK_IMG": 0, "DIFF": 0, "REVIEW": 0, "NOORDER": 0, "NOPAIR": 0}}
        for p in v["pairs"]:
            q, o = p["q"], p["o"]
            sp = {"q": q.name if q else None, "o": o.name if o else None, "why": p["why"], "compared": p["compared"], "skipped": p["skipped"], "skipnote": p.get("skipnote"), "qread": C.doc_info(q, p.get("mode", "qo")), "oread": C.doc_info(o, p.get("mode", "qo")), "rows": []}
            if q and o and p.get("mode", "qo") == "qo":
                st_, dt_ = C.quote_check(q, o); sp["refcheck"] = {"st": st_, "detail": dt_}
            if not (q and o):
                sv["count"]["NOPAIR"] += 1
            for r in p["results"]:
                sv["count"][r["status"]] += 1
                row = {"status": r["status"], "model": r["model"], "msg": r["msg"], "checks": [list(c) for c in r["checks"]] if r.get("checks") else None}
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
                    for sd, dd, rr in (("q", q, r["qrow"]), ("o", o, r["orow"])):
                        if dd and not rr and not r.get("doc") and dd.pages and not row.get(sd + "page"):
                            row[sd + "page"] = page_id(dd, 0); row[sd + "nomatch"] = True   # 対応する行が見つからなかった側は、書類を、そのまま見せる
                    row["docflag"] = r.get("doc")
                sp["rows"].append(row)
            sv["pairs"].append(sp)
        out.append(sv)
    return {"vendors": out, "pages": pages}


def run_job(mode="qo", with_qty=False, gen=0, ev=None):
    cancel = ev.is_set if ev else None
    cur = lambda: JOB["gen"] == gen    # 中止されたあと、古い仕事が、画面の状態を上書きしない
    try:
        vendors = []
        RL = MODES[mode]
        slots = [i for i in range(1, NSLOT + 1) if any(os.path.isdir(slot_dir(i, r)) and os.listdir(slot_dir(i, r)) for r in RL)]
        total = sum(len(os.listdir(slot_dir(i, r))) for i in slots for r in RL if os.path.isdir(slot_dir(i, r)))
        if cur(): JOB.update(total=total, done=0)
        base = {"n": 0}
        for i in slots:
            def prog(done, tot, base=base):
                if cur(): JOB["done"] = base["n"] + done
            n_in = sum(len(os.listdir(slot_dir(i, r))) for r in RL if os.path.isdir(slot_dir(i, r)))
            vendors.append(C.run_vendor(slot_dir(i), workers=C.auto_workers(), progress=prog, name=NAMES[mode][i], mode=mode, with_qty=with_qty, cancel=cancel))
            vendors[-1]["slot"] = i
            base["n"] += n_in
            if cur(): JOB["done"] = base["n"]
        if mode == "qo":
            try: C.cross_hints(vendors)
            except Exception as e: C.log(f"cross_hints失敗: {e!r}")
        res = serialize(vendors)
        if cur(): JOB["result"] = res; JOB["status"] = "done"
    except C.Cancelled:
        C.log("照合を中止した")
    except BaseException as e:  # 失敗は隠さず画面に出す(ログにも残す)
        C.log("照合が失敗: " + traceback.format_exc())
        if cur(): JOB["status"] = "error"; JOB["msg"] = f"{type(e).__name__}: {e}"
    if cur(): JOB["sec"] = round(time.time() - JOB["t0"])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _guard(self, page):
        """守りの検査。①宛先(Host)が自分のパソコン向けか(DNSリバインディング対策) ②他のサイトから来た要求(Origin)でないか ③合言葉があるか"""
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{PORT['n']}", f"localhost:{PORT['n']}"):
            self._send(403, {"error": "宛先が違います"}); return False
        org = self.headers.get("Origin")
        if org and org not in (f"http://127.0.0.1:{PORT['n']}", f"http://localhost:{PORT['n']}"):
            self._send(403, {"error": "他のサイトからの操作は受け付けません"}); return False
        u = urllib.parse.urlparse(self.path)
        tok = self.headers.get("X-MC-Token") or dict(urllib.parse.parse_qsl(u.query)).get("t")
        if not tok or not secrets.compare_digest(tok, TOKEN):
            self._send(403, {"error": "合言葉がありません。アプリから開き直してください"}); return False
        LAST_SEEN[0] = time.time()
        return True

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if not self._guard(True): return
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if not os.path.exists(p): p = os.path.join(HERE, "index.html")
            return self._send(200, open(p, "rb").read(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            try: return self._send(200, state(_mode(dict(urllib.parse.parse_qsl(u.query)))))
            except ValueError as e: return self._send(400, {"error": str(e)})
        if u.path == "/api/health": return self._send(200, HEALTH)
        if u.path == "/api/result": return self._send(200, {"status": JOB["status"], "result": JOB["result"], "msg": JOB["msg"], "sec": JOB["sec"], "mode": JOB.get("mode")})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guard(False): return
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self._send(413, {"error": f"ファイルが大きすぎます({n // 2**20}MB)。{MAX_UPLOAD // 2**20}MBまでです"})
        body = self.rfile.read(n) if n else b""
        try:
            if u.path == "/api/upload":
                mode = _mode(q); i, role = int(q["slot"]), q["role"]
                assert 1 <= i <= NSLOT and role in MODES[mode]
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です。終わってからにしてください(止まっているときは「中止」を押してください)"})
                name = safe_name(q.get("name", "file")); ext = os.path.splitext(name)[1].lower()
                if ext not in C.EXTS: return self._send(400, {"error": unsupported_msg(ext)})
                if not body: return self._send(400, {"error": "中身が空のファイルです(フォルダを入れていませんか。ファイルを入れてください)"})
                d = slot_dir(i, role); os.makedirs(d, exist_ok=True)
                p = os.path.join(d, name); k = 1
                while os.path.exists(p):
                    b, e = os.path.splitext(name); p = os.path.join(d, f"{b}({k}){e}"); k += 1
                try:
                    open(p, "wb").write(body)
                except OSError as e:
                    try: os.remove(p)
                    except OSError: pass
                    return self._send(507, {"error": f"保存できませんでした(パソコンの空き容量が、足りないかもしれません): {e.strerror or e}"})
                if JOB["status"] in ("done", "error"): JOB.update(status="idle", result=None)   # 書類が変わったら、前の結果は消す
                return self._send(200, {"ok": True})
            if u.path == "/api/remove":
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です。終わってからにしてください"})
                mode = _mode(q); i, role = int(q["slot"]), q["role"]
                assert 1 <= i <= NSLOT and role in MODES[mode]
                p = os.path.join(slot_dir(i, role), safe_name(q["name"]))
                if os.path.exists(p): os.remove(p)
                JOB.update(status="idle", result=None) if JOB["status"] != "running" else None
                return self._send(200, {"ok": True})
            if u.path == "/api/name":
                mode = _mode(q); i = int(q["slot"]); assert 1 <= i <= NSLOT
                NAMES[mode][i] = (q.get("name") or NAMES[mode][i])[:20]; return self._send(200, {"ok": True})
            if u.path == "/api/run":
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です"})
                mode = _mode(q); wq = q.get("qty") == "1"
                JOB["gen"] += 1; ev = threading.Event()
                JOB.update(status="running", done=0, total=0, msg="", result=None, t0=time.time(), sec=0, mode=mode, ev=ev)
                threading.Thread(target=run_job, args=(mode, wq, JOB["gen"], ev), daemon=True).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/move":   # 見積⇔発注の枠に入れた「見積同士」の書類を、見積⇔見積の枠へ移す(見積の枠→客先見積 / 発注の枠→仕入先見積)
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です。終わってからにしてください"})
                i = int(q["slot"]); assert 1 <= i <= NSLOT
                for a, b in (("見積", "客先見積"), ("発注", "仕入先見積")):
                    src, dst = slot_dir(i, a), slot_dir(i, b)
                    if not os.path.isdir(src): continue
                    os.makedirs(dst, exist_ok=True)
                    for fn in os.listdir(src):
                        t = os.path.join(dst, fn); k = 1
                        while os.path.exists(t):
                            bs, ex = os.path.splitext(fn); t = os.path.join(dst, f"{bs}({k}){ex}"); k += 1
                        shutil.move(os.path.join(src, fn), t)
                JOB.update(status="idle", done=0, total=0, msg="", result=None, sec=0)
                return self._send(200, {"ok": True})
            if u.path == "/api/cancel":   # 中止: 読み取りが止まったときも、これで、元に戻れる
                if JOB["status"] == "running":
                    JOB["gen"] += 1
                    if JOB.get("ev"): JOB["ev"].set()
                    JOB.update(status="idle", done=0, total=0, msg="", result=None, sec=0)
                    C.log("利用者が中止を押した")
                return self._send(200, {"ok": True})
            if u.path == "/api/savehtml":   # 結果をデスクトップに保存する(専用の窓では、ダウンロードが動かないことがあるため)
                return self._send(200, {"ok": True, "path": save_html_to_desktop(body)})
            if u.path == "/api/reset":   # リセット: その使い方の、書類も結果も、消す(もう一方の使い方の書類は、消さない)
                if JOB["status"] == "running": return self._send(409, {"error": "照合中です"})
                mode = _mode(q)
                slots = [int(q["slot"])] if "slot" in q else list(range(1, NSLOT + 1))
                for i in slots:
                    assert 1 <= i <= NSLOT
                    for r in MODES[mode]: shutil.rmtree(slot_dir(i, r), ignore_errors=True)
                JOB.update(status="idle", done=0, total=0, msg="", result=None, sec=0)
                if "slot" not in q:
                    for i in NAMES[mode]: NAMES[mode][i] = f"{'ABCD'[i-1]}社"
                return self._send(200, {"ok": True})
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})


def setup_log():
    """落ちたとき、原因を調べられるように、ログを残す。書類の中身は、書かない。場所: 作業フォルダの1つ上の mitsumori_check_log.txt"""
    try:
        C.LOGFILE = os.path.join(os.path.dirname(WORK_BASE), "mitsumori_check_log.txt")
        if os.path.exists(C.LOGFILE) and os.path.getsize(C.LOGFILE) > 2_000_000:
            os.replace(C.LOGFILE, C.LOGFILE + ".old")
        C.log(f"=== 起動 {VERSION} / 空きメモリ {C.avail_mb()}MB / コア {os.cpu_count()} / 同時に読む数 {C.auto_workers()} ===")
        fh = open(C.LOGFILE, "a", encoding="utf-8")
        faulthandler.enable(file=fh, all_threads=True)   # アプリが強制的に落ちたときも、その場所を残す
        sys.excepthook = lambda a, b, c: C.log("想定外の例外: " + "".join(traceback.format_exception(a, b, c)))
        threading.excepthook = lambda args: C.log("スレッドの例外: " + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    except Exception:
        pass


def start(port=0):
    global WORK
    os.makedirs(WORK_BASE, exist_ok=True)
    WORK = os.path.join(WORK_BASE, f"run_{os.getpid()}")   # このアプリ専用。2つ開いても、互いの書類を消さない
    setup_log()
    clean_old_runs()
    shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK, exist_ok=True)   # 起動時にも、前回の書類は消す(覚えない)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    PORT["n"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    def _health():
        try:
            HEALTH.clear(); HEALTH.update(state="checking"); r = C.health(); HEALTH.clear(); HEALTH.update(state="done", **r)
            C.log(f"自己診断: {HEALTH}")
        except Exception as e:
            HEALTH.clear(); HEALTH.update(state="done", ok=False, rapid=False, tess=False, msg=f"{type(e).__name__}: {e}")
    threading.Thread(target=_health, daemon=True).start()
    return srv, srv.server_address[1]


def url(port):
    return f"http://127.0.0.1:{port}/?t={TOKEN}"


if __name__ == "__main__":
    srv, port = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8780)
    print(f"{url(port)}  作業フォルダ: {WORK}", flush=True)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass
