const { chromium } = require('playwright'); const { execSync } = require('child_process');
(async()=>{
 const b=await chromium.launch(); const pg=await b.newPage(); await pg.goto(process.argv[2]); await pg.waitForSelector('.dz'); await pg.waitForTimeout(1500);
 console.log('サーバーが動いている間、接続切れの表示:', (await pg.$eval('#conn',e=>e.hidden))?'なし(正常)':'出ている(異常)');
 execSync('kill '+require('fs').readFileSync('/tmp/crash2/h3.pid','utf8').trim()); await pg.waitForTimeout(16000);
 console.log('サーバーを止めて16秒後:', (await pg.$eval('#conn',e=>e.hidden))?'表示なし(異常)':'「'+(await pg.innerText('#conn')).slice(0,60)+'…」'); await b.close();
})();
