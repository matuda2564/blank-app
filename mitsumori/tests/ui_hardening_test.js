const { chromium } = require('playwright');
(async()=>{
 const url=process.argv[2], A='/tmp/crash2/ascii';
 const b=await chromium.launch(); const pg=await b.newPage({viewport:{width:1300,height:900}});
 const dl=[]; pg.on('dialog',async d=>{dl.push(d.message().replace(/\n/g,' ').slice(0,110)); await d.accept()});
 const errs=[]; pg.on('pageerror',e=>errs.push(String(e).slice(0,100)));
 await pg.goto(url); await pg.waitForSelector('.dz'); await pg.waitForTimeout(4000);
 console.log('1 自己診断の警告バナー(出ていなければ正常):', !(await pg.$eval('#hl',e=>e.hidden)) ? '出ている' : '出ていない');
 async function put(slot,role,f){ const [fc]=await Promise.all([pg.waitForEvent('filechooser'),pg.click(`.dz[data-slot="${slot}"][data-role="${role}"]`)]); await fc.setFiles(f); await pg.waitForTimeout(700); }
 await put(1,'見積',A+'/old.xls');
 console.log('2 .xls を入れたときの案内:', dl[dl.length-1]||'(なし)');
 await put(3,'見積',A+'/s1.pdf'); await put(3,'発注',A+'/s2.pdf');
 await pg.click('#go'); await pg.waitForFunction(()=>document.querySelector('#pt').textContent.includes('読み取り中'),null,{timeout:20000});
 console.log('3 読み取り中: 中止ボタンが見える =',!(await pg.$eval('#cancel',e=>e.hidden)),' / 全部リセットは押せない =',await pg.$eval('#reset',e=>e.disabled));
 const t=Date.now(); await pg.click('#cancel'); await pg.waitForFunction(()=>!document.querySelector('#go').disabled,null,{timeout:10000});
 console.log('4 中止後、', Math.round((Date.now()-t)/100)/10,'秒で、「照合する」が、また押せる / 中止ボタンは消えた =',await pg.$eval('#cancel',e=>e.hidden),' / 書類は残っている =',await pg.innerText('.dz[data-slot="3"][data-role="見積"] b'));
 await pg.click('#go'); await pg.waitForFunction(()=>document.querySelector('#pt').textContent.startsWith('完了'),null,{timeout:120000});
 console.log('5 中止のあと、もう一度、照合 → 完了:', await pg.innerText('#pt'));
 // 結果の保存(専用の窓でも動くよう、サーバー経由)
 await pg.click('#save'); await pg.waitForTimeout(1500); console.log('6 結果の保存:', dl[dl.length-1]);
 console.log('ページのエラー:',JSON.stringify(errs));
 await b.close();
})();
