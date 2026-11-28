"""京セラの検収明細表の、本物の見た目(写真から): 見出しが2段(1段目: 注文番号/支給No. SEQ 品目コード 品名 規格 検収金額 / 2段目: 区分 発注/要求元 取引先 入荷日 検収日 提供引渡日 入荷数 単位 合格数 単価 個単 税率 消費税額)で、
2段目の『区分』『発注/要求元』が、注文番号の列の真下に並ぶ。1行は2段: 上段=注文番号・品名・規格・検収金額 / 下段=No.・区分・取引先・日付・入荷数・単位・合格数・単価・税率・消費税額。数字は『1. 000』『64, 500. 00000』のようにすきまが空く。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz, gen_ks as G
def sp(s): return s.replace(",", ", ").replace(".", ". ")
def make_kyo3(path, items=None):
    items = items or G.KYO
    d = fitz.open(); pg = d.new_page(width=842, height=595); T = lambda xy, s, fs=7, f="japan": pg.insert_text(xy, s, fontname=f, fontsize=fs)
    def R(xr, s, y, f="helv"): T((xr - fitz.get_text_length(s, "helv", 7), y), s, f=f)
    T((300, 40), "検収明細表", 14)
    for x, h in ((20, "No."), (125, "注文番号/支給No."), (225, "SEQ"), (250, "品目コード"), (330, "品名"), (600, "規格"), (765, "検収金額")): T((x, 100), h, 7)
    for x, h in ((128, "区分"), (160, "発注/要求元"), (225, "取引先"), (265, "入荷日"), (305, "検収日"), (345, "提供引渡日"), (440, "入荷数"), (475, "単位"), (500, "合格数"), (560, "単価"), (590, "個単"), (620, "税率"), (765, "消費税額")): T((x, 112), h, 7)
    y = 130
    for i, (on, nm, q, p) in enumerate(items, 1):
        T((125, y), on, f="helv"); T((225, y), "001", f="helv"); T((330, y), nm, 7); T((600, y), "-", f="helv")
        R(805, sp(f"{p*q:,.2f}"), y)
        T((20, y + 10), str(i), f="helv"); T((128, y + 10), "50", f="helv"); T((160, y + 10), "46A", f="helv"); T((225, y + 10), "K3163", f="helv")
        T((265, y + 10), "2026/11/06", f="helv"); T((305, y + 10), "2026/11/09", f="helv")
        R(462, sp(f"{q:.3f}"), y + 10); T((477, y + 10), "PC"); R(530, sp(f"{q:.3f}"), y + 10); R(585, sp(f"{p:,.5f}"), y + 10); T((595, y + 10), "A", f="helv"); T((622, y + 10), "10%", f="helv")
        R(805, sp(f"{p*q*0.1:,.2f}"), y + 10)
        y += 28
    d.save(path)
if __name__ == "__main__": make_kyo3("/tmp/crash3/ks3_kyo.pdf")
