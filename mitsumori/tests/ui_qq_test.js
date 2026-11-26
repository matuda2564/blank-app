const { chromium } = require('playwright');
(async()=>{
 const url=process.argv[2]; const C='/tmp/crash2/ascii/cust.pdf', S='/tmp/crash2/ascii/supp.pdf', tx='/tmp/crash2/tx';
 const b=await chromium.launch(); const pg=await b.newPage({viewport:{width:1300,height:1000}});
 const errs=[]; pg.on('pageerror',e=>errs.push(String(e).slice(0,120))); pg.on('dialog',async d=>{console.log('DIALOG:',d.message()); await d.accept()});
 await pg.goto(url); await pg.waitForSelector('.dz');
 async function put(slot,role,f){ const [fc]=await Promise.all([pg.waitForEvent('filechooser'),pg.click(`.dz[data-slot="${slot}"][data-role="${role}"]`)]); await fc.setFiles(f); await pg.waitForTimeout(600); }
 console.log('起動時のタブ/見出し:',await pg.innerText('#ttl'));
 await pg.click('#tab-qq'); await pg.waitForTimeout(800);
 console.log('見積⇔見積タブ: 見出し=',await pg.innerText('#ttl'),'/ 枠の名前=',(await pg.$$eval('.dz b',e=>e.slice(0,2).map(x=>x.textContent))).join(','),'/ 数量チェック表示=',!(await pg.$eval('#qtyrow',e=>e.hidden)));
 await put(1,'客先見積',C); await put(1,'仕入先見積',S);
 await pg.click('#go'); await pg.waitForFunction(()=>document.querySelector('#pt').textContent.startsWith('完了'),null,{timeout:60000}); await pg.waitForTimeout(800);
 const txt=await pg.innerText('#out');
 console.log('--- 結果(抜粋)'); console.log(txt.split('\n').filter(l=>l.trim()).slice(0,14).join('\n'));
 console.log('比べない項目の表示あり=',txt.includes('比べない項目'),' / 「発注」の語が結果に混ざっていない=',!txt.includes('発注「'));
 // 見積⇔発注 タブに戻ると、別の使い方の結果は出ない・書類も混ざらない
 await pg.click('#tab-qo'); await pg.waitForTimeout(900);
 console.log('見積⇔発注タブに戻る: 結果欄の長さ=',(await pg.innerText('#out')).length,' / 見積枠の枚数表示=',await pg.innerText('.dz[data-slot="1"][data-role="見積"] b'));
 await pg.click('#tab-qq'); await pg.waitForTimeout(900);
 console.log('見積⇔見積タブに戻る: 結果が復元=',(await pg.innerText('#out')).includes('型式'));
 // この社をリセット
 await pg.click('.slot:nth-child(1) button:has-text("この社をリセット")'); await pg.waitForTimeout(1200);
 console.log('この社をリセット後: ',await pg.innerText('.dz[data-slot="1"][data-role="客先見積"] b'),'/ 結果欄の長さ=',(await pg.innerText('#out')).length);
 console.log('ページのエラー:',JSON.stringify(errs));
 await pg.screenshot({path:'/tmp/crash2/qq_shot.png'});
 await b.close();
})();
