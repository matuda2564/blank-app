# -*- coding: utf-8 -*-
"""見積・発注 照合ツールの入口(.exe の中身)。裏でサーバーを起動し、専用の窓(なければブラウザ)で開く。
  --selftest <出力ファイル> : 画面を出さずに、読み取りの部品が動くか自己点検して結果をファイルへ書く(ビルド後の確認用)
  --smoke <出力ファイル>    : 画面を出さずに、サーバーごと通しで動かす(照合・画像・見積同士・中止・リセット・保存。ビルド後の確認用)
"""
import os, sys, threading, webbrowser, time, io, traceback

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
def _is_ascii(p):
    try:
        p.encode("ascii"); return True
    except UnicodeEncodeError:
        return False


def _ascii_copy_of(src):
    """Tesseract は、日本語の名前の場所に置くと、動かない(Windows)。英数字だけの場所に、コピーして使う。
    初回だけコピーし、2回目からは、コピー済みを使う(アプリが、日本語の名前のフォルダや、日本語のユーザー名の下にあっても、動くように)"""
    import shutil
    for base in (os.environ.get("PUBLIC"), os.environ.get("ProgramData"), r"C:\Users\Public", r"C:\ProgramData"):
        try:
            if not base or not _is_ascii(base) or not os.path.isdir(base): continue
            dst = os.path.join(base, "mitsumori_check_tess")
            stamp = os.path.join(dst, "stamp.txt")
            sig = str(os.path.getsize(os.path.join(src, "tesseract.exe")))
            ok = os.path.exists(stamp) and open(stamp).read() == sig and os.path.exists(os.path.join(dst, "tesseract.exe"))
            if not ok:
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
                open(stamp, "w").write(sig)
            return dst
        except Exception:
            continue
    return None


# 同梱した Tesseract があれば、それを使う(置き場所が日本語の名前なら、英数字だけの場所に、コピーして使う)
_t = os.path.join(BASE, "tesseract")
if os.path.isdir(_t):
    if not _is_ascii(_t):
        _t = _ascii_copy_of(_t) or _t
    exe = os.path.join(_t, "tesseract.exe")
    if os.path.exists(exe):
        os.environ["MC_TESSERACT"] = exe
        os.environ["TESSDATA_PREFIX"] = os.path.join(_t, "tessdata")
    os.environ["PATH"] = _t + os.pathsep + os.environ.get("PATH", "")


def selftest(outpath):
    log = []
    ok = True
    try:
        import tempfile, fitz
        from PIL import Image
        import mc_core as C
        d = tempfile.mkdtemp()
        def mkpdf(p):
            doc = fitz.open(); pg = doc.new_page(width=595, height=842)
            pg.insert_text((200, 70), "御 見 積 書", fontname="japan", fontsize=20)
            pg.insert_text((60, 110), "見積番号: Q-1", fontname="helv", fontsize=11)
            for x, h in ((60, "型式"), (300, "数量"), (370, "単価"), (460, "金額")):
                pg.insert_text((x, 200), h, fontname="japan", fontsize=11)
            for k, (m, q, pr) in enumerate([("AW20-02BE-D", 5, 3400), ("KQ2H06-M5", 20, 180)]):
                y = 226 + 26 * k
                pg.insert_text((60, y), m, fontname="helv", fontsize=11); pg.insert_text((300, y), str(q), fontname="helv", fontsize=11)
                pg.insert_text((370, y), f"{pr:,}", fontname="helv", fontsize=11); pg.insert_text((460, y), f"{q*pr:,}", fontname="helv", fontsize=11)
            pg.insert_text((370, 300), "合計", fontname="japan", fontsize=11); pg.insert_text((460, 300), f"{5*3400+20*180:,}", fontname="helv", fontsize=11)
            doc.save(p); return doc
        qp, op = os.path.join(d, "q.pdf"), os.path.join(d, "o.pdf")
        mkpdf(qp); doc = mkpdf(op)
        a, b = C.read_doc(qp, "見積"), C.read_doc(op, "発注")
        res, _, _ = C.compare(a, b)
        st = sorted(r["status"] for r in res)
        log.append(f"文字入りPDF: 行={len(a.rows)}/{len(b.rows)} 判定={st}")
        ok &= (st == ["OK", "OK"])
        # スキャン画像(OCR 2種類)
        pm = doc[0].get_pixmap(dpi=150); png = os.path.join(d, "s.png"); pm.save(png)
        s = C.read_doc(png, "発注")
        log.append(f"画像: 行={len(s.rows)} 読み取り={[ (r['model_raw'], r['qty']) for r in s.rows]} 注記={s.notes} TESS={C.TESS_EXE}")
        ok &= len(s.rows) >= 1 and not any("失敗" in n for n in s.notes)
        # 2つのエンジンが、それぞれ動いたか
        im = Image.open(png).convert("RGB")
        ra = C.ocr_rapid(im); rb = C.ocr_tess(im)
        log.append(f"RapidOCR={len(ra)}かたまり Tesseract={len(rb)}かたまり")
        ok &= len(ra) > 5 and len(rb) > 5
    except Exception:
        ok = False; log.append(traceback.format_exc())
    open(outpath, "w", encoding="utf-8").write(("SELFTEST OK\n" if ok else "SELFTEST NG\n") + "\n".join(log) + "\n")
    return 0 if ok else 1


