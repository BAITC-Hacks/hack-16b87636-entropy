/* Exercise the documented Windy store adapter without sending a real API key. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM,VirtualConsole}=require('../artifacts/ui_checks/node_modules/jsdom');
const html=fs.readFileSync('web/windy.html','utf8'),wait=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const errors=[],calls=[],listeners={};let current=Math.floor(Date.now()/3600000)*3600000;
 const first=current,last=current+7*86400000;
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://windpilot.test/map/windy',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
  w.matchMedia=()=>({matches:true});w.fetch=async()=>({ok:true,json:async()=>({enabled:true,key:'test-key-only',turbines:[{id:1,latitude:43.6,longitude:78.5}],product:'gfs',overlay:'wind',level:'surface'})});
  const append=w.Element.prototype.append;w.Element.prototype.append=function(...nodes){for(const node of nodes){if(node.tagName==='SCRIPT'&&node.src){setTimeout(()=>node.onload(),0)}else append.call(this,node)}};
  w.L={divIcon:()=>({}),marker:()=>({addTo(){return this},bindPopup(){return this}})};
  w.windyInit=(options,ready)=>{ready({overlays:{wind:{setMetric(){}}},map:{invalidateSize(){},fitBounds(){}},store:{get:k=>k==='timestamp'?current:k==='product'?'gfs':k==='overlay'?'wind':'surface',getAllowed:()=> 'Allowed values are checked by function',on:(k,f)=>listeners[k]=f,set(k,v,opts){assert.equal(opts,undefined,'Do not bypass Windy validation');calls.push(v);if(v<first-3600000||v>last)return;current=v;listeners[k]?.()}},broadcast:{on(name,fn){if(name==='redrawFinished')setTimeout(fn,0)}}})};
 }});
 const w=dom.window,d=w.document;for(let i=0;i<100&&!d.getElementById('wp-state').hidden;i++)await wait(20);
 assert.equal(errors.length,0,errors.join('\n'));assert.equal(d.getElementById('wp-date').disabled,false);assert.equal(d.getElementById('wp-state').hidden,true);
 const desired=first+86400000,parts=w.eval(`localParts(${desired})`);
 d.getElementById('wp-date').value=parts.date;d.getElementById('wp-hour').value=parts.hour;d.getElementById('wp-show').click();assert.equal(current,desired);assert.match(d.getElementById('wp-date-note').textContent,/Это время прогноза/);
 const before=calls.length;d.getElementById('wp-date').value='2025-08-01';d.getElementById('wp-show').click();assert.equal(calls.length,before);assert.equal(d.getElementById('wp-date-note').getAttribute('role'),'alert');
 const beyond=w.eval(`localParts(${last+86400000})`);d.getElementById('wp-date').value=beyond.date;d.getElementById('wp-hour').value=beyond.hour;d.getElementById('wp-show').click();assert.equal(current,desired);assert.match(d.getElementById('wp-date-note').textContent,/недоступны/);assert.equal(d.getElementById('wp-date').value,parts.date);
 d.getElementById('wp-now').click();assert.equal(current,first);
 assert.equal(errors.length,0,errors.join('\n'));dom.window.close();console.log('PASS: Windy day/hour, station timezone, past-date rejection, provider bounds, reset and no validation bypass.');
}
main().catch(e=>{console.error(e);process.exit(1)});
