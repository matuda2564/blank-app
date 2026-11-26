const { chromium } = require('playwright');
(async()=>{
 const url=process.argv[2], tx='/tmp/crash2/tx';
 const b=await chromium.launch(); const pg=await b.newPage({viewport:{width:1300,height:900}});
 pg.on('dialog',async d=>{await d.accept()});
 await pg.goto(url); await pg.waitForSelector('.dz');
 // 状態の応答を、ランダムに、0〜4秒遅らせる(重い読み取り中の遅れを、わざと再現)
 await pg.route('**/api/state*', async route=>{ const r=await route.fetch(); await new Promise(res=>setTimeout(res,Math.random()*4000)); await route.fulfill({response:r}); });
 async function put(slot,role,f){ const [fc]=await Promise.all([pg.waitForEvent('filechooser',{timeout:15000}),pg.click(`.dz[data-slot="${slot}"][data-role="${role}"]`,{timeout:15000})]); await fc.setFiles(f); await pg.waitForTimeout(600); }
 const ok=[];
 for(let round=1;round<=3;round++){
   const slot=((round-1)%4)+1, q=round%2?'q1':'q2', o=round%2?'o1':'o2';
   try{
     await put(slot,'見積',`${tx}/${q}.pdf`); await put(slot,'発注',`${tx}/${o}.pdf`);
     await pg.click('#go',{timeout:15000}); await pg.waitForTimeout(300);
     await pg.waitForFunction(()=>document.querySelector('#pt').textContent.startsWith('完了'),null,{timeout:40000});
     await pg.waitForTimeout(14000);   // 遅れた古い返事が届く時間を、十分に待つ
     const dis=await pg.$eval('#go',e=>e.disabled), rdis=await pg.$eval('#reset',e=>e.disabled);
     const clickable=await pg.$eval(`.dz[data-slot="${(slot%4)+1}"]`,e=>true);
     await pg.click(`.slot:nth-child(${slot}) button:has-text("この社をリセット")`,{timeout:10000}); await pg.waitForTimeout(9000);
     const left=await pg.innerText(`.dz[data-slot="${slot}"][data-role="見積"] b`);
     ok.push(`第${round}回(${slot}社): 照合OK / 照合ボタン無効=${dis} / 全部リセット無効=${rdis} / この社をリセット後=${left}`);
   }catch(e){ ok.push(`第${round}回: 失敗 ${String(e).slice(0,100)}`); break; }
 }
 console.log(ok.join('\n')); await b.close();
})();
