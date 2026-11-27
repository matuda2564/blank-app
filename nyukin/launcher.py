# -*- coding: utf-8 -*-
"""入金確認ツールの入口(.exe の中身)。裏でサーバーを起動し、専用の窓(なければブラウザ)で開く。
  --selftest <出力ファイル> : 画面を出さずに、部品が動くか自己点検する(ビルド後の確認用)
  --smoke <出力ファイル>    : 画面を出さずに、サーバーごと通しで動かす(読み込み・検索・合計・リセット)
"""
import os, sys, threading, webbrowser, time, io, traceback

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def selftest(outpath):
    out = []; ok = True
    try:
        import fitz, PIL
        out.append(f"PyMuPDF {fitz.__doc__.split(chr(10))[0][:40] if fitz.__doc__ else ''}")
        import nk_core as N
        assert N.norm_name("ｶ)ｷﾖｳｾﾗ") == N.norm_name("キョウセラ株式会社") == N.norm_name("きょうせら"), "名義のそろえ方"
        assert N.parse_date("2026/11/05") == "2026-11-05" and N.parse_date("R8.11.5") == "2026-11-05" and N.parse_date("11/5", 2026) == "2026-11-05", "日付"
        assert N.to_amount("1,234,567") == 1234567 and N.to_amount("△500") == -500, "金額"
        out.append("読み取りの部品: OK")
    except Exception:
        ok = False; out.append("NG " + traceback.format_exc())
    open(outpath, "w", encoding="utf-8").write(("SELFTEST OK\n" if ok else "SELFTEST NG\n") + "\n".join(out) + "\n")
    return 0 if ok else 1


def smoke(outpath):
    """サーバーごと通しで試す(見本のPDFを作って、読み込み→検索→合計→リセット)"""
    out = []; ok = True
    def chk(name, cond, extra=""):
        nonlocal ok
        out.append(("OK  " if cond else "NG  ") + name + ("" if cond else f"  {extra}"))
        if not cond: ok = False
    try:
        import tempfile, urllib.request, urllib.parse, json
        import fitz
        os.environ["NK_WORK"] = os.path.join(tempfile.mkdtemp(prefix="nk_smoke_"), "作業")
        import server
        srv, port = server.start(0)
        base = f"http://127.0.0.1:{port}"
        def call(path, method="GET", body=None):
            req = urllib.request.Request(base + path, data=body, method=method, headers={"X-NK-Token": server.TOKEN})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                try: return r.status, json.loads(raw)
                except Exception: return r.status, raw
        # 見本の入出金リスト
        d = fitz.open(); pg = d.new_page(width=595, height=842)
        for x, h in ((40, "日付"), (110, "摘要"), (330, "お支払金額"), (410, "お預り金額"), (495, "残高")): pg.insert_text((x, 90), h, fontname="japan", fontsize=10)
        rows = [("2026/11/01", "ﾌﾘｺﾐ ｶ)ｷﾖｳｾﾗ", 0, 10000, 110000), ("2026/11/02", "ﾌﾘｺﾐ ｱｵｷ", 0, 5000, 115000), ("2026/11/03", "ﾌﾘｺﾐ ｷﾖｳｾﾗ(ｶ", 0, 20000, 135000), ("2026/11/04", "ｼﾊﾗｲ ｻﾝﾌﾟﾙ", 3000, 0, 132000)]
        y = 112
        for dt, nm, pay, inc, bal in rows:
            pg.insert_text((40, y), dt, fontname="helv", fontsize=9); pg.insert_text((110, y), nm, fontname="japan", fontsize=9)
            for xr, v in ((385, pay), (465, inc), (555, bal)):
                if v: t = f"{v:,}"; pg.insert_text((xr - fitz.get_text_length(t, "helv", 9), y), t, fontname="helv", fontsize=9)
            y += 17
        pdfb = d.tobytes()
        chk("PDFを入れられる", call("/api/upload?name=" + urllib.parse.quote("入出金.pdf"), "POST", pdfb)[0] == 200)
        chk("読み込みを始められる", call("/api/load", "POST", b"")[0] == 200)
        for _ in range(100):
            st = call("/api/state")[1]
            if st["job"]["status"] != "running": break
            time.sleep(0.3)
        chk("読み込みが終わり、4行を読み取る", st["job"]["status"] == "done" and st["total_rows"] == 4, st)
        chk("残高の検算が、全部合う", (st["docs"][0].get("balance") or {}).get("ng") == 0, st["docs"][0].get("balance"))
        r = call("/api/search?q=" + urllib.parse.quote("キョウセラ"))[1]
        chk("名前で探すと、書き方の違う2件が見つかり、入金の合計が30,000円", len(r["rows"]) == 2 and sum(x["in"] or 0 for x in r["rows"]) == 30000, r["rows"])
        chk("元のページの画像が出る", call(f"/api/page?doc=0&p=1")[0] == 200)
        call("/api/reset", "POST", b"")
        st = call("/api/state")[1]
        chk("リセットで、読み込んだデータが、全部消える", st["total_rows"] == 0 and not st["files"], st)
    except Exception:
        ok = False; out.append("NG 例外: " + traceback.format_exc())
    open(outpath, "w", encoding="utf-8").write(("SMOKE OK\n" if ok else "SMOKE NG\n") + "\n".join(out) + "\n")
    return 0 if ok else 1


