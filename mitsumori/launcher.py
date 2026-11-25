# -*- coding: utf-8 -*-
"""見積・発注 照合ツールの入口(.exe の中身)。裏でサーバーを起動し、専用の窓(なければブラウザ)で開く。
  --selftest <出力ファイル> : 画面を出さずに、読み取りの部品が動くか自己点検して結果をファイルへ書く(ビルド後の確認用)
"""
import os, sys, threading, webbrowser, time, io, traceback

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 同梱した Tesseract があれば、それを使う
_t = os.path.join(BASE, "tesseract")
if os.path.isdir(_t):
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


def main():
    import server
    srv, port = server.start(0)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview
        webview.create_window("見積・発注 照合", url, width=1280, height=860)
        webview.start()
    except Exception:
        webbrowser.open(url)
        try:
            import tkinter
            r = tkinter.Tk(); r.title("見積・発注 照合"); r.geometry("380x120")
            tkinter.Label(r, text="ブラウザで画面を開きました。\nこの窓を閉じると、終了します(書類も消えます)。", pady=24).pack()
            r.mainloop()
        except Exception:
            while True: time.sleep(3600)
    import shutil
    shutil.rmtree(server.WORK, ignore_errors=True)   # 終了時に、作業中の書類を全部消す


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    main()