def smoke(outpath):
    """画面を出さずに、サーバーごと、通しで動かす(ビルド後の確認用・日本語の名前の場所でも動くか)。
    起動 → 自己診断 → 見積⇔発注 → 画像(OCR) → 見積⇔見積 → 中止 → 非対応形式の拒否 → 作業フォルダの安全 → リセット → 結果の保存"""
    import json, urllib.request, urllib.parse, tempfile, shutil
    res = []
    def chk(name, cond, info=""):
        res.append((bool(cond), name, str(info)[:200]))
    try:
        import fitz
        import server as S
        import mc_core as C
        srv, port = S.start(0)
        H = {"X-MC-Token": S.TOKEN}
        def call(path, method="GET", body=None, timeout=120):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=H)
            try:
                r = urllib.request.urlopen(req, timeout=timeout); return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        def up(mode, slot, role, name, data):
            return call(f"/api/upload?mode={mode}&slot={slot}&role={urllib.parse.quote(role)}&name={urllib.parse.quote(name)}", "POST", data)
        def wait(limit=240):
            t = time.time()
            while time.time() - t < limit:
                j = call("/api/state")[1]["job"]
                if j["status"] != "running": return j
                time.sleep(0.7)
            return {"status": "timeout"}
        def pdf(title, rows, scan=False):
            d = fitz.open(); pg = d.new_page(width=595, height=842)
            pg.insert_text((200, 70), title, fontname="japan", fontsize=20)
            for x, h in ((60, "型式"), (200, "品名"), (330, "数量"), (390, "単価"), (470, "金額")): pg.insert_text((x, 150), h, fontname="japan", fontsize=11)
            y = 176; tot = 0
            for m, n, q, pr in rows:
                pg.insert_text((60, y), m, fontname="helv", fontsize=11); pg.insert_text((200, y), n, fontname="japan", fontsize=11)
                pg.insert_text((330, y), str(q), fontname="helv", fontsize=11); pg.insert_text((390, y), f"{pr:,}", fontname="helv", fontsize=11)
                pg.insert_text((470, y), f"{q*pr:,}", fontname="helv", fontsize=11); tot += q * pr; y += 26
            pg.insert_text((390, y + 10), "合計", fontname="japan", fontsize=11); pg.insert_text((470, y + 10), f"{tot:,}", fontname="helv", fontsize=11)
            if scan:
                return pg.get_pixmap(dpi=170).tobytes("png")
            return d.tobytes()
        A = [("AW20-02BE-D", "フィルタ", 3, 12000), ("KQ2H06-M5", "継手", 10, 450), ("TJ-800", "アダプタ", 2, 3000)]
        B = [("AW20-02BE-D", "フィルタ", 3, 9800), ("KQ2H06-M5", "継手", 10, 300), ("XT-2200", "リレー", 5, 700)]

        # 0 自己診断
        for _ in range(60):
            h = call("/api/health")[1]
            if h.get("state") == "done": break
            time.sleep(1)
        chk("起動時の自己診断(RapidOCR と Tesseract が動く)", h.get("ok"), h)
        chk("作業フォルダが、このアプリ専用の場所", os.path.basename(S.WORK).startswith("run_"), S.WORK)
        chk("一時フォルダが、英数字だけの場所", C.safe_tmp_dir().isascii(), C.safe_tmp_dir())

        # 1 見積⇔発注(文字入りPDF)
        up("qo", 1, "見積", "q1.pdf", pdf("御見積書", A)); up("qo", 1, "発注", "o1.pdf", pdf("注文書", A))
        call("/api/run?mode=qo", "POST", b""); j = wait()
        r = call("/api/result")[1]
        cnt = r["result"]["vendors"][0]["count"] if r.get("result") else {}
        chk("見積⇔発注: 文字入りPDFが、3行とも「確実に一致」", j["status"] == "done" and cnt.get("OK") == 3, cnt)

        # 2 同じ画面で、次の社へ(「1回しか使えない」の再発防止)
        up("qo", 2, "見積", "q2.png", pdf("御見積書", A, scan=True)); st, _x = up("qo", 2, "発注", "o2.png", pdf("注文書", A, scan=True))
        chk("照合のあと、B社にも、書類を入れられる", st == 200, st)
        call("/api/run?mode=qo", "POST", b""); j = wait()
        r = call("/api/result")[1]
        v2 = [v for v in r["result"]["vendors"] if v["vendor"] == "B社"]
        c2 = v2[0]["count"] if v2 else {}
        chk("見積⇔発注: 画像(OCR)が、動いて、結果が出る(要確認でも、落ちない)", j["status"] == "done" and sum(c2.values()) >= 3, c2)
        chk("画像(OCR)で、1行以上は、確実に一致", (c2.get("OK_IMG", 0) + c2.get("OK", 0)) >= 1, c2)

        # 3 見積⇔見積
        up("qq", 1, "客先見積", "c.pdf", pdf("御見積書", A)); up("qq", 1, "仕入先見積", "s.pdf", pdf("お見積書", B))
        call("/api/run?mode=qq&qty=0", "POST", b""); j = wait()
        r = call("/api/result")[1]; c3 = r["result"]["vendors"][0]["count"] if r.get("result") else {}
        chk("見積⇔見積: 単価・合計が違っても、型番の違い(2件)だけを出す", j["status"] == "done" and c3.get("DIFF") == 2 and c3.get("OK") == 2, c3)

        # 3.5 見積同士を「見積⇔発注」に入れたとき、気づけて、移せる / 判断の基準が、画面から、わかる
        call("/api/reset?mode=qo", "POST", b"")
        up("qo", 4, "見積", "a.pdf", pdf("御見積書", A)); up("qo", 4, "発注", "b.pdf", pdf("お見積り", B))
        call("/api/run?mode=qo", "POST", b""); wait()
        vv = call("/api/result")[1]["result"]["vendors"][0]
        chk("見積同士を「見積⇔発注」に入れたら、「見積⇔見積のタブを使う」と案内する", any("見積 ⇔ 見積" in w for w in vv["warnings"]), vv["warnings"])
        chk("組にした根拠(何行がひもづいたか)が、わかる", any("ひもづ" in x["why"] and "根拠" in x["why"] for x in vv["pairs"]), [x["why"] for x in vv["pairs"]])
        chk("ツールが書類から読んだ中身(型式・数量)が、画面用に、出る", any((x.get("qread") or x.get("oread") or {}).get("rows") for x in vv["pairs"]))
        chk("社の番号が、結果に入っている(移すボタン用)", vv.get("slot") == 4, vv.get("slot"))
        mv = call("/api/move?slot=4", "POST", b"")[0]
        qs = call("/api/state?mode=qq")[1]["slots"][3]
        chk("ボタン1つで、見積⇔見積のタブへ書類を移せる", mv == 200 and qs["客先見積"] == ["a.pdf"] and qs["仕入先見積"] == ["b.pdf"], qs)
        call("/api/reset?mode=qq", "POST", b"")

        # 3.6 型番が1つも重ならない書類は、組にせず、理由が、わかる
        call("/api/reset?mode=qo", "POST", b"")
        up("qo", 4, "見積", "a2.pdf", pdf("御見積書", A)); up("qo", 4, "発注", "z.pdf", pdf("注文書", [("MR-J4-10A", "アンプ", 1, 48500)]))
        call("/api/run?mode=qo", "POST", b""); wait()
        vz = call("/api/result")[1]["result"]["vendors"][0]
        chk("組にならなかった理由が、わかる(ひもづく行が1つも無い)", any(x["q"] is None and "ひもづく行が、1つも無い" in x["why"] for x in vz["pairs"]) and any(x["o"] is None for x in vz["pairs"]), [x["why"] for x in vz["pairs"]])

        # 4 中止(止まったときの、逃げ道)
        call("/api/reset?mode=qo", "POST", b""); call("/api/reset?mode=qq", "POST", b"")
        d = fitz.open()
        for k in range(14):
            pg = d.new_page(width=595, height=842); pg.insert_text((100, 100 + k), "型式 数量 単価 金額", fontname="japan", fontsize=12)
        big = fitz.open()
        for pg in d:
            im = pg.get_pixmap(dpi=150).tobytes("png"); n = big.new_page(width=595, height=842); n.insert_image(n.rect, stream=im)
        up("qo", 3, "見積", "big.pdf", big.tobytes()); up("qo", 3, "発注", "big2.pdf", big.tobytes())
        call("/api/run?mode=qo", "POST", b""); time.sleep(3)
        chk("読み取り中は、状態が running", call("/api/state")[1]["job"]["status"] == "running")
        t = time.time(); call("/api/cancel", "POST", b""); st2 = call("/api/state")[1]["job"]["status"]
        chk("「中止」で、すぐに、元の状態(idle)に戻る", st2 == "idle" and time.time() - t < 5, st2)
        st3, _x = up("qo", 3, "見積", "again.pdf", pdf("御見積書", A))
        chk("中止のあと、また、書類を入れられる", st3 == 200, st3)
        time.sleep(8)   # 中止した古い仕事が、あとから、状態を壊さない
        chk("中止した古い仕事が、あとから、状態を上書きしない", call("/api/state")[1]["job"]["status"] == "idle", call("/api/state")[1]["job"])

        # 5 対応していない形式・空のファイル
        s1, m1 = up("qo", 4, "見積", "old.xls", b"x" * 100); s2, m2 = up("qo", 4, "見積", "empty.pdf", b"")
        chk("対応していない形式(.xls)は、理由つきで断る", s1 == 400 and "xlsx" in m1.get("error", ""), m1)
        chk("空のファイルは、理由つきで断る", s2 == 400, m2)

        # 6 作業フォルダの安全(2つ開いても、片方が、もう片方の書類を消さない)
        dead = os.path.join(S.WORK_BASE, "run_99999999"); os.makedirs(dead, exist_ok=True); open(os.path.join(dead, "x.txt"), "w").write("x")
        mine = os.path.join(S.WORK, "keep.txt"); open(mine, "w").write("x")
        S.clean_old_runs()
        chk("閉じたアプリの古い作業フォルダだけを消し、自分の分は、消さない", (not os.path.exists(dead)) and os.path.exists(mine))

        # 6.5 画面が開いている間は、アプリが動き続ける(画面からの問い合わせで、時刻が更新される)
        t_before = S.LAST_SEEN[0]; time.sleep(1.1); call("/api/state")
        chk("画面からの問い合わせで、「画面が開いている」と、アプリが、わかる", S.LAST_SEEN[0] > t_before)

        # 7 リセット
        call("/api/reset?mode=qo", "POST", b""); stt = call("/api/state?mode=qo")[1]
        chk("全部リセットで、書類が空になる", all(not s["見積"] and not s["発注"] for s in stt["slots"]) and stt["job"]["status"] == "idle")

        # 8 結果の保存(デスクトップ)
        s5, m5 = call("/api/savehtml", "POST", "<html>確認</html>".encode("utf-8"))
        ok = s5 == 200 and os.path.exists(m5.get("path", ""))
        chk("結果をデスクトップに保存できる", ok, m5)
        if ok:
            try: os.remove(m5["path"])
            except OSError: pass
        shutil.rmtree(S.WORK, ignore_errors=True)
    except Exception:
        chk("例外なく最後まで動く", False, traceback.format_exc())
    ng = [x for x in res if not x[0]]
    lines = [("OK " if o else "NG ") + n + ((" … " + i) if (i and not o) else "") for o, n, i in res]
    open(outpath, "w", encoding="utf-8").write(("SMOKE OK\n" if not ng else "SMOKE NG\n") + "\n".join(lines) + "\n")
    return 0 if not ng else 1


