"""見積⇔発注の、まとめ入れ試験の、データ作り。
5社ぶんの、見積と発注書(見積15枚・発注書14枚)を作り、正解(どの発注書の、どの行が、どの見積の、どの行か)も、一緒に出す。
想定する、いろいろ: ①見積番号つきの発注書 ②見積番号の無い発注書 ③品目が減った/増えた ④数量・単価が変わった
⑤型番の書き方が違う(小文字・空白) ⑥2枚の見積が1枚の発注書に収まる ⑦1枚の見積が2枚の発注書に分かれる
⑧別の会社でも、同じ型番を売っている(迷う) ⑨備考欄に、型番らしい文字がある(拾ってはいけない)
使い方: python gen_multi.py <出力フォルダ>   → <出力>/mixed/{見積,発注}/*.pdf と <出力>/truth.json"""
import sys, os, json
import fitz

SELF = "株式会社興電舎商事"
CUST = ["株式会社青木製作所", "有限会社北斗工業", "山田電機株式会社", "株式会社ミナト技研", "大和機械株式会社"]
# 型番・品名・単価。 SY5120-5LZD-01 は、複数の会社に売る(迷うもと)
POOL = {
    "AW20-02BE-D": ("レギュレータ", 4200), "KQ2H06-M5": ("ワンタッチ継手", 380), "SY5120-5LZD-01": ("電磁弁", 5100),
    "MR-J4-10A": ("サーボアンプ", 48500), "TJ-800": ("温度調節器", 9800), "XT-2200": ("センサ", 12300),
    "B9565AW-T": ("チャート紙", 15800), "ISE30A-01-N": ("圧力スイッチ", 7900), "VQ110U-5M": ("電磁弁ユニット", 3600),
    "CDQ2B32-25DZ": ("薄形シリンダ", 6700), "AF30-02": ("エアフィルタ", 2900), "KE-212": ("リレー", 1450),
}
MODELS = list(POOL)


def item(model, qty, price=None):
    nm, pr = POOL[model]
    return {"model": model, "name": nm, "qty": qty, "price": price or pr}


SHIP = {"model": "", "name": "送料", "qty": 1, "price": 1400}


def draw(path, kind, cust, no, ref, items, style, y0=170):
    d = fitz.open(); p = d.new_page(width=595, height=842)
    t = lambda xy, s, fs=10, f="japan": p.insert_text(xy, s, fontname=f, fontsize=fs)
    if kind == "見積":
        t((230, 60), "御 見 積 書", 20); t((50, 100), f"{cust} 御中", 12); t((340, 100), SELF, 12)
        t((340, 116), "鹿児島県霧島市隼人町 TEL 0995-00-0000 FAX 0995-00-0001", 8); t((50, 125), f"見積書No: {no}", 9)
    else:
        t((230, 60), "注 文 書", 20); t((50, 100), f"{SELF} 御中", 12); t((340, 100), cust, 12)
        t((340, 116), "TEL 099-000-0000 (FAX) 099-000-0001", 8)
        if ref: t((50, 125), f"■貴社見積No. {ref} の件です", 9)
        t((50, 140), f"発注番号 {no}", 9)
    for x, h in ((60, "メーカー・品名"), (360, "数 量"), (405, "単位"), (445, "単 価"), (505, "金 額")): t((x, y0 - 14), h, 10)
    y, tot = y0 + 6, 0
    for i, it in enumerate(items, 1):
        it = dict(it)
        if kind == "発注" and not it.get("keepprice"): it["price"] = int(it["price"] * 0.93)   # 発注書の単価・金額は、見積と違って当たり前(比べない)
        amt = it["qty"] * it["price"]; tot += amt
        mdl = it["model"]
        if it.get("writeas"): mdl = it["writeas"]
        nums = lambda yy: (t((365, yy), str(it["qty"]), f="helv"), t((408, yy), "個", 9), t((445, yy), f"{it['price']:,}", f="helv"), t((505, yy), f"{amt:,}", f="helv"))
        if style == 0:   # 品名の行に数字、型番は2行目
            t((50, y), str(i), f="helv"); t((60, y), f"SMC {it['name']}" if mdl else it["name"]); nums(y)
            if mdl: t((60, y + 11), mdl, f="helv")
            y += 30
        else:            # 型番と品名が同じ行
            t((50, y), str(i), f="helv")
            if mdl: t((60, y), mdl, f="helv"); t((60 + 6 * len(mdl) + 10, y), it["name"])
            else: t((60, y), it["name"])
            nums(y); y += 20
    t((60, y + 4), "以下余白", 9)
    t((445, y + 24), "合計", 10); t((505, y + 24), f"{tot:,}", f="helv")
    t((50, y + 60), "備考: 納期は別途ご連絡します。型式変更 KQ2-10A は対象外です。", 9)
    d.save(path)


