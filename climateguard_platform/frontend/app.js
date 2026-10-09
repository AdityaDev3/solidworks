const API_BASE = (localStorage.getItem('cgApiBase') || 'http://127.0.0.1:8000').replace(/\/$/, '');
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const state = { locations:[], selected:'', boundaries:[], sourceItems:[], overview:null, model:null, risk:null, series:[], busy:false };
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateText = value => value ? new Date(value+'T00:00:00Z').toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}) : '—';
const number = (v,d=1) => Number.isFinite(Number(v)) ? Number(v).toLocaleString(undefined,{maximumFractionDigits:d}) : '—';
const statusLabel = s => ({healthy:'Data loaded',not_loaded:'Not loaded',available_on_request:'Available on request',needs_crosswalk:'Needs district crosswalk',credential_required:'Credential required',historical_product_outside_window:'Outside 4-year window',unavailable:'Unavailable',no_validated_vector_feed:'No vector feed'}[s] || s?.replaceAll('_',' ') || 'Unknown');
const statusClass = s => s==='healthy'?'healthy':['available_on_request','needs_crosswalk','credential_required','historical_product_outside_window'].includes(s)?'warning':['unavailable','no_validated_vector_feed'].includes(s)?'error':'';

async function api(path, options={}) {
  const response = await fetch(API_BASE+path,{...options,headers:{...(options.headers||{}),'Accept':'application/json'}});
  const type=response.headers.get('content-type')||'';
  const body=type.includes('json')?await response.json():await response.text();
  if(!response.ok)throw new Error(body?.detail || body?.message || `Request failed (${response.status})`);
  return body;
}
function setApi(stateName, label){const pill=$('#api-status');pill.className=`api-pill ${stateName}`;$('.api-pill span').textContent=label;}
function toast(message,error=false){const node=document.createElement('div');node.className=`toast${error?' error':''}`;node.textContent=message;document.body.append(node);setTimeout(()=>node.remove(),5500);}
function setBusy(button,busy,label){if(!button)return;button.disabled=busy;if(busy){button.dataset.label=button.textContent;button.textContent=label||'Working…';}else if(button.dataset.label){button.textContent=button.dataset.label;delete button.dataset.label;}}

async function boot(){
  $('#data-through').textContent=new Date().toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'});
  try{
    const [health,overview,sources,locations,model]=await Promise.all([api('/api/v1/health'),api('/api/v1/overview'),api('/api/v1/sources'),api('/api/v1/locations'),api('/api/v1/model')]);
    state.overview=overview;state.sourceItems=sources.items||[];state.locations=locations.items||[];state.model=model;
    setApi('connected','API connected');
    drawSources();drawSignals();renderModel();renderCounts();
    const saved=localStorage.getItem('cgLocation');
    state.selected=state.locations.some(x=>x.id===saved)?saved:state.locations[0]?.id||'';
    renderLocations();
    if(state.selected)await chooseLocation(state.selected);
    if(state.locations.length)await loadMap();
    else $('#map-empty').classList.remove('hidden');
    if(health.ollama?.status==='ready')$('#forecast-tag').title=`Ollama narrative model ready: ${health.ollama.model}`;
  }catch(error){
    setApi('offline','Backend unavailable');
    $('#data-through').textContent='No observations loaded';
    $('#source-rows').innerHTML=`<tr><td colspan="5" class="table-empty">Cannot reach the backend at ${esc(API_BASE)}. Start the FastAPI service, then refresh.</td></tr>`;
    drawSignals();$('#map-empty').classList.remove('hidden');
    $('#risk-explanation').textContent='The dashboard has no fabricated fallback values. Start the backend and import real source data to populate this view.';
    toast(error.message||'Backend connection failed.',true);
  }
}

