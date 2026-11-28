import sys; import os; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import fitz, gen_ks as G
def sp(s):  # 『112,000.00000』→『112, 000. 00000』(実物の見た目)
    return s.replace(",",", ").replace(".",". ")
def make_kyo2(path, spaced=True, dy_amt=0):
    d=fitz.open(); pg=d.new_page(width=842,height=595); t=lambda xy,s,fs=7,f="japan": pg.insert_text(xy,s,fontname=f,fontsize=fs)
    t((300,40),"検収明細表",14)
    hdr=[(20,"No."),(45,"区分"),(70,"発注/要求元"),(125,"注文番号/支給No."),(215,"SEQ"),(240,"品目コード"),(300,"取引先"),(345,"入荷日"),(390,"検収日"),(440,"品名"),(560,"入荷数"),(600,"単位"),(625,"合格数"),(670,"単価"),(730,"税率"),(765,"検収金額")]
    for x,h in hdr: t((x,100),h,7)
    y=118
    for i,(on,nm,q,p) in enumerate(G.KYO,1):
        f=(lambda s: sp(s)) if spaced else (lambda s:s)
        def R(xr,s,yy): t((xr-fitz.get_text_length(s,"helv",7),yy),s,f="helv")
        t((20,y),str(i),f="helv"); t((125,y),on,f="helv"); t((440,y),nm,7)
        R(590,f(f"{q:.3f}"),y+9+dy_amt); R(600+60,f(f"{q:.3f}"),y+9+dy_amt); R(715,f(f"{p:,.5f}"),y+9+dy_amt); t((735,y+9),"10%",f="helv")
        R(805,f(f"{p*q:,.2f}"),y+dy_amt); R(805,f(f"{p*q*0.1:,.2f}"),y+18)
        y+=34
    d.save(path)
if __name__=='__main__': make_kyo2('/tmp/crash3/ks2_kyo.pdf'); G.make_ours('/tmp/crash3/ks2_ours.pdf')
