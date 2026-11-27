"""見積⇔発注の、照合エンジン(まとめて何枚・何社ぶん入れても、判断できるように)。

判断の順番(上から。上で決まらないときだけ、下を使う):
  ① 会社名        … 別の会社の書類どうしは、組にしない(画像で会社名を読み違えたときは、決め手にしない)
  ② 見積番号      … 発注書に書いてある見積番号が、見積の番号と一致すれば、いちばん強い根拠
  ③ 型番(品番)   … 書き方の違い(大文字・小文字・ハイフン・空白)は、同じとみなす。1〜2文字違いは『近い型番』として、人に確認
  ④ 品名          … 型番が無い行(送料など)・型番が合わない行の、最後の手がかり。単価・金額が合うときだけ使う
  ⑤ 数量・単価・金額 … 同じ型番が複数あるときの決め手。組にした後は、違いを知らせる
「行」ごとに、見積の行と、発注書の行を、1対1で、ひもづける。だから、
  ・見積2枚の中身が、発注書1枚に収まっていても、 ・見積1枚が、発注書2枚に分かれていても(納品の分割)、行は、ひもづく。
  ・見積にあって、発注書に無い品目 = 『発注されなかった品目』(NOORDER。数量減・見送りの可能性。エラーではないが、必ず、知らせる)
  ・発注書にあって、見積に無い品目 = 『見積に無い品目』(違い・要確認)
「確実」は、文字入りPDF・Excelだけ。ひもづけが曖昧(型番が違う書き方・品名だけ)な行は、必ず、人に回す。
"""
import re, difflib, unicodedata
import mc_core as C

LEGAL = "株式会社|有限会社|合同会社"


def _t(s):
    s = unicodedata.normalize("NFKC", s or "")
    return s.replace("(株)", "株式会社").replace("(有)", "有限会社").replace("㈱", "株式会社").replace("㈲", "有限会社")


def names_in(d, nlines=30):
    """書類の上のほうから、会社名らしいものを、出てきた順に拾う(法人格を除いた名前)"""
    out = []
    for L in d.lines[:nlines]:
        t = re.sub(r"御中|殿", " ¦ ", _t(L["text"]))   # 『御中』が、隣の会社名に、くっつかないように、区切る
        for m in re.finditer(rf"(?:{LEGAL})\s*([^\s、。,.\d():：/¦]{{2,14}})|([^\s、。,.\d():：/¦]{{2,14}})\s*(?:{LEGAL})", t):
            n = (m.group(1) or m.group(2)).strip()
            if n and n not in out and not re.fullmatch(r"御中|様|殿", n): out.append(n)
    return out


def nsim(a, b):
    if not a or not b: return 0.0
    a, b = re.sub(r"[\s・\-]", "", _t(a)).lower(), re.sub(r"[\s・\-]", "", _t(b)).lower()
    if a == b: return 1.0
    if min(len(a), len(b)) >= 3 and (a in b or b in a): return 0.85
    return difflib.SequenceMatcher(None, a, b).ratio()


def learn_self(orders):
    """発注書の『御中』の前の会社名 = 自社(発注書の宛先)。自社の名前を、集める"""
    selfs = []
    for o in orders:
        for L in o.lines[:30]:
            for m in re.finditer(rf"(?:{LEGAL})\s*([^\s、。,.\d():：/]{{2,14}})\s*御中|([^\s、。,.\d():：/]{{2,14}})\s*(?:{LEGAL})\s*御中", _t(L["text"])):
                n = (m.group(1) or m.group(2)).strip()
                if n and n not in selfs: selfs.append(n)
    return selfs


def company_of(d, selfs):
    for n in names_in(d):
        if any(nsim(n, s) >= 0.7 for s in selfs): continue
        return n
    return None


def refs_of(o):
    """発注書が、書いている見積番号(複数あるときは、全部)"""
    t = _t(o.text)
    return {C.norm(m.group(1)) for m in re.finditer(r"見積(?:書|り)?\s*(?:番号|No\.?|NO\.?)+[:：]?\s*([A-Za-z0-9\-]+)", t)}


