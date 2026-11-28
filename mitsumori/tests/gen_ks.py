import fitz
def num(v, dec=0): return f"{v:,.{dec}f}"
ITEMS=[("10077590787","膜厚測定機FV05301メンテナンス作業",1,199500),("10077476879","モデルNo.Q100501 ロット印字機設計(1)",1,101500),("10077473640","10月度トラブル実績集計作業",1,184000),
("10077442044","真空ポンプOH加治木産業本社テープカット機KRF25",1,63550),("10077661870","加治木本社積層機メンテナンス作業",1,100000),("10077661889","加治木鹿児島洗浄機メンテナンス作業",1,10530),
("10075818195","2-2K3Fレーザーマーキング機改造",1,400000),("10076774752","電磁開閉器 MSOD-112 0.4KW 200V DC24V",2,6300),("10076774744","サーキットプロテクタ CP30-BA 3P 1-S 3A",1,5210),
("10077620465","熱サイクル炉3号用温調器(4-1-4F)",1,26000),("10077655528","格子状治具修理",1,124000),("10077655536","メッシュ取り外し作業",1,173000)]
# 京セラ側: 1件は単価違い、1件は個数違い、1件は無し、1件は余分
KYO=[list(x) for x in ITEMS]
KYO[2][3]=185000          # 単価違い
KYO[7][2]=3               # 個数違い
KYO=[x for i,x in enumerate(KYO) if i!=5]   # 10077661889 が京セラに無い
KYO.append(["10077999999","余分な作業",1,12000])
def make_ours(path, spaced=True):
    d=fitz.open(); pg=d.new_page(width=595,height=842); t=lambda xy,s,fs=9,f="japan": pg.insert_text(xy,s,fontname=f,fontsize=fs)
    t((60,60),"京セラ株式会社 鹿児島霧島工場 御中",12); t((380,60),"株式会社興電舎商事",12)
    for x,h in ((50,"日付"),(100,"ご注文番号"),(210,"メーカー / 品名"),(380,"数 量"),(430,"単 価"),(490,"金 額"),(550,"No.")): t((x,200),h,9)
    y=222
    for i,(on,nm,q,p) in enumerate(ITEMS):
        t((50,y),"10/21",f="helv"); t((100,y),on,f="helv"); t((210,y),nm,8)
        t((388,y),str(q),f="helv")
        for xr,v in ((470,p),(535,p*q)):
            s=f"{v:,}"; s=s.replace(",",", ") if spaced else s
            t((xr-fitz.get_text_length(s,"helv",9),y),s,f="helv")
        t((552,y),str(2038068+i),f="helv"); y+=22
    d.save(path)
def make_kyo(path):
    d=fitz.open(); pg=d.new_page(width=842,height=595); t=lambda xy,s,fs=7,f="japan": pg.insert_text(xy,s,fontname=f,fontsize=fs)
    t((300,40),"検収明細表",14)
    hdr=[(20,"No."),(45,"区分"),(70,"発注/要求元"),(125,"注文番号/支給No."),(215,"SEQ"),(240,"品目コード"),(300,"取引先"),(345,"入荷日"),(390,"検収日"),(440,"品名"),(560,"入荷数"),(600,"単位"),(625,"合格数"),(670,"単価"),(730,"税率"),(765,"検収金額")]
    for x,h in hdr: t((x,100),h,7)
    y=118
    for i,(on,nm,q,p) in enumerate(KYO,1):
        t((20,y),str(i),f="helv"); t((45,y),"50",f="helv"); t((70,y),"46A",f="helv"); t((125,y),on,f="helv"); t((300,y),"K3163",f="helv")
        t((440,y),nm,7); t((565,y),f"{q:.3f}",f="helv"); t((600,y),"PC"); t((630,y),f"{q:.3f}",f="helv"); t((668,y),f"{p:,.5f}",f="helv"); t((735,y),"10%",f="helv")
        t((765,y),f"{p*q:,.2f}",f="helv")
        t((215,y+10),"001",f="helv"); t((240,y+10),"-",f="helv"); t((765,y+10),f"{p*q*0.1:,.2f}",f="helv")
        y+=26
    d.save(path)
if __name__=="__main__":
    make_ours('/tmp/crash3/ks_ours.pdf'); make_kyo('/tmp/crash3/ks_kyo.pdf')
