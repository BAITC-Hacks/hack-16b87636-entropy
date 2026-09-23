const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM,VirtualConsole}=require('../artifacts/ui_checks/node_modules/jsdom');
const html=fs.readFileSync('dashboard.html','utf8');
const snapshot=JSON.parse(html.split('/*DATA_START*/')[1].split('/*DATA_END*/')[0]);
const wait=()=>new Promise(r=>setTimeout(r,35));
async function main(){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 let fail=false;const calls=[];
 const dom=new JSDOM(html,{url:'http://windpilot.test/#weather',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
  w.scrollTo=()=>{};
  w.fetch=async url=>{
   calls.push(url);
   if(url.startsWith('/weather/overview')){
    if(fail)return {ok:false,status:503,json:async()=>({detail:'TEST_ONLY: weather unavailable'})};
    const id=Number(new URL(url,'http://windpilot.test').searchParams.get('turbine_id'));
    return {ok:true,json:async()=>({turbine_id:id,latitude:43.64,longitude:78.53,received_at:'2026-09-23T11:00:00Z',
     current:{time:'2026-09-23T16:00:00+05:00',temperature_2m:25,weather_code:0,is_day:1,wind_speed_10m:4,wind_speed_100m:6,wind_direction_10m:270,wind_gusts_10m:8,relative_humidity_2m:22},
     daily:Array.from({length:7},(_,i)=>({date:`2026-09-${23+i}`,weather_code:0,temperature_2m_max:27,temperature_2m_min:12,wind_speed_10m_max:5,wind_gusts_10m_max:9,wind_direction_10m_dominant:270,precipitation_sum:i===1?null:0}))})};
   }
   if(url==='/agent/status')return {ok:true,json:async()=>({enabled:false})};
   if(url==='/weather/status')return {ok:true,json:async()=>({status:'online',checked_at:'2026-09-23T11:00:00Z'})};
   return {ok:true,json:async()=>structuredClone(snapshot.runs['2026-01-31_48'])};
  };
 }});
 const w=dom.window,d=w.document;await wait();await wait();
 assert.equal(d.getElementById('page-weather').hidden,false);
 assert.equal(d.getElementById('weatherContent').hidden,false);
 assert.match(d.getElementById('weatherNowTemp').textContent,/25/);
 assert.equal(d.querySelectorAll('#weatherDaily thead th').length,8);
 assert.equal(d.querySelectorAll('#weatherDaily tbody tr').length,6);
 assert.match(d.querySelector('#weatherDaily tbody tr:last-child td:nth-child(3)').textContent,/—/);
 const preserved=d.getElementById('powerChart').innerHTML;
 d.querySelector('[data-weather-days="3"]').click();assert.equal(d.querySelectorAll('#weatherDaily thead th').length,4);
 d.querySelector('[data-weather-param="wind"]').click();assert.equal(d.querySelectorAll('#weatherDaily tbody tr').length,3);
 d.getElementById('themeToggle').click();assert.equal(d.documentElement.dataset.theme,'dark');
 assert.equal(d.getElementById('powerChart').innerHTML,preserved);
 fail=true;d.getElementById('weatherRefresh').click();await wait();
 assert.equal(d.getElementById('weatherError').hidden,false);
 assert.match(d.getElementById('weatherError').textContent,/прежние данные/);
 assert.equal(d.getElementById('weatherContent').hidden,false);
 d.getElementById('weatherSite').value='2';d.getElementById('weatherSite').dispatchEvent(new w.Event('change'));await wait();
 assert.equal(d.getElementById('weatherContent').hidden,true);
 assert.doesNotMatch(d.getElementById('weatherError').textContent,/прежние данные/);
 fail=false;d.getElementById('weatherRefresh').click();await wait();
 assert.equal(d.getElementById('weatherContent').hidden,false);assert.equal(d.getElementById('weatherError').hidden,true);
 assert.ok(calls.some(s=>s.includes('turbine_id=2&refresh=true')));
 d.querySelector('.nav-item[data-page="overview"]').click();assert.equal(d.body.classList.contains('weather-page'),false);
 assert.equal(d.getElementById('powerChart').innerHTML,preserved);
 assert.deepEqual(errors,[]);w.close();
 const offline=new JSDOM(html,{url:'file:///dashboard.html#weather',runScripts:'dangerously',beforeParse(w){w.scrollTo=()=>{}}});
 await wait();assert.match(offline.window.document.getElementById('weatherEmpty').textContent,/запущенный сервер/);assert.equal(offline.window.document.getElementById('weatherContent').hidden,true);offline.window.close();
 console.log('PASS: seven-day weather, parameters, site selection, missing values, refresh failure, stale-data labels, recovery, offline, theme and unchanged power forecast.');
}
main().catch(e=>{console.error(e);process.exit(1)});