def _squash(t):
    return re.sub(r"[^A-Z0-9]", "", _t(t or "").upper())


def key_of(r):
    return C.tkey(r["model_raw"]) if r.get("model_raw") else ""


def val_agree(a, b):
    n = 0
    for k in ("qty", "price", "amount"):
        if a.get(k) is not None and a.get(k) == b.get(k): n += 1
    return n


class Ctx:
    def __init__(self, quotes, orders):
        self.q, self.o = quotes, orders
        self.selfs = learn_self(orders)
        self.comp = {id(d): company_of(d, self.selfs) for d in quotes + orders}
        self.refs = {id(o): refs_of(o) for o in orders}
        self.qno = {id(q): C.quote_no(q.text) for q in quotes}

    def pair_ev(self, q, o):
        """書類どうしの、根拠(点数と、説明のリスト)"""
        pts, ev = 0, []
        cq, co = self.comp[id(q)], self.comp[id(o)]
        cs = nsim(cq, co) if cq and co else None
        qn = self.qno[id(q)]; rf = self.refs[id(o)]
        if qn and (C.norm(qn) in rf or C.norm(qn) in C.norm(o.text)): pts += 60; ev.append(f"見積番号 {qn} が発注書に書いてある")
        elif rf and qn and C.norm(qn) not in rf and any(C.norm(x) == C.norm(self.qno[id(q2)] or "") for x in rf for q2 in self.q if q2 is not q):
            pts -= 30; ev.append("発注書は、別の見積の番号を書いている")
        if cs is not None:
            if cs >= 0.75: pts += 15; ev.append(f"会社名が同じ({cq})")
            elif cs < 0.5 and q.src != "ocr" and o.src != "ocr": pts -= 100; ev.append(f"会社名が違う(見積 {cq} / 発注書 {co})")
        return pts, ev, cs


def line_edges(ctx, q, o):
    """見積 q の行と、発注書 o の行の、すべての、ひもづけ候補 [(点, 見積の行, 発注書の行, 種類)]"""
    base, ev, cs = ctx.pair_ev(q, o)
    if base <= -100: return []
    out = []
    for qi, qr in enumerate(q.rows):
        for pi, pr in enumerate(o.rows):
            kq, kp = key_of(qr), key_of(pr)
            va = val_agree(qr, pr)
            sq, sp_ = _squash(qr.get("left")), _squash(pr.get("left"))
            if kq and kp and kq == kp: sc, kind = 100, "exact"
            elif (kq and len(kq) >= 6 and kq in sp_ and kq != kp) or (kp and len(kp) >= 6 and kp in sq and kq != kp): sc, kind = 92, "spaced"   # 空白・区切りが違うだけ(sy5120 5lzd 01)
            elif kq and kp and min(len(kq), len(kp)) >= 6 and C.lev(kq, kp) <= 2: sc, kind = 55 + 5 * va, "near"
            elif (not kq or not kp):
                ns = nsim(qr.get("name"), pr.get("name"))
                if ns >= 0.55 and va >= 1 and (qr.get("price") is not None and qr.get("price") == pr.get("price") or qr.get("amount") is not None and qr.get("amount") == pr.get("amount")):
                    sc, kind = 30 + 20 * ns + 5 * va, "name"
                else: continue
            else: continue
            if kind in ("exact", "spaced"): sc += 3 * va
            out.append((sc + base, qi, pi, kind))
    return out


