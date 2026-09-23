/* Data invariants and browser DOM interactions; no mock accuracy claims. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const helpers=require('../web/history.js');
const {JSDOM,VirtualConsole}=require('../artifacts/ui_checks/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const snapshot=JSON.parse(read('dashboard.html').split('/*DATA_START*/')[1].split('/*DATA_END*/')[0]);
const html=read('web/dashboard.template.html').replace('/*CSS*/',()=>read('web/dashboard.css')).replace('/*DATA_START*/{}/*DATA_END*/',()=>`/*DATA_START*/${JSON.stringify(snapshot)}/*DATA_END*/`).replace('/*APP*/',()=>['web/dashboard.js','web/weather.js','web/history.js'].map(read).join('\n'));
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn){for(let i=0;i<100;i++){if(fn())return;await wait(10)}throw new Error('DOM did not settle')}
function payload(start,end,{empty=false,predictionOnly=false}={}){const first=helpers.stationMidnight(start),last=helpers.stationMidnight(new Date(Date.parse(end+'T00:00Z')+86400000).toISOString().slice(0,10)),rows=[];for(let t=first;t<last;t+=3600000)for(const turbine_id of [1,2])rows.push({valid_at:new Date(t).toISOString(),turbine_id,power_actual:empty||predictionOnly||(t===first+3600000&&turbine_id===2)?null:turbine_id*.2,power_pred:empty?null:.2+turbine_id*.2,forecast_origin:empty?null:new Date(first-3600000).toISOString(),model_version:empty?null:'validation-sha',forecast_kind:empty?null:'january_validation',wind_speed_actual:empty||predictionOnly?null:6,temperature_actual:empty||predictionOnly?null:-5});return {start,end,timezone:'Asia/Almaty',observed_end:'2026-01-31T23:00:00+05:00',observed_start:'2023-03-11T00:00:00+06:00',rows,notices:[{code:'test',message:'<img src=x onerror=alert(1)> Проверка происхождения'}],forecast_periods:[{start:'2026-01-01',end:'2026-01-31',forecast_kind:'january_validation',model_version:'validation-sha'}]}}
async function main(){
 assert.equal(helpers.validateRange('2026-01-01','2026-01-31'),31);
 assert.throws(()=>helpers.validateRange('2026-02-30','2026-03-01'),/корректные/);
 assert.throws(()=>helpers.validateRange('2026-01-01','2026-02-01'),/31/);
 assert.throws(()=>helpers.validateRange('2026-02-01','2026-01-31'),/раньше/);
 const change=payload('2024-02-29','2024-02-29');assert.equal(change.rows.length,50,'Timezone change adds one local hour');helpers.validatePayload(change,'2024-02-29','2024-02-29');
 const fixture=payload('2026-01-01','2026-01-01');helpers.validatePayload(fixture,fixture.start,fixture.end);
 assert.throws(()=>helpers.validatePayload({...fixture,rows:fixture.rows.slice(1)},fixture.start,fixture.end),/Не все часы/);
 assert.throws(()=>helpers.validatePayload({...fixture,rows:[fixture.rows[0],...fixture.rows.slice(1,-1),fixture.rows[0]]},fixture.start,fixture.end),/повторяющиеся/);
 const series=helpers.aggregate(fixture.rows,'both');assert.ok(Math.abs(series[0].actual-.3)<1e-10);assert.equal(series[1].actual,null,'Never average only one turbine');assert.ok(Math.abs(series[0].deviation-20)<1e-10);assert.equal(helpers.aggregate(fixture.rows,'1')[1].actual,.2);
 const line=helpers.lineSegments(series,'actual',t=>t/3600000,v=>v*100);assert.equal((line.match(/M/g)||[]).length,2,'Missing observations break paths');
 const csv=helpers.toCSV(fixture.rows,'2');assert.equal(csv.split('\r\n').length,25);assert.match(csv,/power_actual,power_pred,deviation_pp/);assert.match(csv,/"","0.6000000000000001",""/,'Missing actual has empty deviation');
 const failures=[],downloads=[],pending=[];let deferAugust=false;
 const vc=new VirtualConsole();vc.on('jsdomError',e=>failures.push(e.message));
 const dom=new JSDOM(html,{url:'https://windpilot.test/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){w.scrollTo=()=>{};w.Blob=global.Blob;w.URL.createObjectURL=b=>{downloads.push(b);return'blob:test'};w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=()=>{};w.fetch=async(url)=>{
  if(url.startsWith('/history?')){const params=new URL(url,'https://windpilot.test').searchParams,start=params.get('start'),end=params.get('end');if(deferAugust&&start==='2025-08-01')return new Promise(resolve=>pending.push(()=>resolve({ok:true,json:async()=>payload(start,end)})));return{ok:true,json:async()=>payload(start,end,{empty:start.startsWith('2026-08'),predictionOnly:start.startsWith('2026-02')})}}
  if(url==='/history/availability')return{ok:true,json:async()=>({observed_end:'2026-01-31T23:00:00+05:00',forecast_periods:[]})};
  if(url==='/weather/status')return{ok:true,json:async()=>({status:'online',checked_at:new Date().toISOString()})};
  if(url==='/agent/status')return{ok:true,json:async()=>({enabled:true,interval_seconds:900,status:'waiting'})};
  if(url.startsWith('/forecast/live')){const run=structuredClone(snapshot.runs['2026-01-31_48']);run.mode='live_demo';run.baseline=[];run.forecast.forEach(r=>r.weather_issued_at=null);return{ok:true,json:async()=>run}}
  return{ok:true,json:async()=>snapshot.runs['2026-01-31_48']};
 }}});
 const w=dom.window,d=w.document;await wait(50);d.getElementById('modeCompare').click();await until(()=>!d.getElementById('historyContent').hidden);
 assert.equal(d.getElementById('page-history').hidden,false);assert.ok(d.body.classList.contains('history-page'));assert.equal(d.getElementById('modeCompare').getAttribute('aria-pressed'),'true');assert.equal(d.querySelectorAll('#historyTable tr').length,744);assert.equal(d.querySelectorAll('#historyNotices img').length,0,'Server strings rendered as text');assert.match(d.getElementById('historyTable').textContent,/\+20,0/);assert.equal(d.getElementById('historyMissing').textContent,'1 ч');
 assert.match(d.getElementById('historyProvenance').textContent,/validation-sha/);assert.doesNotMatch(d.querySelector('main').textContent,/MAE|RMSE|MSE/);
 const original=d.getElementById('historyChart').innerHTML;d.getElementById('themeToggle').click();assert.equal(d.documentElement.dataset.theme,'dark');assert.equal(d.getElementById('historyChart').innerHTML,original);
 d.getElementById('historyDownload').click();assert.equal((await downloads[0].text()).split('\r\n').length,1489);
 d.getElementById('historyScope').value='1';d.getElementById('historyScope').dispatchEvent(new w.Event('change'));assert.equal(d.getElementById('historyMissing').textContent,'0 ч');
 d.getElementById('modeHistory').click();assert.match(d.getElementById('historyTitle').textContent,/История/);
 d.getElementById('historyEnd').value='2026-02-02';d.getElementById('historyForm').dispatchEvent(new w.Event('submit',{cancelable:true}));assert.equal(d.getElementById('historyContent').hidden,true);assert.match(d.getElementById('historyError').textContent,/31/);
 deferAugust=true;d.querySelector('[data-history-period="2025-08"]').click();await until(()=>pending.length>0);d.querySelector('[data-history-period="2026-02"]').click();await until(()=>!d.getElementById('historyContent').hidden);assert.match(d.getElementById('historyNotices').textContent,/Фактические данные.*не загружены/);assert.equal(d.querySelectorAll('.history-line-actual').length,0);assert.equal(d.querySelectorAll('.history-line-predicted').length,1);pending[0]();await wait(40);assert.match(d.getElementById('historyChartSubtitle').textContent,/февр/,'Stale August request cannot replace February');
 d.getElementById('historyStart').value='2026-08-01';d.getElementById('historyEnd').value='2026-08-31';d.getElementById('historyForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await until(()=>!d.getElementById('historyContent').hidden);assert.match(d.getElementById('historyChart').textContent,/нет измерений/);assert.equal(d.querySelectorAll('#historyChart path').length,0);
 d.getElementById('modeToday').click();await until(()=>!d.getElementById('liveNotice').hidden);assert.equal(d.getElementById('page-overview').hidden,false);assert.equal(d.getElementById('modeToday').getAttribute('aria-pressed'),'true');assert.equal(d.getElementById('weatherBadge').hidden,true);
 dom.window.close();assert.equal(failures.length,0,failures.join('\n'));
 const offline=new JSDOM(html,{url:'file:///dashboard.html',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){w.scrollTo=()=>{}}});await wait(30);offline.window.document.getElementById('modeCompare').click();assert.match(offline.window.document.getElementById('historyError').textContent,/через сервер/);assert.equal(offline.window.document.getElementById('historyContent').hidden,true);offline.window.close();
 console.log('PASS: history ranges/timezone transition, truthful gaps, paired means, signed deviation, CSV, missing-data modes, stale request guard, navigation, themes and offline state.');
}
main().catch(e=>{console.error(e);process.exit(1)});