function renderCounts(){
  $('#district-count').textContent=state.overview?.locations?.toLocaleString()||'0';
  const newest=Object.values(state.overview?.categories||{}).map(x=>x.latest).filter(Boolean).sort().at(-1);
  $('#data-through').textContent=newest?dateText(newest):'No observations loaded';
}
function renderLocations(){
  const select=$('#location-select');
  if(!state.locations.length){select.innerHTML='<option value="">Load a district boundary first</option>';return;}
  select.innerHTML=state.locations.map(loc=>`<option value="${esc(loc.id)}">${esc(loc.name)}${loc.admin1?` · ${esc(loc.admin1)}`:''}</option>`).join('');
  select.value=state.selected;
}
async function chooseLocation(id){
  state.selected=id;localStorage.setItem('cgLocation',id);$('#location-select').value=id;
  try{
    const loc=state.locations.find(x=>x.id===id);if(!loc)return;
    const [series,risk,model]=await Promise.all([api(`/api/v1/locations/${encodeURIComponent(id)}/series?limit=15000`),api(`/api/v1/locations/${encodeURIComponent(id)}/risk`),api(`/api/v1/model?location_id=${encodeURIComponent(id)}`)]);
    state.series=series.items||[];state.risk=risk;state.model=model;renderModel();
    renderObservationCards(loc);renderHistory();renderRisk();drawMap();
  }catch(error){toast(`Could not load this district: ${error.message}`,true);}
}
function renderObservationCards(loc){
  const data=state.series;
  const weather=data.filter(x=>['rainfall','temperature_mean','humidity'].includes(x.category));
  const weatherLatest=weather.map(x=>x.observed_on).sort().at(-1);
  const until=weatherLatest?new Date(`${weatherLatest}T00:00:00Z`):null;
  const days=until?new Date(until.getTime()-27*86400000).toISOString().slice(0,10):null;
  const window=weather.filter(x=>x.observed_on>=days&&x.observed_on<=weatherLatest);
  const rain=window.filter(x=>x.category==='rainfall').map(x=>Number(x.value)).filter(Number.isFinite);
  const temp=window.filter(x=>x.category==='temperature_mean').map(x=>Number(x.value)).filter(Number.isFinite);
  const cases=data.filter(x=>x.category==='dengue_cases').sort((a,b)=>a.observed_to.localeCompare(b.observed_to)).at(-1);
  const pop=data.filter(x=>x.category==='population_count').sort((a,b)=>a.observed_on.localeCompare(b.observed_on)).at(-1);
  const density=pop?.metadata?.density_people_per_sq_km;
  $('#metric-rain').innerHTML=rain.length>=21?`${number(rain.reduce((a,b)=>a+b,0))}<small> mm</small>`:'—<small> mm</small>';
  $('#metric-rain-foot').textContent=rain.length>=21?`${rain.length} daily observations · through ${dateText(weatherLatest)}`:`Only ${rain.length} valid daily readings in the recent window (21 required)`;
  $('#metric-temp').innerHTML=temp.length>=21?`${number(temp.reduce((a,b)=>a+b,0)/temp.length)}<small> °C</small>`:'—<small> °C</small>';
  $('#metric-temp-foot').textContent=temp.length>=21?`28-day window ending ${dateText(weatherLatest)}`:`Only ${temp.length} valid daily readings in the recent window`;
  $('#metric-cases').innerHTML=cases?`${number(cases.value,0)}<small> reported</small>`:'—<small> / week</small>';
  $('#metric-cases-foot').textContent=cases?`${dateText(cases.observed_on)} – ${dateText(cases.observed_to)} · ${esc(cases.source)}`:'No dengue case intervals for this district';
  $('#metric-pop').innerHTML=density?`${number(density,0)}<small> / km²</small>`:'—<small> / km²</small>';
  $('#metric-pop-foot').textContent=pop?`WorldPop dataset year ${pop.observed_on.slice(0,4)} · annual estimate`:'Annual population estimate not loaded';
  $('#boundary-vintage').textContent=loc.boundary_year?`Boundary vintage: ${loc.boundary_year}`:'Boundary vintage: unavailable';
}
function renderRisk(){
  const tag=$('#forecast-tag');
  if(state.risk?.status==='available'){
    tag.classList.add('ready');tag.innerHTML='<i></i> CALIBRATED MODEL';
    $('#risk-score').innerHTML=`${Math.round(state.risk.score*100)}<span>%</span>`;
    $('#risk-context').textContent='Calibrated chance that at least one of the next four weekly reports exceeds the training-window baseline.';
    $('#risk-level').textContent=state.risk.risk_level;
    $('#risk-horizon').textContent=`4 weeks · ${dateText(state.risk.period_start)} – ${dateText(state.risk.period_end)}`;
    $('#risk-meter-fill').style.width=`${Math.max(0,Math.min(100,state.risk.score*100))}%`;
    const signals=state.risk.explanation?.signals||{};
    $('#risk-explanation').textContent=state.risk.narrative||`Observed evidence through ${dateText(state.risk.explanation.last_observed_week_end)}: ${number(signals.rainfall_28d_mm)} mm rainfall and ${number(signals.temp_28d_mean_c)} °C mean temperature over 28 days; last reported weekly count ${number(signals.latest_week_cases,0)}. ${state.risk.narrative_source==='Ollama'?'AI explanation summarizes these data; the numeric model score is unchanged.':''}`;
  }else{
    tag.classList.remove('ready');tag.innerHTML='<i></i> MODEL NOT READY';
    $('#risk-score').innerHTML='—<span>%</span>';$('#risk-level').textContent='No validated estimate';
    $('#risk-meter-fill').style.width='0%';
    $('#risk-context').textContent=state.risk?.reason||'A forecast will appear after valid historical data are ready.';
    $('#risk-horizon').textContent='Next four weeks · dengue';
    $('#risk-explanation').textContent='No disease risk is inferred from climate alone. Historical case reporting must align with climate observations before the model can train.';
  }
}
function renderHistory(){
  const items=state.series||[];
  const caseMap=new Map();
  items.filter(x=>x.category==='dengue_cases').forEach(x=>caseMap.set(x.observed_to,{date:x.observed_to,value:Number(x.value)}));
  const weeklyRain=new Map();
  items.filter(x=>x.category==='rainfall').forEach(x=>{
    const d=new Date(`${x.observed_on}T00:00:00Z`),monday=new Date(d);monday.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));const key=monday.toISOString().slice(0,10);
    weeklyRain.set(key,(weeklyRain.get(key)||0)+Number(x.value));
  });
  const casePoints=[...caseMap.values()].filter(x=>Number.isFinite(x.value)).sort((a,b)=>a.date.localeCompare(b.date));
  const rainPoints=[...weeklyRain.entries()].map(([date,value])=>({date,value})).sort((a,b)=>a.date.localeCompare(b.date));
  if(casePoints.length<2&&rainPoints.length<2){$('#chart-empty').classList.remove('hidden');$('#chart-lines').innerHTML='';$('#chart-period').textContent='No compatible observations loaded';return;}
  $('#chart-empty').classList.add('hidden');
  const shownCases=casePoints.slice(-100),shownRain=rainPoints.slice(-100);
  const dates=[...shownCases,...shownRain].map(x=>x.date).sort();const min=dates[0],max=dates.at(-1);const left=45,right=770,top=14,bottom=215;
  const x=d=>left+(max===min?0:(new Date(d+'T00:00:00Z')-new Date(min+'T00:00:00Z'))/(new Date(max+'T00:00:00Z')-new Date(min+'T00:00:00Z')))*(right-left);
  const path=pts=>{const values=pts.map(p=>p.value),lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;return pts.map((p,i)=>`${i?'L':'M'}${x(p.date).toFixed(1)},${(bottom-((p.value-lo)/span)*(bottom-top-8)).toFixed(1)}`).join(' ');};
  $('#chart-grid').innerHTML=[0,1,2,3].map(i=>`<line class="chart-gridline" x1="${left}" x2="${right}" y1="${top+i*(bottom-top)/3}" y2="${top+i*(bottom-top)/3}"/>`).join('');
  $('#chart-lines').innerHTML=`${shownCases.length>1?`<path class="chart-cases" d="${path(shownCases)}"><title>Reported dengue cases by original reporting interval</title></path>`:''}${shownRain.length>1?`<path class="chart-rain" d="${path(shownRain)}"><title>Observed NASA POWER rainfall, weekly sum</title></path>`:''}`;
  $('#chart-period').textContent=`${dateText(min)} – ${dateText(max)} · separate normalized visual scales`;
}
function renderModel(){
  if(state.model?.status!=='ready'){$('#model-state').textContent='Unavailable';$('#model-summary').textContent=state.model?.reason||'No model trained from compatible genuine observations.';return;}
  const m=state.model.metrics||{};$('#model-state').textContent=`READY · ${state.model.version}`;$('#model-state').classList.add('ready');
  $('#model-summary').textContent=`${state.model.validation}. Target: any of the next four weeks above the training-only threshold (${number(m.training_threshold_cases,0)} reported cases).`;
  $('#model-split').textContent=`${number(m.train_records,0)} / ${number(m.calibration_records,0)} / ${number(m.test_records,0)}`;
  $('#model-prauc').textContent=m.model_pr_auc==null?'—':`${number(m.model_pr_auc,3)} · baseline ${number(m.baseline_pr_auc,3)}`;
  $('#model-brier').textContent=m.brier_score==null?'—':`${number(m.brier_score,3)} · baseline ${number(m.baseline_brier_score,3)}`;
  $('#model-date').textContent=dateText(state.model.trained_at?.slice(0,10));
}
function drawSources(){
  const rows=state.sourceItems;
  if(!rows.length){$('#source-rows').innerHTML='<tr><td colspan="5" class="table-empty">No source registry returned.</td></tr>';return;}
  $('#source-rows').innerHTML=rows.map(s=>{
    const cov=s.coverage||{};const coverage=cov.records?`${number(cov.records,0)} observations${cov.categories?.length?` · ${cov.categories.length} variables`:''}`:s.update_note;
    const latest=cov.latest_observation?dateText(cov.latest_observation):'—';
    return `<tr><td>${esc(s.name)}</td><td class="source-category">${esc(s.category)}</td><td class="source-coverage">${esc(coverage)}</td><td>${esc(latest)}</td><td><span class="source-status ${statusClass(s.status)}">${esc(statusLabel(s.status))}</span></td></tr>`;
  }).join('');
}
function drawSignals(){
  const icons={Weather:'☁','Disease surveillance':'⌁','Population density':'♙','Satellite environment':'◌','Surface water':'≈',Mobility:'⇢','Vector & site indicators':'⌖',Geography:'⌑'};
  $('#signal-grid').innerHTML=state.sourceItems.map(s=>`<article class="signal-card"><div class="signal-card-top"><span class="signal-bullet">${icons[s.category]||'◌'}</span><h3>${esc(s.category)}</h3><span class="status-tag ${statusClass(s.status)}">${esc(statusLabel(s.status))}</span></div><p>${esc(s.update_note||'No source metadata available.')}</p></article>`).join('')||'<article class="signal-card"><p>Connect to the API to see the source registry.</p></article>';
}

