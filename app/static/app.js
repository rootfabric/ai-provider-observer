const cards = document.querySelector('#cards');
const summary = document.querySelector('#summary');
const mode = document.querySelector('#mode');
const refresh = document.querySelector('#refresh');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = v => typeof v === 'number' ? new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(v) : '—';
const time = v => { if(!v) return 'reset —'; const d=new Date(v); if(Number.isNaN(d.getTime())) return 'reset —'; const ms=d-Date.now(); if(ms<=0) return 'reset скоро'; const h=Math.floor(ms/3600000), m=Math.floor(ms%3600000/60000); return `reset ${h}ч ${m}м`; };
const statusText = s => ({ok:'OK',partial:'PARTIAL',error:'ERROR',disabled:'OFF'})[s] || s;
function windowHtml(p,w){
  const used = typeof w.used_percent==='number' ? w.used_percent : null;
  const trend = p.trends?.windows?.[w.name]?.used_percent_per_hour;
  const cls = used>=90?'bad':used>=75?'warn':'';
  const numeric = w.used!=null && w.limit!=null ? `${num(w.used)} / ${num(w.limit)} ${esc(w.unit||'')}` : '';
  const trendText = typeof trend==='number' ? `${trend>=0?'+':''}${num(trend)} п.п./ч` : 'скорость —';
  return `<div class="window"><div class="windowhead"><span class="windowname">${esc(w.name)}${w.unlimited?' · unlimited':''}</span><span class="windowvalue">${used==null?'—':num(used)+'% used'}</span></div>${used==null?'':`<div class="bar"><div class="fill ${cls}" style="width:${Math.max(0,Math.min(100,used))}%"></div></div>`}<div class="sub"><span>${numeric||trendText}</span><span>${time(w.reset_at)}</span></div>${numeric?`<div class="sub"><span>${trendText}</span><span>${w.remaining!=null?'left '+num(w.remaining):''}</span></div>`:''}</div>`;
}
function balanceHtml(p,b){
  const trend = p.trends?.balances?.[b.currency]?.spend_per_hour;
  return `<div class="balance"><div><div class="big">${num(b.total)} ${esc(b.currency||'')}</div><div class="sub">баланс</div></div><div style="text-align:right"><div>${typeof trend==='number'?num(trend)+' /ч':'—'}</div><div class="sub">расход</div></div></div>`;
}
function card(p){
  const details = Object.entries(p.details||{}).filter(([k])=>!['warning'].includes(k)).slice(0,5).map(([k,v])=>`${esc(k)}: ${esc(typeof v==='object'?JSON.stringify(v):v)}`).join(' · ');
  return `<article class="card"><div class="cardtop"><div><div class="provider">${esc(p.label)}</div><div class="plan">${esc(p.plan||'')}</div></div><span class="status ${esc(p.status)}">${esc(statusText(p.status))}</span></div><div class="meta"><span>check ${p.latency_ms==null?'—':p.latency_ms+' ms'}</span><span>${p.checked_at?new Date(p.checked_at).toLocaleTimeString('ru-RU'):'—'}</span></div>${(p.windows||[]).map(w=>windowHtml(p,w)).join('')}${(p.balances||[]).length?`<div class="balances">${p.balances.map(b=>balanceHtml(p,b)).join('')}</div>`:''}${p.error?`<div class="error">${esc(p.error)}</div>`:''}${!p.error && !(p.windows||[]).length && !(p.balances||[]).length?`<div class="empty">${esc(p.details?.note||'Нет числовых метрик')}</div>`:''}${details?`<div class="details">${details}</div>`:''}</article>`;
}
async function load(){
  const r=await fetch('/api/status',{cache:'no-store'}); const data=await r.json();
  mode.textContent=data.demo_mode?'DEMO MODE':`poll ${data.poll_interval_seconds}s`;
  const ps=data.providers||[]; cards.innerHTML=ps.map(card).join('');
  const ok=ps.filter(p=>p.status==='ok').length, errors=ps.filter(p=>p.status==='error').length, off=ps.filter(p=>p.status==='disabled').length;
  summary.innerHTML=`<div class="stat"><b>${ok}</b> OK</div><div class="stat"><b>${errors}</b> errors</div><div class="stat"><b>${off}</b> not configured</div>`;
}
refresh.addEventListener('click',async()=>{refresh.disabled=true;refresh.textContent='Проверяю…';try{await fetch('/api/refresh',{method:'POST'});await load()}finally{refresh.disabled=false;refresh.textContent='Обновить'}});
load().catch(e=>cards.innerHTML=`<div class="error">${esc(e)}</div>`); setInterval(()=>load().catch(()=>{}),30000);
