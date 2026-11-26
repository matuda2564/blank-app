import sys,os,random,shutil
sys.path.insert(0,'/tmp/mc/app')
import fitz, mc_core as C
FONT="japan"
def mk(path,title,rows,with_price=True):
    d=fitz.open(); p=d.new_page(width=595,height=842)
    p.insert_text((200,70),title,fontname=FONT,fontsize=20)
    for x,h in ((60,"型式"),(200,"品名"),(330,"数量"),(390,"単価"),(470,"金額")): p.insert_text((x,150),h,fontname=FONT,fontsize=11)
    y=170; tot=0
    for m,n,q,pr in rows:
        p.insert_text((60,y),m,fontname=FONT,fontsize=11); p.insert_text((200,y),n,fontname=FONT,fontsize=11); p.insert_text((330,y),str(q),fontname=FONT,fontsize=11)
        p.insert_text((390,y),f"{pr:,}",fontname=FONT,fontsize=11); p.insert_text((470,y),f"{q*pr:,}",fontname=FONT,fontsize=11); tot+=q*pr; y+=24
    p.insert_text((390,y+10),"合計",fontname=FONT,fontsize=11); p.insert_text((470,y+10),f"{tot:,}",fontname=FONT,fontsize=11)
    d.save(path)
base='/tmp/crash2/qq/slot1'; 
for r in ("客先見積","仕入先見積"): os.makedirs(f'{base}/{r}',exist_ok=True)
cust=[("AW20-02BE-D","フィルタレギュレータ",3,12000),("KQ2H06-M5","ワンタッチ継手",10,450),("TJ-800","変換アダプタ",2,3000),("SY5120-5LZD-01","電磁弁",4,5200),("MR-J4-10A","サーボアンプ",1,48000)]
supp=[("AW20-02BE-D","フィルタレギュレータ",3,9800),("KQ2H06-M5","ワンタッチ継手",12,300),("TJ-800","変換アダプタ",2,2100),("SY5120-5LZD-02","電磁弁",4,4100),("XT-2200","リレー",5,700)]
mk(f'{base}/客先見積/cust.pdf',"御見積書",cust); mk(f'{base}/仕入先見積/supp.pdf',"お見積書",supp)
for qty in (False,True):
    v=C.run_vendor(base,workers=1,name="A社",mode="qq",with_qty=qty)
    p=v['pairs'][0]; print(f"--- 数量も比べる={qty} / 組: {p['why']} / 比べた項目: {p['compared']} / 比べない: {p.get('skipnote')}")
    for r in sorted(p['results'],key=lambda r:r['status']): print(f"  {r['status']:7} {r['model']:16} {r['msg'][:90]}")
