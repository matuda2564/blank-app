import sys,os,io
sys.path.insert(0,'/tmp/ghw/blank-app/mitsumori')
import fitz, mc_core as C
def doc(title, items, scan=False, dpi=170):
    d=fitz.open(); p=d.new_page(width=595,height=842)
    p.insert_text((220,60),title,fontname="japan",fontsize=20)
    p.insert_text((60,100),"宛先: 株式会社テスト 御中    TEL 03-1234-5678  登録番号 T1234567890123",fontname="japan",fontsize=9)
    for x,h in ((50,"No."),(80,"品名・規格"),(330,"数量"),(375,"単位"),(420,"単価"),(480,"金額"),(530,"納期")): p.insert_text((x,150),h,fontname="japan",fontsize=10)
    y=176; tot=0
    for no,name,q,u,pr,due in items:
        p.insert_text((50,y),str(no),fontname="helv",fontsize=10); p.insert_text((80,y),name,fontname="japan",fontsize=10)
        p.insert_text((335,y),str(q),fontname="helv",fontsize=10); p.insert_text((378,y),u,fontname="japan",fontsize=10)
        p.insert_text((420,y),f"{pr:,}",fontname="helv",fontsize=10); p.insert_text((480,y),f"{q*pr:,}",fontname="helv",fontsize=10); p.insert_text((530,y),due,fontname="japan",fontsize=9); tot+=q*pr; y+=24
    p.insert_text((80,y+6),"以下余白",fontname="japan",fontsize=10)
    p.insert_text((420,y+34),"小計",fontname="japan",fontsize=10); p.insert_text((480,y+34),f"{tot:,}",fontname="helv",fontsize=10)
    p.insert_text((420,y+54),"消費税",fontname="japan",fontsize=10); p.insert_text((480,y+54),f"{int(tot*0.1):,}",fontname="helv",fontsize=10)
    p.insert_text((420,y+74),"合計",fontname="japan",fontsize=10); p.insert_text((480,y+74),f"{int(tot*1.1):,}",fontname="helv",fontsize=10)
    return p.get_pixmap(dpi=dpi).tobytes("png") if scan else d.tobytes()
cust=[(1,"国際チャート チャート紙 B9565AW-T 10冊入",1,"箱",15800,"納期約20日"),(2,"ペン先 XYZ-100A 5本入",2,"箱",3000,"在庫あり"),(3,"インク QRS-777 黒",4,"本",800,"在庫あり")]
supp=[(1,"国際チャート 記録紙 10冊入 (型番 B9565AW-T)",1,"箱",12000,"約20日"),(2,"ペン先 XYZ-100B 5本入",2,"箱",2200,"在庫あり")]
base='/tmp/crash2/real/slot1'
for kind in ("text","scan"):
    root=f"{base}_{kind}"
    for r in ("客先見積","仕入先見積"): os.makedirs(f"{root}/{r}",exist_ok=True)
    ext="png" if kind=="scan" else "pdf"
    open(f"{root}/客先見積/国際チャート お見積り.{ext}","wb").write(doc("御見積書",cust,kind=="scan"))
    open(f"{root}/仕入先見積/国際チャート B9565AW-T 御見積書.{ext}","wb").write(doc("お見積書",supp,kind=="scan"))
    v=C.run_vendor(root,workers=2,name="A社",mode="qq")
    p=v["pairs"][0]
    print(f"=== {kind}: 組={p['why']} / 比べた項目={p['compared']}")
    for r in sorted(p["results"],key=lambda r:(r['status'],r['model'])): print(f"  {r['status']:7} {r['model']:14} {r['msg'][:95]}")
