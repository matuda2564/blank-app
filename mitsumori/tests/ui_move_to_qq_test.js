const { chromium } = require('playwright');
(async()=>{
 const b=await chromium.launch(); const pg=await b.newPage({viewport:{width:1300,height:1000}}); const dl=[];
 pg.on('dialog',async d=>{dl.push(d.message().slice(0,40)); await d.accept()});
 await pg.goto(process.argv[2]); await pg.waitForSelector('.dz');
 async function put(slot,role,f){ const [fc]=await Promise.all([pg.waitForEvent('filechooser'),pg.click(`.dz[data-slot="${slot}"][data-role="${role}"]`)]); await fc.setFiles(f); await pg.waitForTimeout(600); }
 await put(1,'見積','/tmp/crash2/tx/q1.pdf'); await put(1,'発注','/tmp/crash2/tx/q2.pdf');
 await pg.click('#go'); await pg.waitForFunction(()=>document.querySelector('#pt').textContent.startsWith('完了'),null,{timeout:120000}); await pg.waitForTimeout(800);
 const btn=pg.locator('button:has-text("見積 ⇔ 見積」のタブへ移す")'); console.log('移すボタンが出る =',await btn.count()>0);
 await btn.click(); await pg.waitForTimeout(1500);
 console.log('タブ =',await pg.innerText('#ttl'),'/ 客先見積の枠 =',await pg.innerText('.dz[data-slot="1"][data-role="客先見積"] b'),'/ 仕入先見積の枠 =',await pg.innerText('.dz[data-slot="1"][data-role="仕入先見積"] b'));
 await b.close();
})();