def match(quotes, orders):
    """全部の見積の行 × 全部の発注書の行から、1対1のひもづけを、強い順に、決める"""
    ctx = Ctx(quotes, orders)
    edges = []
    for qd in quotes:
        for od in orders:
            for sc, qi, pi, kind in line_edges(ctx, qd, od): edges.append((sc, id(qd), qi, id(od), pi, kind))
    edges.sort(key=lambda e: -e[0])
    qline, pline = {}, {}   # (docid, 行) -> (相手 docid, 行, 種類, 点)
    for sc, dq, qi, do, pi, kind in edges:
        if (dq, qi) in qline or (do, pi) in pline: continue
        qline[(dq, qi)] = (do, pi, kind, sc); pline[(do, pi)] = (dq, qi, kind, sc)
    # 分納: 見積の1行が、発注書の複数行に分かれている(同じ型番)
    byid = {id(d): d for d in quotes + orders}
    split = {}
    for sc, dq, qi, do, pi, kind in edges:
        if (do, pi) in pline or kind != "exact": continue
        owner = qline.get((dq, qi))
        if owner and owner[2] == "exact":   # その見積の行は、すでに、別の発注書の行と、ひもづいている → 分納
            split.setdefault((dq, qi), []).append((do, pi)); pline[(do, pi)] = (dq, qi, "split", sc)
    # あいまいさ: 同じ点数で、別の見積にも、ひもづけられた発注書の行(会社名も見積番号も決め手にならない)
    by_p = {}
    for sc, dq, qi, do, pi, kind in edges: by_p.setdefault((do, pi), []).append((sc, dq, qi, kind))
    amb = {}
    for (do, pi), (dq, qi, kind, sc) in pline.items():
        if kind == "split": continue
        alts = {x[1] for x in by_p.get((do, pi), []) if x[1] != dq and x[0] >= sc - 3 and x[3] in ("exact", "spaced")}
        if alts: amb[(do, pi)] = [byid[a].name for a in alts]
    return ctx, qline, pline, split, byid, amb


def _st(r, k):
    return r.get(k)


def row_result(ctx, q, o, qr, pr, kind, extra=None):
    """ひもづいた1組の行の、判定"""
    ck = [("見積番号",) + C.quote_check(q, o)]
    cq, co = ctx.comp[id(q)], ctx.comp[id(o)]
    if cq and co: ck.append(("会社名", "ok" if nsim(cq, co) >= 0.75 else "na", "同じ" if nsim(cq, co) >= 0.75 else f"見積 {cq} / 発注書 {co}"))
    ck.append(("型式", "ok" if kind in ("exact", "split", "spaced") else "na", ("一致" if kind in ("exact", "split") else "書き方(空白・区切り)が違うが、同じ型番(見積 " + (qr.get("model_raw") or "") + " / 発注書 " + (pr.get("left") or "")[:30] + ")") if kind in ("exact", "split", "spaced") else ("近い型番(見積 " + (qr.get("model_raw") or "なし") + " / 発注書 " + (pr.get("model_raw") or "なし") + ")" if kind == "near" else "型番が無い行(品名・金額で判断)")))
    diffs, vals = [], {}
    for k_, lab in (("qty", "数量"), ("price", "単価"), ("amount", "金額")):
        va, vb = qr.get(k_), pr.get(k_)
        if extra and k_ == "qty": vb = extra["qty"]
        if k_ == "amount" and extra: vb = extra["amount"]
        if va is not None and vb is not None:
            same = va == vb
            ck.append((lab, "ok" if same else "ng", "一致" if same else f"見積 {va:,} / 発注 {vb:,}"))
            if not same: diffs.append(f"{lab}: 見積「{va:,}」⇔ 発注「{vb:,}」")
        else:
            found = False
            if (va is None and q.src == "ocr" and vb is not None and C._found_num(q, vb)) or (vb is None and o.src == "ocr" and va is not None and C._found_num(o, va)): found = True
            ck.append((lab, "ok" if found else "na", "画像の文字の中に、同じ数字があった" if found else "読み取れず"))
    ocr = q.src == "ocr" or o.src == "ocr"
    sure = bool(qr.get("exact", True)) and bool(pr.get("exact", True))
    unv = qr.get("unverified") or pr.get("unverified")
    probs = list(qr.get("problems", [])) + list(pr.get("problems", []))
    label = (qr.get("model_raw") or pr.get("model_raw") or qr.get("name") or pr.get("name") or "(型番なし)")[:22]
    if kind == "near":
        msg = "型番が1〜2文字違う行を、同じ品目と見て、ひもづけた(見間違いか本当の違いか、確認してください)。" + ("違い: " + " / ".join(diffs) if diffs else "")
        return {"status": "REVIEW", "model": label, "msg": msg, "qrow": qr, "orow": pr, "checks": ck}
    if kind == "name" and not diffs and sure and not ocr and not probs and nsim(qr.get("name"), pr.get("name")) >= 0.9 and all(c[1] == "ok" for c in ck if c[0] in ("数量", "単価", "金額")):
        return {"status": "OK", "model": label, "msg": "", "qrow": qr, "orow": pr, "checks": ck}   # 型番の無い行(送料など)が、品名・数量・単価・金額、全部同じ
    if kind == "name":
        msg = "型番が無い行(送料など)を、品名と金額で、同じ品目と見て、ひもづけた。" + ("違い: " + " / ".join(diffs) if diffs else "")
        return {"status": "DIFF" if diffs and sure else "REVIEW", "model": label, "msg": msg, "qrow": qr, "orow": pr, "checks": ck}
    if diffs:
        return {"status": "DIFF" if sure else "REVIEW", "model": label, "msg": " / ".join(diffs) + ("" if sure else "(違いに見えるが、画像の読み取りが確実でない)"), "qrow": qr, "orow": pr, "checks": ck}
    if probs or (not sure and not ocr):
        return {"status": "REVIEW", "model": label, "msg": "読み取りに自信がない: " + " / ".join(dict.fromkeys(probs or ["読み取りが確実でない"])), "qrow": qr, "orow": pr, "checks": ck}
    if ocr and (not sure or unv):
        okv = any(c[0] in ("数量", "金額") and c[1] == "ok" for c in ck)
        if okv: return {"status": "OK_IMG", "model": label, "msg": "型式・数量・金額は、相手の書類と一致しました。画像の読み取りは確実でないので、目で確認してください", "qrow": qr, "orow": pr, "checks": ck}
        return {"status": "REVIEW", "model": label, "msg": "読み取りに自信がない(画像の読み取りで、数量・金額を確かめられない)", "qrow": qr, "orow": pr, "checks": ck}
    return {"status": "OK_IMG" if ocr else "OK", "model": label, "msg": "", "qrow": qr, "orow": pr, "checks": ck}