function ringPath(coords){return coords.map((p,i)=>`${i?'L':'M'}${project(p[0],p[1])}`).join(' ')+'Z';}
function project(lon,lat){const x=55+(lon-67.5)/(98-67.5)*460;const y=7+(37.8-lat)/(37.8-6.5)*344;return `${x.toFixed(1)},${y.toFixed(1)}`;}
function geometryPath(geometry){if(!geometry)return '';const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[];return polygons.map(poly=>poly.map(ringPath).join(' ')).join(' ');}
async function loadMap(){
  try{const collection=await api('/api/v1/locations/geojson?limit=1200');state.boundaries=collection.features||[];$('#map-empty').classList.toggle('hidden',state.boundaries.length>0);drawMap();}
  catch(error){$('#map-empty').classList.remove('hidden');toast(`Map unavailable: ${error.message}`,true);}
}
function drawMap(){
  const shapes=state.boundaries;if(!shapes.length)return;
  $('#map-shapes').innerHTML=shapes.map((feature,i)=>{const p=feature.properties||{};return `<path class="district-shape ${p.id===state.selected?'selected':''}" data-location="${esc(p.id)}" d="${geometryPath(feature.geometry)}" fill-rule="evenodd"><title>${esc(p.name)}${p.admin1?` · ${esc(p.admin1)}`:''}</title></path>`;}).join('');
  $$('.district-shape').forEach(el=>el.addEventListener('click',()=>chooseLocation(el.dataset.location)));
  $('#india-map').setAttribute('aria-label',`${shapes.length} district boundaries; selected ${state.locations.find(x=>x.id===state.selected)?.name||'none'}`);
}

