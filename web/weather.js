const siteWeather={data:null,days:7,token:0,busy:false,loadedAt:0};
function weatherCondition(code,night=false){
 if(!finite(code))return {kind:'unknown',label:'Нет данных'};
 if(code===0)return {kind:night?'night':'sun',label:'Ясно'};
 if(code===1||code===2)return {kind:'partly',label:code===1?'Малооблачно':'Переменная облачность'};
 if(code===3)return {kind:'cloud',label:'Пасмурно'};
 if([45,48].includes(code))return {kind:'fog',label:'Туман'};
 if([71,73,75,77,85,86].includes(code))return {kind:'snow',label:'Снег'};
 if([95,96,99].includes(code))return {kind:'storm',label:'Гроза'};
 if([51,53,55,56,57].includes(code))return {kind:'rain',label:'Морось'};
 if([61,63,65,66,67,80,81,82].includes(code))return {kind:'rain',label:'Дождь'};
 return {kind:'unknown',label:'Нет описания'};
}
function weatherIcon(code,night=false){
 const {kind,label}=weatherCondition(code,night),sun='<g stroke="#d69b20" fill="#ffcf5b"><circle cx="28" cy="25" r="11"/><path fill="none" d="M28 7v-4m0 44v-4M10 25H6m44 0h-4M15 12l-3-3m32 32-3-3M15 38l-3 3m32-32-3 3"/></g>',cloud='<path d="M16 43a10 10 0 0 1 0-20 14 14 0 0 1 27 0 10 10 0 0 1 2 20Z" fill="var(--scene-hill)" stroke="var(--scene-mast)"/>';
 let body=kind==='sun'?sun:kind==='night'?'<path d="M40 8a23 23 0 1 0 13 34A23 23 0 0 1 40 8Z" fill="#b7c9ee" stroke="var(--accent)"/>':kind==='unknown'?'<text x="32" y="38" text-anchor="middle" fill="currentColor">—</text>':(kind==='partly'?sun:'')+cloud;
 if(kind==='rain')body+='<path d="m21 49-3 7m15-7-3 7m15-7-3 7" stroke="#559bdf"/>';
 if(kind==='snow')body+='<path d="M20 48v9m-4-4h8m15-5v9m-4-4h8" stroke="#559bdf"/>';
 if(kind==='storm')body+='<path d="m33 43-8 10h7l-3 9 13-14h-9l3-5" fill="#efbe44" stroke="#d69b20"/>';
 if(kind==='fog')body+='<path d="M12 49h40M17 56h30" stroke="var(--muted)"/>';
 return `<svg viewBox="0 0 64 64" class="weather-icon" role="img" aria-label="${label}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}
const weatherTemp=n=>finite(n)?`${n>0?'+':''}${fmt(n,0)}°`:'—';
function windDirection(deg){if(!finite(deg))return '—';const label=['С','СВ','В','ЮВ','Ю','ЮЗ','З','СЗ'][Math.round(deg/45)%8];return `<span class="wind-direction" title="Откуда дует: ${fmt(deg,0)}°"><span aria-hidden="true" style="transform:rotate(${deg}deg)">↓</span>${label}</span>`}
function renderSiteWeather(){
 const data=siteWeather.data;if(!data)return;
 const c=data.current;
 $('weatherContent').hidden=false;$('weatherEmpty').hidden=true;
 $('weatherCoordinates').textContent=`${fmt(data.latitude,5)}° N, ${fmt(data.longitude,5)}° E`;
 $('weatherNowTime').textContent=`СЕЙЧАС · ${timeStr(c.time)} · UTC+5`;
 $('weatherNowIcon').innerHTML=weatherIcon(c.weather_code,c.is_day===0);
 $('weatherNowTemp').textContent=weatherTemp(c.temperature_2m);
 $('weatherNowDescription').textContent=weatherCondition(c.weather_code,c.is_day===0).label;
 $('weatherMetrics').innerHTML=[['Ветер · 10 м',`${fmt(c.wind_speed_10m)} <small>м/с</small>`],['Направление',windDirection(c.wind_direction_10m)],['Порывы · 10 м',`${fmt(c.wind_gusts_10m)} <small>м/с</small>`],['Ветер · 100 м',`${fmt(c.wind_speed_100m)} <small>м/с</small>`],['Влажность',`${fmt(c.relative_humidity_2m,0)} <small>%</small>`],['Получено',`<span class="weather-received">${clockTime(data.received_at)} <small>UTC+5</small></span>`]].map(([label,v])=>`<div class="weather-metric"><span>${label}</span><strong>${v}</strong></div>`).join('');
 const days=data.daily.slice(0,siteWeather.days),enabled=key=>document.querySelector(`[data-weather-param="${key}"]`).checked;
 const row=(label,field,format,cls='')=>`<tr class="${cls}"><th scope="row">${label}</th>${days.map(d=>`<td>${format(d[field])}</td>`).join('')}</tr>`;
 let table=`<thead><tr><th scope="col">Параметр</th>${days.map((d,i)=>`<th scope="col"><span class="weather-day">${i===0?'Сегодня':i===1?'Завтра':humanDate(d.date+'T12:00:00+05:00',{weekday:'short'}).split(',')[0]}</span><span class="weather-date">${humanDate(d.date+'T12:00:00+05:00')}</span>${weatherIcon(d.weather_code)}<span class="weather-condition">${weatherCondition(d.weather_code).label}</span></th>`).join('')}</tr></thead><tbody>`;
 if(enabled('temperature'))table+=row('Температура · макс.', 'temperature_2m_max',weatherTemp,'weather-high')+row('Температура · мин.','temperature_2m_min',weatherTemp,'weather-low');
 if(enabled('wind'))table+=row('Ветер · макс., м/с','wind_speed_10m_max',v=>fmt(v))+row('Порывы · макс., м/с','wind_gusts_10m_max',v=>`<span class="gust-pill">${fmt(v)}</span>`)+row('Направление · 10 м','wind_direction_10m_dominant',windDirection);
 const maxRain=Math.max(1,...days.map(d=>finite(d.precipitation_sum)?d.precipitation_sum:0));
 if(enabled('rain'))table+=row('Осадки за сутки, мм','precipitation_sum',v=>`<div class="rain-cell"><span>${fmt(v)}</span><i style="height:${finite(v)?v/maxRain*30:0}px"></i></div>`);
 $('weatherDaily').innerHTML=table+'</tbody>';
 document.querySelectorAll('[data-weather-days]').forEach(b=>{const active=Number(b.dataset.weatherDays)===siteWeather.days;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))});
}
async function loadSiteWeather(refresh=false){
 if(location.protocol==='file:'){$('weatherEmpty').textContent='Текущая погода доступна через запущенный сервер WindPilot. Откройте сайт по адресу сервера.';return}
 const token=++siteWeather.token,id=Number($('weatherSite').value);siteWeather.busy=true;
 $('weatherRefresh').disabled=true;$('weatherSync').textContent='Синхронизация…';$('weatherError').hidden=true;$('weatherContent').setAttribute('aria-busy','true');
 if(siteWeather.data?.turbine_id!==id){siteWeather.data=null;$('weatherContent').hidden=true;$('weatherEmpty').hidden=false;$('weatherEmpty').textContent='Получаем погоду для выбранной установки…';$('weatherCoordinates').textContent='Координаты загрузятся вместе с погодой'}
 try{
  const data=await api(`/weather/overview?turbine_id=${id}${refresh?'&refresh=true':''}`,35000);
  if(token!==siteWeather.token)return;
  if(data.turbine_id!==id||!data.current||!Array.isArray(data.daily)||data.daily.length!==7||!Number.isFinite(Date.parse(data.current.time))||!Number.isFinite(Date.parse(data.received_at)))throw new Error('Получены неполные погодные данные.');
  siteWeather.data=data;siteWeather.loadedAt=Date.now();renderSiteWeather();
  $('weatherSync').textContent=`Получено ${timeStr(data.received_at)} · UTC+5`;
  connection(true,'Погода площадки получена из Open-Meteo.');
 }catch(error){if(token!==siteWeather.token)return;const saved=Boolean(siteWeather.data);$('weatherError').textContent=(saved?'Обновление не удалось. Показаны прежние данные с указанным временем. ':'Погода недоступна. ')+(error.name==='AbortError'?'Источник не ответил вовремя.':error.message);$('weatherError').hidden=false;$('weatherSync').textContent=saved?'Сохранённые данные · обновление не удалось':'Нет соединения';if(!saved)$('weatherEmpty').textContent='Нажмите «Обновить погоду», чтобы повторить запрос.';connection(false,'Последняя попытка получить погоду завершилась ошибкой.')}
 finally{if(token===siteWeather.token){siteWeather.busy=false;$('weatherRefresh').disabled=false;$('weatherContent').setAttribute('aria-busy','false')}}
}
$('weatherRefresh').addEventListener('click',()=>loadSiteWeather(true));
$('weatherSite').addEventListener('change',()=>loadSiteWeather());
document.querySelectorAll('[data-weather-days]').forEach(b=>b.addEventListener('click',()=>{siteWeather.days=Number(b.dataset.weatherDays);renderSiteWeather()}));
document.querySelectorAll('[data-weather-param]').forEach(b=>b.addEventListener('change',renderSiteWeather));
$('weatherToPower').addEventListener('click',()=>{if(state.busy)return;state.scope=$('weatherSite').value;state.horizon=48;showPage('agent');loadLive()});
setInterval(()=>{if(state.page==='weather'&&!document.hidden&&!siteWeather.busy&&Date.now()-siteWeather.loadedAt>=900000)loadSiteWeather()},60000);
