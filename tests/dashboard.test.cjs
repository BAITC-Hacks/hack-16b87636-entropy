/* DOM behavior tests; not a claim of visual browser review. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('../artifacts/ui_checks/node_modules/jsdom');
const html=fs.readFileSync(path.join(__dirname,'../dashboard.html'),'utf8');
const snapshot=JSON.parse(html.split('/*DATA_START*/')[1].split('/*DATA_END*/')[0]);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function main(){
 const failures=[],downloads=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>failures.push(e.message));
 const open=(served=false)=>new JSDOM(html,{url:served?'https://windpilot.test/':'file:///dashboard.html',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
  w.scrollTo=()=>{};w.Blob=global.Blob;w.URL.createObjectURL=b=>{downloads.push(b);return 'blob:test'};w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=()=>{};
  if(served){w.localStorage.setItem('windpilot-theme','dark');w.fetch=async url=>{
   if(url.startsWith('/forecast/live')){const run=structuredClone(snapshot.runs['2026-01-31_48']);run.mode='live_demo';run.forecast_origin=new Date().toISOString();run.baseline=[];run.forecast.forEach(r=>r.weather_issued_at=null);return{ok:true,json:async()=>run}}
   if(url==='/agent/status')return{ok:true,json:async()=>({enabled:true,interval_seconds:900,status:'waiting',last_checked_at:new Date().toISOString(),last_run_id:'test-only',last_result:'reused'})};
   if(url==='/weather/status')return{ok:true,json:async()=>({status:'online',checked_at:new Date().toISOString()})};
   return{ok:true,json:async()=>snapshot.runs['2026-01-31_48']};
  }}
 }});
 const dom=open(),w=dom.window,d=w.document;await wait(30);assert.equal(failures.length,0,failures.join('\n'));
 assert.equal(d.querySelectorAll('#powerChart path').length,1);assert.equal(d.querySelectorAll('#hourlyTable tr').length,48);
 assert.equal(d.getElementById('dataNotice').hidden,false);assert.equal(d.getElementById('connectionText').textContent,'Офлайн');
 assert.match(d.getElementById('scopeCaption').textContent,/двух установок/);
 const original=d.getElementById('powerChart').innerHTML;d.getElementById('themeToggle').click();assert.equal(d.documentElement.dataset.theme,'dark');assert.equal(d.getElementById('powerChart').innerHTML,original);
 assert.doesNotMatch(d.querySelector('main').textContent,/MAE|RMSE|MSE|Спросить агента|Загрузить свой CSV/);
 assert.equal(d.querySelector('.wind-scene').getAttribute('aria-hidden'),'true');
 const select=d.getElementById('scopeSelect');select.value='1';select.dispatchEvent(new w.Event('change'));assert.match(d.getElementById('scopeCaption').textContent,/выбранной/);
 d.querySelector('[data-horizon="24"]').click();await wait(20);assert.equal(d.querySelectorAll('#hourlyTable tr').length,24);assert.equal(d.getElementById('horizonTitle').textContent,'24 часа');
 d.getElementById('daySlider').value='28';d.getElementById('daySlider').dispatchEvent(new w.Event('change'));await wait(20);assert.match(d.getElementById('originLabel').textContent,/28.*2026/);
 assert.match(d.getElementById('baselineCaption').textContent,/нет свежих/);
 const calendar=d.getElementById('datePicker');calendar.value='2026-03-01';assert.equal(calendar.validity.rangeOverflow,true);calendar.dispatchEvent(new w.Event('change'));assert.equal(calendar.value,'2026-02-28');
 d.getElementById('download').click();const csv=await downloads[0].text();assert.equal(csv.trim().split('\r\n').length,25);assert.match(csv,/power_pred/);
 d.querySelector('.agent-entry').click();assert.equal(d.getElementById('page-agent').hidden,false);assert.equal(d.querySelectorAll('#agentSteps .step').length,6);
 d.querySelector('[data-page="planner"]').click();assert.equal(d.getElementById('page-planner').hidden,false);assert.equal(d.querySelectorAll('#hourStrip>span').length,24);
 d.getElementById('planMode').value='quiet';d.getElementById('planMode').dispatchEvent(new w.Event('input'));assert.equal(d.getElementById('thresholdField').hidden,true);assert.match(d.getElementById('plannerResult').textContent,/САМЫЙ НИЗКИЙ/);
 const windows=w.eval("planningWindows([{at:'2026-02-01T00:00Z',power:.5},{at:'2026-02-01T01:00Z',power:.6},{at:'2026-02-01T03:00Z',power:.7}],2,.5,false)");assert.equal(windows.length,1);assert.equal(windows[0].start,0);
 const noWindows=w.eval("planningWindows([{at:'2026-02-01T00:00Z',power:.4}],1,.5,false)");assert.equal(noWindows.length,0);
 d.getElementById('liveButton').click();assert.match(d.getElementById('toast').textContent,/запущенного сервера/);assert.equal(d.getElementById('liveNotice').hidden,true);
 d.getElementById('recalculate').click();await wait(20);assert.equal(d.getElementById('errorNotice').hidden,false);assert.match(d.getElementById('errorNotice').textContent,/автономном/);
 d.querySelector('[data-page="help"]').click();assert.match(d.getElementById('page-help').textContent,/оперативное происхождение независимо не подтверждено/);
 d.querySelector('[data-page="map"]').click();assert.equal(d.getElementById('page-map').hidden,false);assert.equal(d.getElementById('windMap').getAttribute('src'),null);assert.equal(d.getElementById('mapPlaceholder').hidden,false);assert.match(d.getElementById('page-map').textContent,/не воспроизводит февральский/);
 dom.window.close();
 const online=open(true),o=online.window.document;await wait(50);assert.equal(o.documentElement.dataset.theme,'dark');assert.equal(o.getElementById('connectionText').textContent,'Онлайн');o.getElementById('themeToggle').click();assert.equal(online.window.localStorage.getItem('windpilot-theme'),'light');
 o.getElementById('liveButton').click();await wait(30);assert.equal(o.getElementById('liveNotice').hidden,false);assert.equal(o.getElementById('weatherBadge').hidden,true);assert.equal(o.getElementById('timelineCard').hidden,true);assert.match(o.getElementById('baselineCaption').textContent,/нет свежих/);assert.match(o.getElementById('autoTitle').textContent,/15 минут/);
 o.getElementById('returnHistory').click();await wait(30);assert.equal(o.getElementById('liveNotice').hidden,true);assert.equal(o.getElementById('timelineCard').hidden,false);
 const savedChart=o.getElementById('powerChart').innerHTML;o.querySelector('[data-page="map"]').click();assert.equal(o.getElementById('windMap').getAttribute('src'),'/map/windy');assert.equal(o.getElementById('windMap').hidden,false);assert.equal(o.body.classList.contains('map-page'),true);o.querySelector('.nav-item[data-page="overview"]').click();assert.equal(o.body.classList.contains('map-page'),false);assert.equal(o.getElementById('powerChart').innerHTML,savedChart);
 online.window.close();assert.equal(failures.length,0,failures.join('\n'));console.log('PASS: real snapshot UI, percent display, date/horizon/scope, hourly table, CSV, planner, six agent steps, honest offline/live modes, themes and preserved limitations.');
}
main().catch(e=>{console.error(e);process.exit(1)});
