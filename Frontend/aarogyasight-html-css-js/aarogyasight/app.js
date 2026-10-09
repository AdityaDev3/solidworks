const MAP_STATES=[{"name": "Andaman and Nicobar"}, {"name": "Andhra Pradesh"}, {"name": "Arunachal Pradesh"}, {"name": "Assam"}, {"name": "Bihar"}, {"name": "Chandigarh"}, {"name": "Chhattisgarh"}, {"name": "Dadra and Nagar Haveli"}, {"name": "Daman and Diu"}, {"name": "Delhi"}, {"name": "Goa"}, {"name": "Gujarat"}, {"name": "Haryana"}, {"name": "Himachal Pradesh"}, {"name": "Jharkhand"}, {"name": "Karnataka"}, {"name": "Kerala"}, {"name": "Lakshadweep"}, {"name": "Madhya Pradesh"}, {"name": "Maharashtra"}, {"name": "Manipur"}, {"name": "Meghalaya"}, {"name": "Mizoram"}, {"name": "Nagaland"}, {"name": "Orissa"}, {"name": "Puducherry"}, {"name": "Punjab"}, {"name": "Rajasthan"}, {"name": "Sikkim"}, {"name": "Tamil Nadu"}, {"name": "Tripura"}, {"name": "Uttar Pradesh"}, {"name": "Uttaranchal"}, {"name": "West Bengal"}, {"name": "Jammu and Kashmir"}, {"name": "Ladakh"}];
const ALERTS=[{"message": "High dengue risk in Nagpur", "time": "2 hours ago", "tone": "danger"}, {"message": "Unusual rainfall increase detected in Bhopal", "time": "5 hours ago", "tone": "warm"}, {"message": "Water stagnation risk in Chennai", "time": "1 day ago", "tone": "blue"}, {"message": "Environmental conditions back to normal in Hyderabad", "time": "2 days ago", "tone": "green"}, {"message": "Weekly climate summary is available", "time": "3 days ago", "tone": "blue"}, {"message": "Sensor network check completed in Maharashtra", "time": "3 days ago", "tone": "green"}];
/* Frontend-only demonstration. Replace DEMO_DATA with your backend response. */
const DEMO_DATA = [
 {name:'Nagpur',state:'Maharashtra',risk:72,temperature:33.1,rain:92,humidity:81,change:18,lng:79.0882,lat:21.1458},
 {name:'Bhopal',state:'Madhya Pradesh',risk:64,temperature:31.7,rain:108,humidity:76,change:14,lng:77.4126,lat:23.2599},
 {name:'Chennai',state:'Tamil Nadu',risk:56,temperature:34.2,rain:78,humidity:84,change:9,lng:80.2707,lat:13.0827},
 {name:'Hyderabad',state:'Telangana',risk:38,temperature:30.4,rain:64,humidity:69,change:-6,lng:78.4867,lat:17.385}
];
const adjustments={Dengue:0,Malaria:-11,Chikungunya:-19,Zika:-35};
let disease='Dengue',district=0,zoom=1,apiConnected=false;
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>Array.from(root.querySelectorAll(s));
const score=i=>Math.max(8,DEMO_DATA[i].risk+adjustments[disease]);
const label=r=>r>=60?'High Risk':r>=40?'Moderate Risk':'Low Risk';
function update(){
 const d=DEMO_DATA[district],r=score(district);
 $$('[role=tab]').forEach(b=>{const active=b.textContent.trim()===disease;b.setAttribute('aria-selected',String(active));b.classList.toggle('bg-primary',active);b.classList.toggle('text-primary-foreground',active);b.classList.toggle('text-foreground',!active);});
 const summary=$('.summary-grid');
 if(summary){$('.stat-label',summary).textContent=disease+' Risk';$('.stat-value',summary).textContent=r>=60?'High':r>=40?'Moderate':'Low';}
 const detail=$('[data-panel="district"]');
 if(detail){$('.district-value',detail).textContent=r+'%';$('.risk-badge',detail).textContent=label(r);$('[aria-label="Select district"]',detail).value=district;const vals=$$('.weather-item strong',detail);[d.temperature+'°C',d.rain+' mm',d.humidity+'%'].forEach((v,i)=>vals[i].textContent=v);$('.trend',detail).lastChild.textContent=Math.abs(d.change)+'%';const factors=$$('.factor',detail);factors[0].lastChild.textContent=d.rain>80?'High rainfall in last 2 weeks':'Moderate rainfall in last 2 weeks';factors[1].lastChild.textContent=r>50?'Increase in stagnant water areas':'Stable water levels in monitored areas';factors[2].lastChild.textContent=r>50?'Favorable temperature for vector breeding':'Improving environmental conditions';}
 const map=$('.map-svg');
 if(map){map.setAttribute('aria-label',disease+' sample risk map of India');const tooltip=$('.map-tooltip').parentElement;tooltip.setAttribute('transform',`translate(${(d.lng-58)*15+12} ${(39-d.lat)*12-37})`);const texts=$$('text',tooltip);[d.name,label(r),r+'%'].forEach((v,i)=>texts[i].textContent=v);$$('.map-state').forEach((p,i)=>{const state=MAP_STATES[i].name;let level=['Maharashtra','Madhya Pradesh','Chhattisgarh'].includes(state)?0:['Uttar Pradesh','Bihar','Telangana','Odisha','Jharkhand','West Bengal'].includes(state)?1:['Rajasthan','Gujarat','Andhra Pradesh','Karnataka','Tamil Nadu'].includes(state)?2:3;level=Math.min(3,level+(disease==='Dengue'?0:disease==='Zika'?2:1));p.setAttribute('fill',`var(--risk-${['critical','high','medium','low'][level]})`);});}
 updateCharts();
 const rows=$$('.data-table tbody tr');rows.forEach((row,i)=>{if(row.cells.length===5 && row.closest('table').querySelectorAll('th')[2].textContent==='Disease'){row.cells[2].textContent=disease;row.cells[3].textContent=score(i)+'%';row.cells[4].textContent=label(score(i));}});
 if(apiConnected)renderUnavailable();
}
function renderUnavailable(){
 $$('.district-value,.summary-grid .stat-value').forEach(el=>el.textContent='—');
 $$('.risk-badge').forEach(el=>el.textContent='Unavailable');
 $$('.prediction-line,.prediction-dot,.historical-line,.historical-dot,.historical-area').forEach(el=>el.style.display='none');
 $$('.map-svg').forEach(el=>el.setAttribute('aria-label','Illustrative map; no validated risk data available'));
 $$('.factor').forEach(el=>{el.lastChild.textContent='Data unavailable';});
 $$('.data-table tbody tr').forEach(row=>{if(row.cells.length===5){row.cells[3].textContent='Unavailable';row.cells[4].textContent='Unavailable';}});
 $$('.alert-list').forEach(el=>{el.textContent='No validated predictions or configured alerts are available.';});
 $$('.forecast-svg').forEach(el=>el.setAttribute('aria-label','Forecast unavailable: no validated model is configured'));
 $$('.chart-legend').forEach(el=>el.textContent='Forecast unavailable');
}
function updateCharts(){
 const select=$('[aria-label="Forecast period"]');const count=select?.value==='4'?10:18;
 const predicted=[9,15,20,22,28,35,41,47,52,59,60,67,73,77,75,72,70,69], historical=[5,9,12,14,18,22,19,24,28,30,29,36,42,44,40,35,32,29];
 const adjust=disease==='Dengue'?0:disease==='Zika'?28:13;
 for(const [arr,cls] of [[predicted,'prediction'],[historical,'historical']]){const pts=arr.slice(0,count).map((v,i)=>[34+i*16.5,104-Math.max(3,v-adjust)*.88]);$$('.'+cls+'-line').forEach(line=>line.setAttribute('points',pts.map(p=>p.join(',')).join(' ')));$$('.forecast-svg').forEach(svg=>{$$('.'+cls+'-dot',svg).forEach((dot,i)=>{dot.style.display=i<count?'':'none';if(pts[i]){dot.setAttribute('cx',pts[i][0]);dot.setAttribute('cy',pts[i][1]);}});if(cls==='historical')$('.historical-area',svg)?.setAttribute('d',`M34 104 L${pts.map(v=>v.join(' ')).join(' L')} L${pts.at(-1)[0]} 104Z`);});}
}
function closeModal(){$('.modal-backdrop')?.remove();document.body.style.overflow='';}
function showModal(title,content){closeModal();const wrap=document.createElement('div');wrap.className='modal-backdrop';wrap.innerHTML=`<div class="modal" role="dialog" aria-modal="true" aria-label="${title}"><div class="modal-head"><h2>${title}</h2><button class="inline-flex items-center justify-center size-9 rounded-md hover:bg-accent" aria-label="Close dialog">✕</button></div>${content}</div>`;document.body.appendChild(wrap);document.body.style.overflow='hidden';$('button',wrap).onclick=closeModal;wrap.onclick=e=>{if(e.target===wrap)closeModal();};$('button',wrap).focus();}
$$('[role=tab]').forEach(b=>b.onclick=()=>{disease=b.textContent.trim();update();});
$('[aria-label="Select district"]')?.addEventListener('change',e=>{district=Number(e.target.value);update();});
$('[aria-label="Forecast period"]')?.addEventListener('change',updateCharts);
$$('.map-state').forEach((p,i)=>{p.onclick=()=>{const index=DEMO_DATA.findIndex(d=>d.state===MAP_STATES[i].name);if(index>=0){district=index;update();}};});
$$('.map-svg [role=button]').forEach((p,i)=>{p.onclick=()=>{district=i;update();};p.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){district=i;update();}};});
for(const [name,delta] of [['Zoom in',.25],['Zoom out',-.25]])$(`[aria-label="${name}"]`)?.addEventListener('click',()=>{zoom=Math.max(1,Math.min(2.2,zoom+delta));$('.map-svg>g').setAttribute('transform',`translate(${400*(1-zoom)} ${190*(1-zoom)}) scale(${zoom})`);});
const alertsHtml=()=>apiConnected?'<p>Alerts are unavailable because validated predictions and configured alert rules are not available.</p>':`<div class="alert-list">${ALERTS.map(a=>`<div class="alert-row tone-${a.tone}"><span class="alert-message">${a.message}</span><span class="alert-time">${a.time}</span></div>`).join('')}</div><p>Sample notifications from the demonstration dataset.</p>`;
$$('button').forEach(b=>{const text=b.textContent.trim();if(text==='How It Works')b.onclick=()=>showModal('From signals to prevention','<h3>Environmental data</h3><p>Climate and sensor readings capture temperature, rainfall, humidity, and water conditions.</p><h3>Regional risk analysis</h3><p>A connected prediction model can analyze these signals to estimate disease outbreak risk.</p><h3>District-level insights</h3><p>Risk maps and alerts help public-health teams identify regions that need attention.</p><p>This preview uses sample data. It is not medical advice.</p>');if(text==='Explore Map')b.onclick=()=>$('#risk-map').scrollIntoView({behavior:'smooth',block:'center'});if(text==='View All'||b.getAttribute('aria-label')==='View notifications')b.onclick=()=>showModal('Recent alerts',alertsHtml());if(b.getAttribute('aria-label')==='Open profile')b.onclick=()=>showModal('Radhika Sharma','<h3>Public health analyst</h3><p>Demonstration profile. Account and sign-in services are not connected.</p>');if(text==='Export CSV'||text==='Download report')b.onclick=downloadReport;});
$('[aria-label="Toggle navigation"]')?.addEventListener('click',()=>$('.sidebar').classList.toggle('is-open'));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();$('.search-results')?.remove();}});
const input=$('[aria-label="Search district, city or disease"]');
input?.addEventListener('input',()=>{$('.search-results')?.remove();const q=input.value.toLowerCase().trim();if(!q)return;const results=DEMO_DATA.map((d,i)=>({label:d.name+', '+d.state,index:i})).filter(d=>d.label.toLowerCase().includes(q));Object.keys(adjustments).filter(d=>d.toLowerCase().includes(q)).forEach(d=>results.push({label:d,disease:d}));const box=document.createElement('div');box.className='search-results';if(!results.length){box.textContent='No matching regions or diseases';}results.forEach(result=>{const b=document.createElement('button');b.className='inline-flex w-full items-center rounded-md p-3 text-xs hover:bg-accent';b.textContent=result.label;b.onclick=()=>{if(result.disease)disease=result.disease;else district=result.index;input.value='';box.remove();update();};box.appendChild(b);});$('.search').appendChild(box);});
input?.addEventListener('keydown',e=>{if(e.key==='Enter')$('.search-results button')?.click();});
function downloadReport(){const csv='District,State,Disease,Sample risk (%),Temperature (C),Rainfall (mm),Humidity (%)\n'+DEMO_DATA.map((d,i)=>`${d.name},${d.state},${disease},${score(i)},${d.temperature},${d.rain},${d.humidity}`).join('\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='aarogyasight-sample-report.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

// Backend connectivity is additive: retain the established layout and report honestly
// when the API has no surveillance or validated model data to supply.
(async function connectClimateGuard(){
 const apiBase=(window.CLIMATEGUARD_API_BASE||document.querySelector('meta[name="climateguard-api"]')?.content||'http://127.0.0.1:8000').replace(/\/$/,'');
 const foot=$('.page-foot');if(!foot)return;
 const notice=document.createElement('div');notice.className='api-status-banner';notice.setAttribute('role','status');
 notice.textContent='DEMO DATA · Illustrative values only; no live surveillance or validated forecast is connected.';
 foot.parentElement.insertBefore(notice,foot);
 try{
  const [health,sources]=await Promise.all([fetch(apiBase+'/api/v1/health',{signal:AbortSignal.timeout(3500)}),fetch(apiBase+'/api/v1/data-sources/status',{signal:AbortSignal.timeout(3500)})]);
  if(!health.ok||!sources.ok)throw new Error('API unavailable');
  const [healthData,sourceData]=await Promise.all([health.json(),sources.json()]);
  notice.classList.add('api-connected');
  apiConnected=true;
  notice.textContent=`BACKEND CONNECTED · ${sourceData.items?.find(x=>x.name==='OpenDengue')?.status==='unavailable'?'Dengue surveillance unavailable; predictions withheld.':'Source availability shown below.'} Static metrics and alerts remain illustrative; sensor and weather values appear only after configured ingestion.`;
  // Existing illustration remains visually intact, but is not presented as a backend forecast.
  renderUnavailable();
  $$('.live-badge').forEach(el=>el.textContent='Unavailable');
  notice.dataset.predictionStatus=healthData.predictions;
  const locationsResponse=await fetch(apiBase+'/api/v1/locations',{signal:AbortSignal.timeout(3500)});
  if(locationsResponse.ok){
   const locationData=await locationsResponse.json(),loc=locationData.items?.[0];
   if(loc){
    const weatherResponse=await fetch(apiBase+'/api/v1/dashboard/timeseries?location_id='+encodeURIComponent(loc.id)+'&limit=1',{signal:AbortSignal.timeout(3500)});
    if(weatherResponse.ok){const weatherData=await weatherResponse.json(),row=weatherData.items?.at(-1),values=$$('.district-weather .weather-item strong');if(row&&values.length===3){values[0].textContent=row.temperature_c==null?'Unavailable':row.temperature_c+'°C';values[1].textContent=row.precipitation_mm==null?'Unavailable':row.precipitation_mm+' mm';values[2].textContent=row.humidity_pct==null?'Unavailable':row.humidity_pct+'%';notice.textContent+=` Latest NASA POWER observation for ${loc.name}: ${row.observed_on}.`;}}
   }
  }
  const kitsResponse=await fetch(apiBase+'/api/v1/kits',{signal:AbortSignal.timeout(3500)});
  if(kitsResponse.ok){const kitData=await kitsResponse.json(),kit=kitData.items?.find(item=>item.last_seen);if(kit){const readingsResponse=await fetch(apiBase+'/api/v1/kits/'+encodeURIComponent(kit.id)+'/readings?limit=1',{signal:AbortSignal.timeout(3500)});if(readingsResponse.ok){const readings=(await readingsResponse.json()).items?.[0]?.measurements||{},sensors=$$('.sensor-grid .sensor strong'),names=$$('.sensor-grid .sensor .tiny');const entries=[['temperature_c','C','Air Temperature (kit)'],['water_level_m','m','Water Level (kit)'],['humidity_pct','%','Humidity (kit)'],['rainfall_mm','mm','Rainfall (kit)']];entries.forEach(([key,unit,label],i)=>{if(sensors[i])sensors[i].textContent=readings[key]?`${readings[key].value} ${unit}`:'Unavailable';if(names[i])names[i].textContent=label;});notice.textContent+=` Kit ${kit.id} last received ${kit.last_seen}.`;}}
  }
 }catch(_error){/* Static preview can be opened without a running backend. */}
})();