def find_appbrowser():
    """Edge または Chrome を探す(Windowsには、Edgeが、必ず入っている)。専用の窓(アドレス欄・タブなし)で開くために使う"""
    env = os.environ.get("MC_APPBROWSER")
    if env: return env if os.path.exists(env) else None
    pf = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA") if os.environ.get(k)]
    for base in pf:
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = os.path.join(base, rel)
            if os.path.exists(p): return p
    return None


def open_app_window(url, server_mod, mc_core):
    """EdgeやChromeを、アプリ用の窓(--app)で開き、その窓が閉じられるまで待つ。戻り値: 窓を開いて、閉じられたら True / 開けなかったら False"""
    exe = find_appbrowser()
    if not exe: return False
    import subprocess, tempfile
    prof = tempfile.mkdtemp(prefix="mc_win_", dir=server_mod.WORK if os.path.isdir(server_mod.WORK) else None)   # 専用の置き場(閉じたら、このプロセスも終わる)
    try:
        t0 = time.time()
        pr = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1280,860", "--no-first-run", "--no-default-browser-check"])
        mc_core.log(f"アプリ用の窓で開いた: {exe}")
        pr.wait()
        if time.time() - t0 < 5:   # すぐ終わった=別のプロセスに任せて終わった/開けなかった → 窓を開けた扱いにしない
            mc_core.log("アプリ用の窓が、すぐ終わった(開けなかった可能性)"); return False
        return True
    except Exception:
        mc_core.log("アプリ用の窓を開けなかった: " + traceback.format_exc()); return False
    finally:
        import shutil; shutil.rmtree(prof, ignore_errors=True)


def main():
    import server, mc_core
    srv, port = server.start(0)
    url = server.url(port)
    reason = ""
    try:
        import webview
        webview.create_window("見積・発注 照合", url, width=1280, height=860)
        webview.start()
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        mc_core.log("専用の窓(WebView2)を開けなかった: " + traceback.format_exc())   # 黙って握りつぶさない
        if open_app_window(url, server, mc_core):   # 次の手: Edge/Chrome を、アプリ用の窓で開く(タブ・アドレス欄なし。窓を閉じたら、終了)
            import shutil
            shutil.rmtree(server.WORK, ignore_errors=True)
            return
        mc_core.log("ブラウザで開く")
        webbrowser.open(url)
        idle = int(os.environ.get("MC_IDLE_EXIT") or 180)   # 画面(ブラウザ)が、この秒数、開かれていなければ、自動で終了する
        try:
            import tkinter
            r = tkinter.Tk(); r.title("見積・発注 照合"); r.geometry("560x300")
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