def build(out):
    root = os.path.join(out, "mixed"); os.makedirs(os.path.join(root, "見積"), exist_ok=True); os.makedirs(os.path.join(root, "発注"), exist_ok=True)
    truth_po, truth_q, files = {}, {}, {"q": [], "o": []}
    qn = 1000; pn = 5000; company_quotes = {}

    def add_q(ci, items):
        nonlocal qn
        qn += 7; name = f"見積_{ci}_{qn}.pdf"; draw(os.path.join(root, "見積", name), "見積", CUST[ci], str(qn), None, items, ci % 2)
        files["q"].append(name)
        for it in items: truth_q[(name, it["model"])] = "NOT_ORDERED"
        return name, qn

    def add_p(ci, items, ref):
        nonlocal pn
        pn += 13; name = f"発注_{ci}_{pn}.pdf"
        draw(os.path.join(root, "発注", name), "発注", CUST[ci], str(pn), ref, items, (ci + 1) % 2); files["o"].append(name); return name

    for ci in range(5):
        a, b, c = MODELS[(ci * 2) % 12], MODELS[(ci * 2 + 1) % 12], MODELS[(ci * 2 + 2) % 12]
        e, f_, g = "SY5120-5LZD-01", MODELS[(ci * 3 + 5) % 12], MODELS[(ci * 3 + 7) % 12]
        qs = []
        i1 = [item(a, 2), item(b, 5), item(c, 1)] + ([dict(SHIP)] if ci == 0 else []); i2 = [item(e, 4), item(f_, 3), item(g, 10)]; _used = {x["model"] for x in i1 + i2}; _free = [m for m in MODELS if m not in _used and m != MODELS[0]]; m3a, m3b = _free[ci % len(_free)], _free[(ci + 3) % len(_free)]; i3 = [item(m3a, 2), item(m3b, 6)]
        company_quotes[ci] = i1 + i2 + i3
        q1, n1 = add_q(ci, i1); q2, n2 = add_q(ci, i2); q3, n3 = add_q(ci, i3)
        # ① P1: 見積1と全部同じ・見積番号つき
        p1 = add_p(ci, [item(a, 2), item(b, 5), item(c, 1)] + ([dict(SHIP)] if ci == 0 else []), str(n1))
        for m in (a, b, c): truth_po[(p1, m)] = {"q": q1, "expect": "OK"}; truth_q[(q1, m)] = "ORDERED"
        if ci == 0: truth_q[(q1, "")] = "ORDERED"
        if ci == 2:      # ⑩ 同じ型番を、単価違いで2回見積(改訂)→ 発注は、新しいほうの単価
            x = MODELS[0]; qa, _ = add_q(ci, [item(x, 2, 4200)]); qb, _ = add_q(ci, [item(x, 2, 3900)])
            pz = add_p(ci, [dict(item(x, 2, 3900), keepprice=True)], None); truth_po[(pz, x)] = {"q": qb, "expect": "OK"}; truth_q[(qb, x)] = "ORDERED"
        if ci == 1:      # ⑥ 見積2・見積3の中身を、1枚の発注書に(見積番号なし)
            it = [item(e, 4), item(f_, 3), item(m3a, 2)]
            p2 = add_p(ci, it, None)
            for x, qq in zip(it, (q2, q2, q3)): truth_po[(p2, x["model"])] = {"q": qq, "expect": "OK"}; truth_q[(qq, x["model"])] = "ORDERED"
        elif ci == 3:    # ⑦ 見積2が、2枚の発注書に分かれる(納品分割)
            pa = add_p(ci, [item(e, 4), item(f_, 3)], None); pb = add_p(ci, [item(g, 10), item(m3a, 2)], None)
            for m, pp, qq in ((e, pa, q2), (f_, pa, q2), (g, pb, q2), (m3a, pb, q3)): truth_po[(pp, m)] = {"q": qq, "expect": "OK"}; truth_q[(qq, m)] = "ORDERED"
        else:            # ③④⑤ 見積2: 1品目が減り・数量が変わり・型番の書き方が違い・新しい品目が増える(見積番号なし)
            wr = e.lower().replace("-", " ")      # ⑤ 書き方が違う
            it = [dict(item(e, 4), writeas=wr), item(f_, 5)]    # f_ の数量 3→5(違い)・g は、発注されない
            used = {x["model"] for x in company_quotes[ci]}
            _fn = [m for m in MODELS if m not in used]; new = _fn[ci % len(_fn)]
            it.append(item(new, 2))                         # 見積に無い新品目
            p2 = add_p(ci, it, None)
            truth_po[(p2, e)] = {"q": q2, "expect": "OK"}; truth_q[(q2, e)] = "ORDERED"
            truth_po[(p2, f_)] = {"q": q2, "expect": "DIFF_QTY"}; truth_q[(q2, f_)] = "ORDERED"
            truth_po[(p2, new)] = {"q": None, "expect": "NEW"}
    json.dump({"po": {f"{k[0]}|{k[1]}": v for k, v in truth_po.items()}, "q": {f"{k[0]}|{k[1]}": v for k, v in truth_q.items()}, "files": files, "cust": CUST},
              open(os.path.join(out, "truth.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return root, files


def to_scans(root, n):
    """発注書のうち、先頭のn枚を、画像(FAXのスキャン)にする"""
    for name in sorted(os.listdir(os.path.join(root, "発注")))[:n]:
        pth = os.path.join(root, "発注", name); d = fitz.open(pth)
        d[0].get_pixmap(dpi=200).save(pth.replace(".pdf", ".png")); d.close(); os.remove(pth)


if __name__ == "__main__":
    out = sys.argv[1]; root, files = build(out)
    if len(sys.argv) > 2: to_scans(root, int(sys.argv[2])); print("見積", len(files["q"]), "枚 / 発注書", len(files["o"]), "枚 →", root)