function adminHeaders(){const token=sessionStorage.getItem('cgAdminToken')||$('#admin-token').value.trim();if(!token)throw new Error('Enter the backend admin key first.');sessionStorage.setItem('cgAdminToken',token);return {'X-Admin-Token':token};}
async function adminCall(path,options={}){
  const response=await fetch(API_BASE+path,{...options,headers:{...(options.headers||{}),...adminHeaders()}});
  const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||`Request failed (${response.status})`);return data;
}
async function perform(button,fn,working){
  setBusy(button,true,working);$('#admin-message').classList.remove('error');$('#admin-message').textContent='Working…';
  try{const result=await fn();$('#admin-message').textContent=JSON.stringify(result);toast('Import finished. Refreshing source coverage.');await boot();}
  catch(error){$('#admin-message').textContent=error.message;$('#admin-message').classList.add('error');toast(error.message,true);}
  finally{setBusy(button,false);}
}
function selectedOffset(){return Math.max(0,state.locations.findIndex(x=>x.id===state.selected));}
function csvRows(text){
  // The small crosswalk/feature imports support quoted commas and escaped quotes.
  const lines=text.replace(/^\uFEFF/,'').split(/\r?\n/).filter(x=>x.trim());
  return lines.map(line=>{const cells=[];let value='',quoted=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'&&line[i+1]==='"'&&quoted){value+='"';i++;}else if(c==='"'){quoted=!quoted;}else if(c===','&&!quoted){cells.push(value);value='';}else value+=c;}cells.push(value);return cells;});
}
function wire(){
  $('#refresh').onclick=boot;$('#refresh-sources').onclick=async()=>{try{state.sourceItems=(await api('/api/v1/sources')).items;drawSources();drawSignals();}catch(e){toast(e.message,true);}};
  $('#location-select').onchange=e=>chooseLocation(e.target.value);
  $('#open-admin').onclick=$('#open-admin-top').onclick=()=>{if(sessionStorage.getItem('cgAdminToken'))$('#admin-token').value=sessionStorage.getItem('cgAdminToken');$('#admin-dialog').showModal();};
  $('#load-boundaries').onclick=()=>$('#admin-dialog').showModal();
  $('#sync-boundaries').onclick=e=>perform(e.currentTarget,()=>adminCall('/api/v1/admin/ingest/boundaries',{method:'POST'}),'Loading boundary layer…');
  $('#sync-weather').onclick=e=>{
    if(!state.selected){toast('Load a boundary and choose a district first.',true);return;}
    perform(e.currentTarget,()=>adminCall(`/api/v1/admin/ingest/weather?offset=${selectedOffset()}&limit=1`,{method:'POST'}),'Fetching 4-year weather…');
  };
  $('#sync-population').onclick=e=>{
    if(!state.selected){toast('Load a boundary and choose a district first.',true);return;}
    perform(e.currentTarget,()=>adminCall(`/api/v1/admin/ingest/population?start_year=2022&end_year=2025&offset=${selectedOffset()}&limit=1`,{method:'POST'}),'Summarizing 4 annual estimates…');
  };
  $('#sync-opendengue').onclick=e=>perform(e.currentTarget,()=>adminCall('/api/v1/admin/ingest/opendengue',{method:'POST'}),'Downloading case extract…');
  $('#upload-crosswalk').onclick=()=>$('#crosswalk-file').click();
  $('#crosswalk-file').onchange=e=>{const file=e.target.files[0];if(!file)return;const form=new FormData();form.append('file',file);perform($('#upload-crosswalk'),()=>adminCall('/api/v1/admin/opendengue/crosswalk',{method:'POST',body:form}),'Saving crosswalk…');};
  $('#upload-satellite').onclick=()=>$('#satellite-file').click();
  $('#satellite-file').onchange=e=>{const file=e.target.files[0];if(!file)return;importSignalCsv(file);};
  $('#train-model').onclick=async e=>{
    const token=sessionStorage.getItem('cgAdminToken');if(!token){$('#admin-dialog').showModal();toast('Set the backend admin key in Data setup first.');return;}
    if(!state.selected){toast('Choose a district to train its location-specific model.',true);return;}
    setBusy(e.currentTarget,true,'Training with chronological validation…');try{await adminCall(`/api/v1/admin/model/train?location_id=${encodeURIComponent(state.selected)}`,{method:'POST'});state.model=await api(`/api/v1/model?location_id=${encodeURIComponent(state.selected)}`);renderModel();await chooseLocation(state.selected);toast('Location-specific model trained and evaluated.');}catch(err){toast(err.message,true);}finally{setBusy(e.currentTarget,false);}
  };
}
async function importSignalCsv(file){
  const rows=csvRows(await file.text());if(rows.length<2){toast('CSV has no data rows.',true);return;}
  const header=rows[0].map(x=>x.trim());const required=['location_id','category','observed_on','value','unit','source'];
  if(required.some(x=>!header.includes(x))){toast(`CSV needs columns: ${required.join(', ')}`,true);return;}
  const form=new FormData();form.append('file',file);
  const button=$('#upload-satellite');setBusy(button,true,'Importing measured records…');
  try{const result=await adminCall('/api/v1/admin/observations/import-csv',{method:'POST',body:form});
    $('#admin-message').textContent=`Imported ${result.stored}; duplicates ${result.duplicates}; rejected ${result.rejected}. Review units and quality flags.`;toast(`Imported ${result.stored}; rejected ${result.rejected}.`);state.sourceItems=(await api('/api/v1/sources')).items;drawSources();drawSignals();if(state.selected)await chooseLocation(state.selected);
  }catch(err){toast(err.message,true);}finally{setBusy(button,false);}
}

wire();boot();
