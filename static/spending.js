/* Spending tab on the Cards page: categories with sub-categories, home vs travelling, trips, and one-click recategorising.
   Data comes from _cardsTxnCache (set by renderCards) and is filtered by the shared period (_cardsPeriod). */
let _txnQuery='',_txnLimit=60;
let _spSplit='type';   // sub-rows under a category: 'type' (sub-categories) or 'ctx' (home vs travelling)
let _spStack='card';   // cycle chart stacked by 'card' or 'cat'
let _spTax=null;       // taxonomy from the server
const merchantKey=d=>String(d||'').replace(/[0-9#*\/_.-]+/g,' ').replace(/\s+/g,' ').trim().split(' ').slice(0,3).join(' ').toUpperCase()||'UNKNOWN';
const spQ=id=>document.getElementById(id);
const spTripPlace=t=>(t||'').split(' · ')[1]||'trip';
/* Windows cannot draw flag emoji (it shows GB, US...), so flags are drawn from local SVGs; the emoji stays as the fallback if a file is missing */
const FLAG_RX=/([\u{1F1E6}-\u{1F1FF}])([\u{1F1E6}-\u{1F1FF}])/gu;
function flagHTML(s){
  return esc(s).replace(FLAG_RX,(m,a,b)=>{const c=String.fromCharCode(a.codePointAt(0)-0x1F1E6+97,b.codePointAt(0)-0x1F1E6+97);
    return `<img class="flag" src="/static/flags/${c}.svg" alt="" onerror="this.replaceWith(document.createTextNode('${m}'))">`;});
}

function spFiltered(spend){
  const f=_spendFilter;
  return spend.filter(t=>(!f.category||t.category===f.category)&&(!f.sub||(t.subcategory||'—')===f.sub)&&(!f.trip||t.trip===f.trip));
}

function renderSpendCharts(){
  if(!spQ('cardCatChart'))return;
  if(!_cardsTxnCache){                      // something cleared the cache (a category edit, say): reload it, then draw
    api('/api/spending/transactions?days=800').then(d=>{_cardsTxnCache=d;renderSpendCharts();}).catch(()=>{});
    return;
  }
  const all=_cardsTxnCache;
  // an explicit cycle click is more specific than the period dropdown and overrides it
  const base=_spendFilter.month?all.filter(t=>t.cycle===_spendFilter.month):filterByPeriod(all,_cardsPeriod);
  const spend=base.filter(t=>t.amount>0);
  drawSpKpis(spend);
  drawSpCategories(_spendFilter.trip?spend.filter(t=>t.trip===_spendFilter.trip):spend);
  drawSpTrips(spend);
  const filtered=spFiltered(spend);
  drawSpCycles(all);
  renderUncategorised(spend);

  const merch={};filtered.forEach(t=>{const k=merchantKey(t.description);merch[k]=(merch[k]||0)+t.amount;});
  MiniChart.rankBars(spQ('cardMerchChart'),Object.entries(merch).sort((a,b)=>b[1]-a[1]).map(([label,value])=>({label,value})),{limit:9,format:v=>fmtK(v)});
  const cardTotals={};filtered.forEach(t=>cardTotals[t.card_name]=(cardTotals[t.card_name]||0)+t.amount);
  MiniChart.rankBars(spQ('cardByCardChart'),Object.entries(cardTotals).sort((a,b)=>b[1]-a[1]).map(([label,value])=>({label,value})),{showPct:true,format:v=>fmtK(v)});
  const dow=[0,0,0,0,0,0,0];
  filtered.forEach(t=>{const[y,mo,da]=t.txn_date.split('-').map(Number);dow[new Date(y,mo-1,da).getDay()]+=t.amount;});
  MiniChart.bar(spQ('cardDowChart'),['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map((label,i)=>({label,value:dow[i]})),{highlight:'max',height:200});

  const f=_spendFilter,chip=(k,txt)=>`<span class="due-chip" onclick="clearSpendFilter('${k}')">${flagHTML(txt)} ✕</span>`;
  spQ('spendFilterArea').innerHTML=(f.category||f.sub||f.trip||f.month)?`<div style="margin:0 0 14px"><span class="sub-dim">Showing</span>
    ${f.category?chip('category',f.category):''}${f.sub?chip('sub',f.sub):''}${f.trip?chip('trip',f.trip):''}${f.month?chip('month',fmtMonth(f.month)):''}
    <span class="due-chip" onclick="clearSpendFilter('all')">Clear all</span></div>`:'';

  window._txnRows=filtered;
  spQ('spendTxnArea').innerHTML=filtered.length?`
    <div class="sec"><h2>Transactions</h2><div class="sp-search"><p id="txnCount"></p>
      <input id="txnSearch" type="search" placeholder="Search description, category or trip" value="${esc(_txnQuery)}"></div></div>
    <div class="table-wrap" style="max-height:560px;overflow-y:auto">
      <table class="stmt-table"><thead><tr><th>Date</th><th>Card</th><th>Description</th><th>Category</th><th class="num">${CUR}</th></tr></thead><tbody id="txnBody"></tbody></table>
    </div><div id="txnMore" style="text-align:center;margin-top:12px"></div>`:'';
  if(filtered.length){spQ('txnSearch').oninput=e=>{_txnQuery=e.target.value;_txnLimit=60;drawTxnRows();};drawTxnRows();}
}

function drawSpKpis(spend){
  const total=spend.reduce((s,t)=>s+t.amount,0),away=spend.filter(t=>t.trip).reduce((s,t)=>s+t.amount,0);
  const ds=spend.map(t=>t.txn_date).sort(),days=ds.length?Math.max(1,Math.round((new Date(ds[ds.length-1])-new Date(ds[0]))/86400000)+1):1;
  const cat={};spend.forEach(t=>cat[t.category]=(cat[t.category]||0)+t.amount);
  const top=Object.entries(cat).sort((a,b)=>b[1]-a[1])[0];
  const k=(l,v,m)=>`<div class="kpi"><div class="kpi-l">${l}</div><div class="kpi-v">${v}</div><div class="kpi-m">${m}</div></div>`;
  spQ('spKpis').innerHTML=`<div class="kpis">${k('Spent',`${CUR} ${fmt(total)}`,periodLabel(_cardsPeriod))}${k('A day',`${CUR} ${fmt(total/days)}`,`over ${days} days`)}
    ${k('While travelling',`${CUR} ${fmt(away)}`,total?Math.round(away/total*100)+'% of spend':'')}${k('Biggest category',top?esc(top[0]):'-',top?`${CUR} ${fmt(top[1])} (${Math.round(top[1]/total*100)}%)`:'')}</div>`;
}

function drawSpCategories(src){
  const tot={};
  src.forEach(t=>{const c=tot[t.category]=tot[t.category]||{all:0,trip:0,subs:{}},s=t.subcategory||'—';
    c.all+=t.amount;if(t.trip)c.trip+=t.amount;const x=c.subs[s]=c.subs[s]||{all:0,trip:0};x.all+=t.amount;if(t.trip)x.trip+=t.amount;});
  const rows=Object.entries(tot).sort((a,b)=>b[1].all-a[1].all),max=rows.length?rows[0][1].all:1,total=rows.reduce((s,r)=>s+r[1].all,0)||1;
  const bar=(all,trip,m)=>`<div class="sp-bar"><span class="home" style="width:${(all-trip)/m*100}%"></span><span class="trip" style="width:${trip/m*100}%"></span></div>`;
  const sel=_spendFilter.category;
  const el=spQ('cardCatChart');
  el.innerHTML=`<div class="sp-legend"><span><i class="home"></i>At home</span><span><i class="trip"></i>Travelling</span>
      <span class="sp-seg"><button class="${_spSplit==='type'?'on':''}" data-split="type">By type</button><button class="${_spSplit==='ctx'?'on':''}" data-split="ctx">Home vs travelling</button></span></div>`+
    (rows.map(([c,v])=>`<div class="sp-row ${sel===c?'sel':''}" data-c="${esc(c)}"><div class="sp-top"><span>${esc(c)}</span><b>${CUR} ${fmt(v.all)} <i>${Math.round(v.all/total*100)}%</i></b></div>${bar(v.all,v.trip,max)}</div>`+
      (sel===c?(_spSplit==='type'
        ?Object.entries(v.subs).sort((a,b)=>b[1].all-a[1].all).map(([s,x])=>`<div class="sp-row sp-sub ${_spendFilter.sub===s?'sel':''}" data-s="${esc(s)}"><div class="sp-top"><span>${esc(s)}</span><b>${CUR} ${fmt(x.all)}</b></div>${bar(x.all,x.trip,v.all)}</div>`).join('')
        :`<div class="sp-row sp-sub"><div class="sp-top"><span>At home</span><b>${CUR} ${fmt(v.all-v.trip)}</b></div>${bar(v.all-v.trip,0,v.all)}</div>
          <div class="sp-row sp-sub"><div class="sp-top"><span>Travelling</span><b>${CUR} ${fmt(v.trip)}</b></div>${bar(0,v.trip,v.all)}</div>`):'')).join('')||'<div class="empty" style="padding:24px">No spending in this period.</div>');
  el.onclick=e=>{
    const sp=e.target.closest('[data-split]');if(sp){_spSplit=sp.dataset.split;drawSpCategories(src);return;}
    const sub=e.target.closest('[data-s]');if(sub){_spendFilter.sub=_spendFilter.sub===sub.dataset.s?null:sub.dataset.s;renderSpendCharts();return;}
    const r=e.target.closest('[data-c]');if(r){_spendFilter.category=_spendFilter.category===r.dataset.c?null:r.dataset.c;_spendFilter.sub=null;renderSpendCharts();}};
}

function drawSpTrips(spend){
  const tr={};
  spend.filter(t=>t.trip).forEach(t=>{const x=tr[t.trip]=tr[t.trip]||{label:t.trip,total:0,n:0,min:t.txn_date,max:t.txn_date,cats:{}};
    x.total+=t.amount;x.n++;if(t.txn_date<x.min)x.min=t.txn_date;if(t.txn_date>x.max)x.max=t.txn_date;x.cats[t.category]=(x.cats[t.category]||0)+t.amount;});
  // a trip's real dates come from its on-the-ground charges over all time, not from whatever the chosen period happens to contain
  const span={};(_cardsTxnCache||[]).forEach(t=>{if(!t.trip||t.amount<=0||t.category==='Travel')return;const x=span[t.trip]=span[t.trip]||{min:t.txn_date,max:t.txn_date};if(t.txn_date<x.min)x.min=t.txn_date;if(t.txn_date>x.max)x.max=t.txn_date;});
  Object.values(tr).forEach(x=>{const sp=span[x.label];if(sp){x.smin=sp.min;x.smax=sp.max;}else{x.smin=x.min;x.smax=x.max;}});
  const list=Object.values(tr).sort((a,b)=>b.smin.localeCompare(a.smin));
  const d=iso=>new Date(iso+'T00:00:00').toLocaleDateString('en-GB',{day:'numeric',month:'short'});
  const el=spQ('spTrips');
  el.innerHTML=list.length?list.map(x=>{const days=Math.round((new Date(x.smax)-new Date(x.smin))/86400000)+1;
    return `<div class="sp-trip-row ${_spendFilter.trip===x.label?'sel':''}" data-trip="${esc(x.label)}"><div class="sp-top"><span><b>${flagHTML(x.label)}</b><div class="fine">${d(x.smin)} to ${d(x.smax)} · ${x.n} charges</div></span>
      <b>${CUR} ${fmt(x.total)}<div class="fine" style="text-align:right">${fmt(x.total/days)} a day</div></b></div>
      <div class="sp-chips">${Object.entries(x.cats).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([c,v])=>`<span class="rw-chip">${esc(c)} ${fmt(v)}</span>`).join('')}</div></div>`;}).join('')
    :'<div class="empty" style="padding:24px">No trips in this period. A trip is detected from spending in foreign currencies.</div>';
  el.onclick=e=>{const r=e.target.closest('[data-trip]');if(!r)return;_spendFilter.trip=_spendFilter.trip===r.dataset.trip?null:r.dataset.trip;renderSpendCharts();};
}

function drawSpCycles(all){
  const monSource=all.filter(t=>t.amount>0&&(!_spendFilter.category||t.category===_spendFilter.category)&&(!_spendFilter.sub||(t.subcategory||'—')===_spendFilter.sub)&&(!_spendFilter.trip||t.trip===_spendFilter.trip));
  const cyc12=((_cyc&&_cyc.months)||[]).slice(-12),keys=cyc12.map(m=>m.month);
  const dim=_spStack==='cat'?(t=>t.category):(t=>t.card_name);
  const totals={};monSource.forEach(t=>totals[dim(t)]=(totals[dim(t)]||0)+t.amount);
  const names=Object.entries(totals).sort((a,b)=>b[1]-a[1]).map(x=>x[0]);
  const keep=_spStack==='cat'?names.slice(0,8):names.sort();
  const cell={};monSource.forEach(t=>{const n=keep.includes(dim(t))?dim(t):'Other categories';cell[t.cycle+'|'+n]=(cell[t.cycle+'|'+n]||0)+t.amount;});
  const series=[...keep,...(_spStack==='cat'&&names.length>8?['Other categories']:[])];
  spQ('spStackSeg').innerHTML=`<button class="${_spStack==='card'?'on':''}" data-st="card">By card</button><button class="${_spStack==='cat'?'on':''}" data-st="cat">By category</button>`;
  spQ('spStackSeg').onclick=e=>{const b=e.target.closest('[data-st]');if(b){_spStack=b.dataset.st;drawSpCycles(all);}};
  MiniChart.stackedColumns(spQ('cardMonthChart'),keys.map((k,i)=>fmtMonth(k)+(cyc12[i].complete?'':'*')),
    series.map(n=>({name:n,data:keys.map(k=>cell[k+'|'+n]||0)})),
    {colors:MiniChart.pal(),height:300,onClick:i=>{const k=keys[i];if(!k)return;_spendFilter.month=_spendFilter.month===k?null:k;renderSpendCharts();}});
}

function drawTxnRows(){
  const q=_txnQuery.trim().toLowerCase(),rows=(window._txnRows||[]).filter(t=>!q||[t.description,t.category,t.subcategory,t.trip].some(x=>(x||'').toLowerCase().includes(q)));
  const body=spQ('txnBody');if(!body)return;
  body.innerHTML=rows.slice(0,_txnLimit).map(t=>`<tr><td>${t.txn_date}</td><td><span class="sub-dim">${esc(t.card_name)}</span></td>
    <td class="clickable-row" onclick="openStatementDetail(${t.statement_id})">${esc(t.description)}${t.fx_currency?`<div class="fine">${esc(t.fx_currency)} ${fmt2(Math.abs(t.fx_amount))}</div>`:''}</td>
    <td><span class="pill sp-cat" onclick="editTxnCategory(${t.id})" title="Change category">${esc(t.category)}${t.subcategory?' › '+esc(t.subcategory):''}</span>${t.trip?`<span class="sp-tag ${t.trip_manual?'pinned':''}" onclick="editTxnTrip(${t.id})" title="Change trip">${flagHTML(spTripPlace(t.trip))}</span>`:(t.category==='Travel'?`<span class="sp-tag none" onclick="editTxnTrip(${t.id})">+ trip</span>`:'')}</td>
    <td class="num">${fmt2(t.amount)}</td></tr>`).join('')||'<tr><td colspan="5"><div class="empty" style="padding:20px">Nothing matches.</div></td></tr>';
  const sum=rows.reduce((a,t)=>a+t.amount,0);
  spQ('txnCount').textContent=`${rows.length} transaction${rows.length!==1?'s':''} · ${CUR} ${fmt2(sum)}${rows.length>_txnLimit?` · showing ${_txnLimit}`:''}`;
  spQ('txnMore').innerHTML=rows.length>_txnLimit?`<button class="btn ghost sm" onclick="_txnLimit+=200;drawTxnRows()">Show more</button>`:'';
}

/* ───────── recategorising ───────── */
async function spTaxonomy(){
  if(!_spTax){const r=await api('/api/spending/taxonomy');_spTax=r.taxonomy;}
  const custom=(await api('/api/spending/categories/custom').catch(()=>[])).map(c=>c.name);
  const flat=[];
  Object.entries(_spTax).forEach(([c,subs])=>{if(c==='Payments & Transfers')return;subs.forEach(s=>flat.push(s?`${c} › ${s}`:c));});
  custom.forEach(c=>{if(!flat.includes(c))flat.push(c);});
  return flat;
}
async function editTxnTrip(tid){
  const t=(_cardsTxnCache||[]).find(x=>x.id===tid);if(!t)return;
  const trips=await api('/api/spending/trips');
  const opts=['Automatic','No trip',...trips.map(x=>x.label)];
  const cur=t.trip_manual==='-'?'No trip':t.trip_manual?t.trip_manual:'Automatic';
  const v=await dialog({title:'Which trip is this for?',text:`${t.description.replace(/\s+/g,' ').trim()} · ${t.txn_date}. Bookings are matched to a trip automatically; pin it here if that is wrong.`,ok:'Save',
    fields:[{key:'trip',label:'Trip',options:opts,value:opts.includes(cur)?cur:'Automatic'}]});
  if(!v)return;
  await api(`/api/transactions/${tid}/trip`,{method:'PUT',body:{trip:v.trip==='Automatic'?'':v.trip==='No trip'?'-':v.trip}});
  _cardsTxnCache=await api('/api/spending/transactions?days=800');renderSpendCharts();
}
function stmtCatCell(t,flat){
  const cur=t.subcategory?`${t.category} › ${t.subcategory}`:t.category;
  const opts=(flat.includes(cur)?flat:[cur,...flat]).map(f=>`<option value="${esc(f)}" ${f===cur?'selected':''}>${esc(f)}</option>`).join('')+'<option value="__new__">+ New category…</option>';
  return `<td><select data-prev="${esc(cur)}" onchange="(async()=>{const c=await resolveNewCategory(this);if(c){const p=c.split(' › ');updateTxnCategory(${t.id},p[0],p[1]||'');}})()">${opts}</select></td>`;
}
async function editTxnCategory(tid){
  const t=(_cardsTxnCache||[]).find(x=>x.id===tid);if(!t)return;
  const flat=await spTaxonomy(),cur=t.subcategory?`${t.category} › ${t.subcategory}`:t.category;
  const v=await dialog({title:'Change category',text:`${t.description.replace(/\s+/g,' ').trim()}: this applies to every charge from this merchant, past and future.`,ok:'Save',
    fields:[{key:'cs',label:'Category',options:flat,value:flat.includes(cur)?cur:flat[0]}]});
  if(!v)return;
  const [category,sub]=v.cs.split(' › ');
  const r=await api(`/api/transactions/${tid}`,{method:'PUT',body:{category,subcategory:sub||''}});
  toast(`Moved ${r.updated??'the'} transactions`);
  _cardsTxnCache=await api('/api/spending/transactions?days=800');renderSpendCharts();
}
async function renderUncategorised(spend){
  const box=spQ('uncatArea');if(!box)return;
  const other=spend.filter(t=>t.category==='Other'),otherTotal=other.reduce((a,t)=>a+t.amount,0),total=spend.reduce((a,t)=>a+t.amount,0);
  const ai=await api('/api/spending/ai').catch(()=>null);
  if(!ai||(!other.length&&!ai.waiting)){box.innerHTML='';return;}
  const by={};other.forEach(t=>{const k=merchantKey(t.description);(by[k]=by[k]||{key:k,total:0,n:0,tid:t.id}).total+=t.amount;by[k].n++;});
  const top=Object.values(by).sort((a,b)=>b.total-a.total).slice(0,5);
  const flat=await spTaxonomy();
  box.innerHTML=`<div class="chart-card wide" style="margin-bottom:20px"><h3>Not sorted yet <span class="sub-dim">${CUR} ${fmt(otherTotal)}${total?` (${Math.round(otherTotal/total*100)}% of spend)`:''}</span></h3>
    <div class="fine" style="margin:-4px 0 10px">New merchants are sorted by rules first, then by AI on the cheapest model, once per merchant. Anything left, pick below and it is remembered.</div>
    ${top.map(m=>`<div class="budget-row"><span class="br-label"><b>${esc(m.key)}</b> <span class="sub-dim">${m.n} charge${m.n>1?'s':''}</span></span><span class="br-amount">${CUR} ${fmt(m.total)}</span>
      <select onchange="spPick(this,${m.tid})"><option value="">Choose…</option>${flat.map(f=>`<option>${esc(f)}</option>`).join('')}</select></div>`).join('')}
    <div class="pl-acts"><label class="check" style="font-size:13px"><input type="checkbox" id="spAiOn" ${ai.enabled?'checked':''}> Use AI for new unknown merchants</label>
      <button class="btn ghost sm" id="spAiGo" ${ai.waiting?'':'disabled'}>${ai.waiting?`Sort ${ai.waiting} merchants with AI now`:'Nothing waiting for AI'}</button><span class="fine" id="spAiMsg"></span></div></div>`;
  spQ('spAiOn').onchange=e=>api('/api/spending/ai',{method:'PUT',body:{enabled:e.target.checked}});
  spQ('spAiGo').onclick=async()=>{const m=spQ('spAiMsg');spQ('spAiGo').disabled=true;m.textContent='Asking the model…';
    try{const r=await api('/api/spending/ai-classify',{method:'POST'});
      if(r.ok){m.textContent=`Sorted ${r.classified} of ${r.asked}.`;_cardsTxnCache=await api('/api/spending/transactions?days=800');renderSpendCharts();}
      else m.textContent=r.error||'Could not sort them.';}catch(e){m.textContent='Could not reach the model.';}};
}
async function spPick(sel,tid){
  if(!sel.value)return;const [category,sub]=sel.value.split(' › ');
  await api(`/api/transactions/${tid}`,{method:'PUT',body:{category,subcategory:sub||''}});
  _cardsTxnCache=await api('/api/spending/transactions?days=800');renderSpendCharts();
}
async function pickPeriod(){
  const now=new Date(),ym=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
  for(;;){
    const v=await dialog({title:'Choose a period',text:'A calendar month, one statement cycle (each card closes on its own day), or any dates.',ok:'Show',fields:[
      {key:'kind',label:'Type',options:['Calendar month','Statement cycle','Date range'],value:'Calendar month'},
      {key:'month',label:'Month (for the first two)',type:'month',value:ym},
      {key:'from',label:'From (date range)',type:'date'},{key:'to',label:'To (date range)',type:'date'}]});
    if(!v)return null;
    if(v.kind==='Date range'){
      if(v.from&&v.to&&v.from<=v.to)return `rng:${v.from}..${v.to}`;
      toast('Pick a start date and an end date, start first','err');continue;
    }
    if(/^\d{4}-\d{2}$/.test(v.month))return (v.kind==='Statement cycle'?'cyc:':'mon:')+v.month;
    toast('Pick a month','err');
  }
}
function clearSpendFilter(which){
  if(which==='all')_spendFilter={category:null,sub:null,trip:null,month:null};
  else{_spendFilter[which]=null;if(which==='category')_spendFilter.sub=null;}
  renderSpendCharts();
}
async function recategorizeAll(){
  try{
    const r=await api('/api/spending/recategorize',{method:'POST'});
    toast(r.changed?`Re-sorted ${r.changed} transaction${r.changed!==1?'s':''}`:'Everything was already sorted');
    _cardsTxnCache=null;renderCards();
  }catch{toast('Re-classify failed','err');}
}