def find_appbrowser():
    """Edge または Chrome を探す(Windowsには、Edgeが、必ず入っている)。専用の窓(アドレス欄・タブなし)で開くために使う"""
    env = os.environ.get("NK_APPBROWSER")
    if env: return env if os.path.exists(env) else None
    pf = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA") if os.environ.get(k)]
    for base in pf:
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = os.path.join(base, rel)
            if os.path.exists(p): return p
    return None


def open_app_window(url, server_mod, nk_core):
    """EdgeやChromeを、アプリ用の窓(--app)で開き、その窓が閉じられるまで待つ。戻り値: 窓を開いて、閉じられたら True / 開けなかったら False"""
    exe = find_appbrowser()
    if not exe: return False
    import subprocess, tempfile
    prof = tempfile.mkdtemp(prefix="nk_win_", dir=server_mod.WORK if os.path.isdir(server_mod.WORK) else None)   # 専用の置き場(閉じたら、このプロセスも終わる)
    try:
        t0 = time.time()
        pr = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1280,860", "--no-first-run", "--no-default-browser-check"])
        nk_core.log(f"アプリ用の窓で開いた: {exe}")
        pr.wait()
        if time.time() - t0 < 5:   # すぐ終わった=別のプロセスに任せて終わった/開けなかった → 窓を開けた扱いにしない
            nk_core.log("アプリ用の窓が、すぐ終わった(開けなかった可能性)"); return False
        return True
    except Exception:
        nk_core.log("アプリ用の窓を開けなかった: " + traceback.format_exc()); return False
    finally:
        import shutil; shutil.rmtree(prof, ignore_errors=True)


def main():
    import server
    import nk_core
    srv, port = server.start(0)
    url = server.url(port)
    reason = ""
    try:
        import webview
        webview.create_window("入金確認", url, width=1280, height=860)
        webview.start()
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        nk_core.log("専用の窓(WebView2)を開けなかった: " + traceback.format_exc())   # 黙って握りつぶさない
        if open_app_window(url, server, nk_core):   # 次の手: Edge/Chrome を、アプリ用の窓で開く(タブ・アドレス欄なし。窓を閉じたら、終了)
            import shutil
            shutil.rmtree(server.WORK, ignore_errors=True)
            return
        nk_core.log("ブラウザで開く")
        webbrowser.open(url)
        idle = int(os.environ.get("NK_IDLE_EXIT") or 180)   # 画面(ブラウザ)が、この秒数、開かれていなければ、自動で終了する
        try:
            import tkinter
            r = tkinter.Tk(); r.title("入金確認"); r.geometry("560x300")
            tkinter.Label(r, text="専用の窓を開けなかったため、ブラウザで画面を開きました。使い方は、同じです。\n\n■ この小さい窓は、閉じても(最小化しても)大丈夫です。アプリは、動き続けます。\n■ 終わるときは、下の「終了する」を押すか、ブラウザの画面を全部閉じて、3分待ってください。\n■ ブラウザの画面が消えたときは、「画面を開き直す」を押してください。\n   (古い画面や、ブックマークは、使えません)\n\n理由(開発者に伝えてください):\n" + reason[:160], pady=10, wraplength=520, justify="left").pack()
            bf = tkinter.Frame(r); bf.pack(pady=6)
            tkinter.Button(bf, text="画面を開き直す", command=lambda: webbrowser.open(url)).pack(side="left", padx=8)
            tkinter.Button(bf, text="終了する(書類も消えます)", command=r.destroy).pack(side="left", padx=8)
            r.protocol("WM_DELETE_WINDOW", r.iconify)   # ×を押しても終了しない(うっかり閉じて、画面が使えなくなるのを防ぐ)
            def tick():
                if time.time() - server.LAST_SEEN[0] > idle: r.destroy(); return
                r.after(3000, tick)
            r.after(3000, tick); r.mainloop()
        except Exception:
            while time.time() - server.LAST_SEEN[0] <= idle: time.sleep(2)   # 窓が出せないときも、画面が開かれている間は、動き続ける
    import shutil
    shutil.rmtree(server.WORK, ignore_errors=True)   # 終了時に、作業中の書類を全部消す


def windowcheck(outpath):
    """専用の窓が、このパソコン(ビルド機)で、本当に、開いて、画面を表示できるか(--windowcheck <出力ファイル>)"""
    import threading
    res = {"ok": False, "title": None, "err": ""}
    try:
        import server
        srv, port = server.start(0)
        import webview
        w = webview.create_window("check", server.url(port), width=900, height=600)
        def later():
            try:
                time.sleep(7)
                res["title"] = w.evaluate_js("document.title")
                res["ok"] = bool(res["title"])
            except Exception:
                res["err"] += traceback.format_exc()
            finally:
                try: w.destroy()
                except Exception: pass
        webview.start(later)
    except Exception:
        res["err"] += traceback.format_exc()
    open(outpath, "w", encoding="utf-8").write(("WINDOW OK\n" if res["ok"] else "WINDOW NG\n") + f"title={res['title']}\n{res['err']}\n")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    if len(sys.argv) >= 3 and sys.argv[1] == "--smoke":
        sys.exit(smoke(sys.argv[2]))
    if len(sys.argv) >= 3 and sys.argv[1] == "--windowcheck":
        sys.exit(windowcheck(sys.argv[2]))
    main()