def build(quotes, orders):
    """戻り: [(見積|None, 発注書|None, 理由, 結果の一覧, 比べた項目, 比べなかった項目)]"""
    if not quotes and not orders: return []
    ctx, qline, pline, split, byid, amb = match(quotes, orders)
    # 書類どうしの、結びつき(ひもづいた行の数)
    link = {}
    for (dq, qi), (do, pi, kind, sc) in qline.items(): link.setdefault((dq, do), []).append((qi, pi, kind))
    for (dq, qi), lst in split.items():
        for do, pi in lst: link.setdefault((dq, do), []).append((qi, pi, "split"))
    # 各書類の『主な相手』(いちばん、行がひもづいた相手)
    main_q = {}; main_o = {}
    for (dq, do), L in sorted(link.items(), key=lambda kv: -len(kv[1])):   # ひもづいた行が多い組から
        main_q.setdefault(dq, do); main_o.setdefault(do, dq)
    pairs = {}
    for (dq, do), L in link.items():
        q, o = byid[dq], byid[do]
        pairs[(dq, do)] = {"q": q, "o": o, "results": [], "ev": ctx.pair_ev(q, o)[1]}
    for (dq, do), L in link.items():
        q, o = byid[dq], byid[do]
        done_split = set()
        for qi, pi, kind in L:
            qr, pr = q.rows[qi], o.rows[pi]
            if kind == "split": continue
            extra = None
            sp = split.get((dq, qi))
            if sp:   # 分納の、合計で、比べる
                tot_q = pr.get("qty") or 0; tot_a = pr.get("amount") or 0
                for (do2, pi2) in sp:
                    r2 = byid[do2].rows[pi2]; tot_q += r2.get("qty") or 0; tot_a += r2.get("amount") or 0
                extra = {"qty": tot_q, "amount": tot_a}
            res = row_result(ctx, q, o, qr, pr, kind, extra)
            if amb.get((do, pi)):   # 同じ根拠で、ほかの見積にも、ひもづく → 黙って決めない
                res["status"] = "REVIEW"
                res["msg"] = f"同じ型番・同じ内容の見積が、ほかにもある(「{'」「'.join(amb[(do, pi)])}」)。どの見積の発注か、決められない。" + res["msg"]
            if sp: res["msg"] = ("発注が、" + str(len(sp) + 1) + "枚に分かれている(合計で比べた)。" + res["msg"]).strip(); res["status"] = res["status"] if res["status"] != "OK" else "REVIEW"
            pairs[(dq, do)]["results"].append(res)
    # 発注書の、見積に無い行
    for o in orders:
        po = main_o.get(id(o))
        for pi, pr in enumerate(o.rows):
            if (id(o), pi) in pline: continue
            label = (pr.get("model_raw") or pr.get("name") or "(型番なし)")[:22]
            ocr = o.src == "ocr"
            res = {"status": "REVIEW" if (ocr or pr.get("problems")) else "DIFF", "model": label, "msg": "発注書にだけある品目(見積に無い)。" + ("画像の読み取りが確実でないので、断定しない" if ocr else "新しく加わった品目か、別の見積の品目か、確認してください"), "qrow": None, "orow": pr}
            if po: pairs[(po, id(o))]["results"].append(res)
    # 見積の、発注されなかった行(その見積に、1行でも、ひもづいた発注があるときだけ)
    for q in quotes:
        mo = main_q.get(id(q))
        if not mo: continue
        for qi, qr in enumerate(q.rows):
            if (id(q), qi) in qline or (id(q), qi) in split: continue
            label = (qr.get("model_raw") or qr.get("name") or "(型番なし)")[:22]
            pairs[(id(q), mo)]["results"].append({"status": "NOORDER", "model": label, "msg": "見積にあるが、発注書に無い品目(数量減・見送りの可能性。発注されなかった品目です)", "qrow": qr, "orow": None})
    out = []
    for (dq, do), pdt in pairs.items():
        q, o = pdt["q"], pdt["o"]
        n = len(link[(dq, do)])
        others_q = [byid[x[1]].name for x in link if x[0] == dq and x[1] != do]
        others_o = [byid[x[0]].name for x in link if x[1] == do and x[0] != dq]
        why = f"{n}行が、ひもづいた。根拠: " + (" / ".join(pdt["ev"]) if pdt["ev"] else "型番が一致")
        if others_o: why += f"。★この発注書は、ほかの見積(「{'」「'.join(others_o)}」)の品目も、含んでいる"
        if others_q: why += f"。★この見積は、ほかの発注書(「{'」「'.join(others_q)}」)にも、分かれている"
        out.append((q, o, why, pdt["results"], ["型式", "数量", "単価", "金額"], []))
    paired_q = {k[0] for k in link}; paired_o = {k[1] for k in link}
    for o in orders:
        if id(o) not in paired_o:
            on = C.quote_no(o.text); rf = ctx.refs[id(o)]
            parts = ["相手が決まらない(ひもづく行が、1つも無い)"]
            if rf: parts.append(f"発注書に書かれた見積番号は {'、'.join(sorted(rf))}、入っている見積の番号は {C._nums(quotes)}")
            co = ctx.comp[id(o)]
            if co: parts.append(f"発注書の会社名: {co}")
            parts.append("対応する見積が、入っていない(または、入れる社の枠が違う)かもしれません")
            out.append((None, o, "。".join(parts), [], [], []))
    for q in quotes:
        if id(q) not in paired_q:
            refs = sorted({x for o in orders for x in ctx.refs[id(o)]})
            out.append((q, None, "対応する発注書がない" + (f"(入っている発注書が書いている見積番号: {'、'.join(refs)}。この見積の番号: {ctx.qno[id(q)] or '読み取れなかった'})" if refs else ""), [], [], []))
    return out
