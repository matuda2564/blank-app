"""入金確認ツールの試験用に、銀行の入出金リスト(PDF)を作る。使い方: python gen_bank.py <出力PDF> [逆順]"""
import sys, random
import fitz

NAMES = ["ﾌﾘｺﾐ ｶ)ｷﾖｳｾﾗ", "ﾌﾘｺﾐ ｷﾖｳｾﾗ(ｶ", "ﾌﾘｺﾐ ｶ)ﾔﾏﾀﾞﾃﾞﾝｷ", "ﾌﾘｺﾐ ﾐﾅﾄｷﾞｹﾞﾝ(ｶ", "ﾌﾘｺﾐ ｱｵｷｾｲｻｸｼﾖ ﾕ)", "ﾌﾘｺﾐ ｲﾄｳ ﾀﾛｳ", "ﾌﾘｺﾐ ﾎｸﾄｳｺｳｷﾞﾖｳ ﾕ)", "ﾌﾘｺﾐ ﾔﾏﾀﾞ ﾊﾅｺ"]


def build(out, reverse=False, n=140, seed=1):
    random.seed(seed)
    rows = []; bal = 1_000_000
    for i in range(n):
        d = 1 + i * 28 // n
        nm = random.choice(NAMES)
        if random.random() < 0.15: pay, inc = random.randint(1, 80) * 1000, 0; nm = "ｼﾊﾗｲ " + nm[3:]
        else: pay, inc = 0, random.randint(1, 300) * 100
        bal = bal + inc - pay
        rows.append((f"2026/11/{d:02d}", nm, pay, inc, bal))
    longnames = {7: "ﾌﾘｺﾐ ﾎｸﾄｳｺｳｷﾞﾖｳｶﾌﾞｼｷｶﾞｲｼﾔ", 30: "ﾌﾘｺﾐ ｶ)ｷﾖｳｾﾗ ﾋﾞｼﾞﾈｽｿﾘﾕｰｼﾖﾝｽﾞ"}
    shown = list(reversed(rows)) if reverse else rows
    d = fitz.open(); y = 0; pg = None; per = 38
    for k, (dt, nm, pay, inc, bal) in enumerate(shown):
        if k % per == 0:
            pg = d.new_page(width=595, height=842)
            pg.insert_text((200, 50), "入出金明細 2026年11月分", fontname="japan", fontsize=14)
            for x, h in ((40, "日付"), (110, "摘要"), (330, "お支払金額"), (410, "お預り金額"), (495, "残高")): pg.insert_text((x, 90), h, fontname="japan", fontsize=10)
            y = 112
        pg.insert_text((40, y), dt, fontname="helv", fontsize=9)
        pg.insert_text((110, y), nm, fontname="japan", fontsize=9)
        for xr, v in ((385, pay), (465, inc), (555, bal)):
            if v or xr == 555:
                t = f"{v:,}"; pg.insert_text((xr - fitz.get_text_length(t, "helv", 9), y), t, fontname="helv", fontsize=9)
        y += 17
        if (len(shown) - 1 - k if reverse else k) in longnames:   # 名義が2行にまたがる行
            pg.insert_text((110, y - 4), "(続き)ﾋﾞｼﾞﾈｽ", fontname="japan", fontsize=9); y += 8
    d.save(out)
    return rows


if __name__ == "__main__":
    build(sys.argv[1], len(sys.argv) > 2)
