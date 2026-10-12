/* Aurum — frontend */
const CLASS_LABELS={stock:'Stocks',etf:'ETFs',mutual_fund:'Mutual Funds',bond:'Bonds',savings:'Savings',gold:'Gold',crypto:'Crypto',cash:'Cash',other:'Other',all:'All Holdings'};
const CLASS_SUB={stock:'Individual company shares · live priced',etf:'Exchange-traded funds · live priced',mutual_fund:'Funds · priced once a day',bond:'Fixed income',savings:'Savings & deposit accounts',gold:'Physical & allocated gold',crypto:'Digital assets',cash:'Current & cash accounts',other:'Other assets',all:'Everything in one place'};

const DEFAULT_GRAD='#2C3A35';

let summary=null,editingHoldingId=null,editingCardId=null,payingStmtId=null,addStmtCardId=null,quickUpdateHoldingId=null;
let currentPage='dashboard',appSettings={},CUR='USD';   // CUR: the app currency (Settings > Currency); every amount is stored and shown in it

const sevIcon=sev=>`<svg class="i"><use href="#i-${sev==='high'?'alert':sev==='medium'?'alert':'spark'}"/></svg>`;
/* ─── in-app dialogs (native confirm/prompt are silently blocked in some embedded windows) ─── */
function dialog({title,text='',fields=[],ok='OK',danger=false}){
  return new Promise(resolve=>{
    const ov=document.getElementById('dlgOverlay'),okB=document.getElementById('dlgOk'),cancelB=document.getElementById('dlgCancel');
    document.getElementById('dlgTitle').textContent=title;
    document.getElementById('dlgText').textContent=text;
    document.getElementById('dlgFields').innerHTML=fields.map((f,i)=>`<label>${esc(f.label)}${f.options
      ?`<select data-k="${f.key}">${f.options.map(o=>`<option ${o===f.value?'selected':''}>${esc(o)}</option>`).join('')}</select>`
      :`<input data-k="${f.key}" type="${f.type||'text'}" ${f.step?`step="${f.step}"`:''} value="${esc(f.value??'')}" ${i===0?'data-first':''}>`}</label>`).join('');
    okB.textContent=ok;okB.className='btn '+(danger?'danger':'solid');
    const done=v=>{ov.classList.remove('show');okB.onclick=cancelB.onclick=ov.onkeydown=null;resolve(v);};
    okB.onclick=()=>{const out={};ov.querySelectorAll('[data-k]').forEach(e=>out[e.dataset.k]=e.value);done(out);};
    cancelB.onclick=()=>done(null);
    ov.onkeydown=e=>{if(e.key==='Escape')done(null);else if(e.key==='Enter'&&e.target.tagName!=='SELECT'){e.preventDefault();okB.click();}};
    ov.classList.add('show');
    setTimeout(()=>(ov.querySelector('[data-first]')||okB).focus(),30);
  });
}
const askConfirm=(text,{title='Are you sure?',ok='Confirm',danger=true}={})=>dialog({title,text,ok,danger}).then(r=>r!==null);

/* ─── theme ─────────────────────────────────────────────── */
function applyTheme(theme){
  document.documentElement.setAttribute('data-theme',theme);
  try{localStorage.setItem('theme',theme);}catch(e){}
}
async function setTheme(theme){
  applyTheme(theme);
  api('/api/settings',{method:'PUT',body:{theme}}).catch(()=>{});
  const active=document.querySelector(`#themeToggle .seg-btn[data-theme="${theme}"]`);
  if(active)document.querySelectorAll('#themeToggle .seg-btn').forEach(b=>b.classList.toggle('active',b===active));
  go(currentPage); // charts bake theme colors into their config at render time — re-render to pick it up
}

const fmt=n=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Math.round(n||0));
const fmtK=n=>MiniChart.fmtK(n);
const fmt2=n=>new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n||0);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ejs=s=>esc(String(s==null?'':s).replace(/[\\'"`<>&\r\n\u2028\u2029]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0')));
const localISO=d=>{const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),dd=String(d.getDate()).padStart(2,'0');return `${y}-${m}-${dd}`;};
const today=()=>localISO(new Date());

async function api(path,opts){
  const r=await fetch(path,{credentials:'same-origin',...opts,headers:{'Content-Type':'application/json','X-Wealth':'1',...(opts&&opts.headers)},
    body:opts&&opts.body?JSON.stringify(opts.body):undefined});
  if(r.status===401){const d=await r.clone().json().catch(()=>({}));if(window.onSessionEnded)window.onSessionEnded(d.error);}
  if(!r.ok)throw new Error(await r.text());
  return r.json();
}
function toast(m,k='ok'){const t=document.getElementById('toast');t.textContent=m;t.className='toast show '+k;setTimeout(()=>t.className='toast',2600);}
const setV=(id,v)=>{const e=document.getElementById(id);if(e)e.value=v??''};
const getV=id=>document.getElementById(id).value;

/* ─── routing ───────────────────────────────────────────── */
const ASSET_CLASS_PAGES=['stock','etf','mutual_fund','bond','savings','gold','crypto','cash','other','all'];
function navKeyFor(page){return ASSET_CLASS_PAGES.includes(page)?'all':page;}
function go(page){
  currentPage=page;
  MiniChart.destroyAll();
  const navKey=navKeyFor(page);
  document.querySelectorAll('.tab,.bn-item').forEach(t=>t.classList.toggle('active',t.dataset.page===navKey));
  document.body.classList.toggle('on-assistant',page==='assistant');
  ['dashboard','cards','bank','budget','income','plan','category','market','assistant'].forEach(p=>{
    const el=document.getElementById('page-'+p);
    if(el)el.classList.toggle('hidden',p!==page&&!(p==='category'&&ASSET_CLASS_PAGES.includes(page)));
  });
  window.scrollTo({top:0,behavior:'instant'});
  if(page==='dashboard')renderDashboard();
  else if(page==='cards')renderCards();
  else if(page==='budget')renderBudget();
  else if(page==='bank')renderBank();
  else if(page==='income')renderIncome();
  else if(page==='plan')renderPlan();
  else if(page==='assistant')renderAssistant();
  else if(page==='market')renderMarket();
  else{document.getElementById('page-category').classList.remove('hidden');renderCategory(page);}
}

/* ─── dashboard ─────────────────────────────────────────── */
function readinessRingSVG(score,color,size){
  size=size||132;
  const stroke=9,r=(size-stroke)/2,cx=size/2,cy=size/2,circ=2*Math.PI*r;
  const pct=Math.max(0,Math.min(100,score||0))/100;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" style="stroke:var(--track)" stroke-width="${stroke}"/>
    <circle class="readiness-ring-arc" cx="${cx}" cy="${cy}" r="${r}" fill="none" style="stroke:${color}" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${circ}" stroke-dashoffset="${circ}" data-target="${circ*(1-pct)}" transform="rotate(-90 ${cx} ${cy})"/>
  </svg>`;
}
function animateReadinessRings(){
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    document.querySelectorAll('.readiness-ring-arc').forEach(el=>{el.style.strokeDashoffset=el.dataset.target;});
  }));
}

/* ─── dashboard ─────────────────────────────────────────── */
let _dashTok=0;
const _proj={ret:5,years:5,extra:null};
const MONTHS_SHORT=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const fmtMonth=ym=>ym?`${MONTHS_SHORT[+ym.slice(5,7)-1]} ${ym.slice(2,4)}`:'';
const addMonths=n=>{const d=new Date();d.setMonth(d.getMonth()+n);return d.toLocaleDateString('en-US',{month:'short',year:'numeric'});};

/* future value with monthly compounding; returns balance after each month 0..n */
function growthPath(start,monthly,annualPct,n,outs){
  const r=annualPct/100/12,out=[start];
  for(let i=1;i<=n;i++)out.push(Math.max(0,out[i-1]*(1+r)+monthly-((outs&&outs[i])||0)));
  return out;
}
function monthsToReach(start,monthly,annualPct,target,cap=600){
  if(start>=target)return 0;
  const r=annualPct/100/12;let v=start;
  for(let i=1;i<=cap;i++){v=v*(1+r)+monthly;if(v>=target)return i;}
  return null;
}
function monthlyCardSpend(txns){
  // a month counts only once the data reaches a later month (statements lag, so a month that just ended is partial)
  const latest=(txns||[]).reduce((mx,t)=>t.txn_date>mx?t.txn_date:mx,'');
  const cur=latest?latest.slice(0,7):localISO(new Date()).slice(0,7),m={};
  (txns||[]).forEach(t=>{if(t.amount>0){const k=t.txn_date.slice(0,7);if(k!==cur)m[k]=(m[k]||0)+t.amount;}});
  return Object.keys(m).sort().map(k=>({month:k,total:m[k]}));
}

async function renderDashboard(){
  const tok=++_dashTok,el=document.getElementById('page-dashboard');
  const alive=()=>tok===_dashTok&&currentPage==='dashboard';
  const [s,dues,hist,whatChanged]=await Promise.all([api('/api/summary'),api('/api/dues?days=14'),
    api('/api/history').catch(()=>[]),api('/api/what-changed').catch(()=>null)]);
  if(!alive())return;
  summary=s;updateBadges();checkBillReminders(dues);
  const nHold=Object.values(s.class_counts).reduce((a,b)=>a+b,0);
  const changeHTML=whatChanged?`<div class="hero-change"><svg class="i ${whatChanged.direction==='up'?'pnl-pos':'pnl-neg'}"><use href="#i-${whatChanged.direction==='up'?'up':'down'}"/></svg>
    <span><b>What changed.</b> ${esc(whatChanged.summary)}</span></div>`:'';
  el.innerHTML=`
    <section class="nw-hero">
      <div>
        <div class="hero-label">Net worth</div>
        <div class="hero-val"><span class="cur">${CUR}</span>${fmt(s.total_base)}</div>
        <div class="hero-meta">${nHold} holdings across ${s.by_class.length} asset classes${fxLine()?' · '+fxLine():''}</div>
        ${changeHTML}
        <div class="hero-split">
          <div style="--dot:var(--info)"><div class="hs-lbl">Safe</div><div class="hs-val">${fmt(s.safe_base)}</div><div class="hs-meta">${s.safe_pct}% of total</div></div>
          <div style="--dot:var(--accent)"><div class="hs-lbl">Growth</div><div class="hs-val">${fmt(s.growth_base)}</div><div class="hs-meta">${s.growth_pct}% of total</div></div>
          ${dues.length?`<div style="--dot:var(--neg)"><div class="hs-lbl">Due soon</div><div class="hs-val">${dues.length}</div><div class="hs-meta">within 14 days</div></div>`:''}
        </div>
      </div>
      <div class="hero-chart"><div id="hist" class="chart-mount"></div>
        ${hist.length<8?`<div class="fine">${hist.length} day${hist.length===1?'':'s'} of history. A snapshot is saved automatically each day you open the app, so this fills in on its own.</div>`:''}</div>
    </section>
    <div id="dash-rest"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`;
  MiniChart.line(document.getElementById('hist'),hist,{height:210,name:'Net worth'});

  const J=p=>api(p).catch(()=>null);
  const [insights,readiness,patterns,cyc,anomalies,actionPlan,categoryTrends,netWorthByClass,fxExposure,subTrend,goals,heatmapData,mixData,freshness,holdings,budget,txns,inc,bs,pins]=
    await Promise.all([J('/api/insights'),J('/api/readiness'),J('/api/patterns'),J('/api/spend-cycles'),J('/api/anomalies'),J('/api/action-plan'),
      J('/api/category-trends'),J('/api/history/by-class'),J('/api/fx-exposure'),J('/api/subscription-trend'),J('/api/goals'),
      J('/api/heatmap?weeks=12'),J('/api/category-mix'),J('/api/data-freshness'),J('/api/holdings'),J('/api/budget'),J('/api/spending/transactions?days=395'),J('/api/income-basis'),J('/api/basics'),J('/api/plan/insights')]);
  if(!alive())return;
  if(txns)_cardsTxnCache=txns;

  /* ── key numbers ── */
  /* every figure below comes from /api/basics (measured: payslips + card statements), not from the typed-in budget */
  const saved=bs?bs.saved:(budget?budget.savings+budget.invest:0);
  const runway=bs&&bs.runway_months!=null?bs.runway_months:null;
  const sRate=bs?bs.savings_rate_gross_pct:null;
  const grossBase=bs?bs.gross:0;
  const mt=((cyc&&cyc.months)||[]).filter(m=>m.complete).map(m=>({month:m.month,total:m.total}));
  const staleCards=(freshness||[]).filter(f=>f.stale&&/statement$/i.test(f.label)&&!/bank account|email sync/i.test(f.label));
  const avgSpend=bs&&bs.card_cycles?bs.card_avg:null;
  const spendTrend=mt.length>=2&&mt[mt.length-2].total?((mt[mt.length-1].total/mt[mt.length-2].total-1)*100):null;
  const GROW=['stock','etf','mutual_fund','gold','crypto'];
  const inv=(holdings||[]).filter(h=>GROW.includes(h.asset_class)&&h.value_base>0),invTotal=inv.reduce((a,h)=>a+h.value_base,0);
  const single=inv.filter(h=>h.asset_class==='stock'||h.asset_class==='crypto').sort((a,b)=>b.value_base-a.value_base)[0];
  const topPct=single&&invTotal?single.value_base/invTotal*100:null;
  const tone=(v,good,warn,hiBad)=>v==null?'':hiBad?(v>=good?'bad':v>=warn?'warn':'good'):(v>=good?'good':v>=warn?'warn':'bad');
  const rateTone=!bs?'':bs.flow_avg<0?'bad':bs.flow_typical<0?'warn':tone(sRate,20,10);
  const kpiHTML=`<div class="kpis">
    <div class="kpi ${tone(runway,6,3)}"><div class="kpi-l">${tip('Cash runway','runway')}</div><div class="kpi-v">${runway!=null?runway.toFixed(1):'—'}<small>months</small></div>
      <div class="kpi-m">${runway!=null?`${CUR} ${fmt(bs.liquid)} in cash and savings at ${CUR} ${fmt(bs.spend)} a month of real spending. 6+ is comfortable.`:'Needs card statements to measure your spending.'}</div></div>
    <div class="kpi ${rateTone}"><div class="kpi-l">${tip('Savings rate','rate')}</div><div class="kpi-v">${sRate!=null?Math.round(sRate):'—'}<small>%</small></div>
      <div class="kpi-m">${sRate!=null?`${CUR} ${fmt(saved)} a month is ${sRate}% of gross pay. ${bs.flow_typical<0?`A typical month is ${CUR} ${fmt(-bs.flow_typical)} short after it; your bonus ${bs.flow_avg>=0?'covers that':'does not cover that'}.`:`A typical month still leaves ${CUR} ${fmt(bs.flow_typical)} after it.`}`:'Add your income in Budget.'}</div></div>
    <div class="kpi"><div class="kpi-l">Everyday card spend</div><div class="kpi-v">${avgSpend!=null?fmtK(avgSpend):'—'}<small>${CUR} a month</small></div>
      <div class="kpi-m">${avgSpend!=null?`Usual month over ${bs.card_cycles} cycles, trips and irregular costs left out.${spendTrend!=null?` Latest cycle ${spendTrend>=0?'up':'down'} ${Math.abs(spendTrend).toFixed(0)}%.`:''}${staleCards.length?` ${staleCards.length} card${staleCards.length>1?'s':''} sent no statement lately, so this may be low.`:''}`:'Needs card statements.'}</div></div>
    <div class="kpi ${tone(topPct,40,30,true)}"><div class="kpi-l">Largest single stock</div><div class="kpi-v">${topPct!=null?Math.round(topPct):'—'}<small>%</small></div>
      <div class="kpi-m">${single?`${esc(single.name)} of the ${CUR} ${fmt(invTotal)} you have invested. Over 30% is concentrated; funds and ETFs are not counted.`:'No single stocks held, so nothing is concentrated.'}</div></div>
  </div>`;

  /* ── needs attention: one panel, grouped, instead of four separate banners ── */
  const sevOrder={high:0,medium:1,low:2};
  const dueHTML=dues.length?`<div class="alert-bar"><span class="alert-icon"><svg class="i"><use href="#i-alert"/></svg></span>
    <div class="alert-text"><strong>${dues.length} card payment${dues.length>1?'s':''} due soon</strong><br>${dues.map(d=>`<span class="due-chip ${d.urgency}" onclick="go('cards')">
      ${esc(d.card_name)} · ${CUR} ${fmt(d.total_due)} · ${d.days_until_due<0?'overdue':d.days_until_due===0?'due today':'in '+d.days_until_due+' days'}</span>`).join('')}</div></div>`:'';
  const attn=[
    ...(actionPlan||[]).filter(a=>a.page||a.priority!=='low').map(a=>({sev:a.priority,title:a.text,page:a.page})),
    ...((pins&&pins.insights)||[]).map(i=>({sev:i.severity,title:i.title,body:i.body,page:i.page,ask:i.ask,pid:i.id})),
    ...(insights||[]).map(i=>({sev:i.severity,title:i.title,body:i.body,key:i.key}))].sort((a,b)=>sevOrder[a.sev]-sevOrder[b.sev]);
  const attnRow=(i,k)=>`<div class="plan-item pri-${i.sev}"><span class="plan-icon sev-ic-${i.sev}">${sevIcon(i.sev)}</span>
    <div style="flex:1;min-width:0"><p><b>${esc(i.title)}</b></p>${i.body?`<div class="fine" style="margin-top:3px">${esc(i.body)}</div>`:''}
    <div class="pl-acts">${i.page?`<button class="btn ghost sm" data-go="${esc(i.page)}">Open</button>`:''}${i.ask?`<button class="btn ghost sm" data-ask="${esc(i.ask)}">Ask the assistant</button>`:''}${i.pid||i.key?`<button class="btn ghost sm" data-x="${k}">Dismiss</button>`:''}</div></div></div>`;
  const attnHTML=`<div class="chart-card" id="attnCard"><div class="panel-head"><h3>Needs your attention</h3>${attn.length>3?`<button class="btn ghost sm" data-tab="insights">All ${attn.length}</button>`:''}</div>
    ${attn.length?attn.slice(0,3).map((i,k)=>attnRow(i,k)).join(''):`<div class="plan-item" style="padding:0"><span class="plan-icon sev-ic-low">${sevIcon('low')}</span><p>Nothing needs your attention right now.</p></div>`}</div>`;
  const anomRow=a=>`<div class="plan-item"><span class="plan-icon ${a.direction==='up'?'pnl-neg':'pnl-pos'}"><svg class="i"><use href="#i-${a.direction==='up'?'up':'down'}"/></svg></span>
    <p><b>${esc(a.category)}</b> ${a.direction} ${Math.abs(a.pct_change)}% in ${esc(a.month)} vs ${esc(a.prev_month)} <span class="sub-dim">(${CUR} ${fmt(a.value)} vs ${fmt(a.prev_value)})</span></p></div>`;
  const an=anomalies||[];
  const anomHTML=an.length?`<div class="chart-card" style="margin-bottom:20px"><h3>Unusual spending <span class="sub-dim">vs the month before, moves of ${CUR} 400 or more</span></h3>${an.slice(0,4).map(anomRow).join('')}
      ${an.length>4?`<details class="more"><summary>Show ${an.length-4} more</summary>${an.slice(4).map(anomRow).join('')}</details>`:''}</div>`:'';

  /* ── readiness ── */
  const briefingHTML=readiness?(()=>{
    const vClass='verdict-'+readiness.verdict.toLowerCase().replace(/\s+/g,'-');
    const col={'verdict-healthy':'var(--pos)','verdict-stable':'var(--info)','verdict-watch':'var(--warn)','verdict-at-risk':'var(--neg)'}[vClass]||'var(--accent)';
    return `<div class="briefing-card">
      <div class="readiness-ring">${readinessRingSVG(readiness.score,col)}
        <div class="readiness-ring-inner"><div class="readiness-score" style="color:${col}">${readiness.score}</div>
          <div class="readiness-verdict ${vClass}">${esc(readiness.verdict)}</div></div></div>
      <div style="flex:1;min-width:200px"><div class="briefing-eyebrow">${tip('Financial readiness','readiness')}</div>
        <div class="briefing-breakdown" style="margin-top:12px">${readiness.breakdown.map(b=>{
          const m=b.score>=80?'var(--pos)':b.score>=60?'var(--info)':b.score>=40?'var(--warn)':'var(--neg)';
          return `<div class="briefing-metric"><div class="briefing-metric-label">${esc(b.label)}</div>
            <div class="briefing-metric-bar"><div class="briefing-metric-fill" style="width:${b.score}%;background:${m}"></div></div>
            <div class="briefing-metric-val" style="color:${m}">${b.score}</div></div>`;}).join('')}</div></div>
      ${readiness.pace_note?`<div class="briefing-note">${esc(readiness.pace_note)}</div>`:''}</div>`;
  })():'';
  const cashflowHTML=(bs&&bs.net_typical)?`<div class="chart-card"><h3>Where your income goes <span class="sub-dim">a typical month, ${CUR} ${fmt(bs.net_typical)} take-home</span></h3>
    <div id="cashflow"></div>${bs.flow_typical<0?`<div class="fine" style="color:var(--neg)">Short by ${CUR} ${fmt(-bs.flow_typical)} in a typical month once you invest. Your bonus averages about ${CUR} ${fmt(bs.bonus_per_month)} a month, which makes the average month ${bs.flow_avg>=0?'+':'−'}${CUR} ${fmt(Math.abs(bs.flow_avg))}.</div>`
      :`<div class="fine">${CUR} ${fmt(bs.flow_typical)} is left in a typical month, before your bonus.</div>`}
    ${incomeCheck(budget,inc)}</div>`:'';

  /* ── composition (tabs) ── */
  const compHTML=`<div class="chart-card wide"><div class="panel-head"><h3>Where your money sits</h3>
    <div class="tabbar" id="compTabs"><button class="active" data-k="class">Asset class</button><button data-k="geo">Geography</button><button data-k="ccy">Currency</button></div></div>
    <div id="comp"></div><div id="compExtra"></div></div>`;

  /* ── projection ── */
  const contrib0=bs?Math.max(0,bs.net_avg-bs.spend):(budget?budget.savings+budget.invest:0);      // what you really keep a month, bonus included
  if(_proj.extra==null)_proj.extra=Math.round(contrib0);
  const pgoals=(bs&&bs.goals)||[],outs={};
  pgoals.forEach(g=>{const m=Math.max(1,Math.round(g.months_left));outs[m]=(outs[m]||0)+g.amount;});
  const projHTML=`<div class="chart-card wide"><h3>Where this is heading <span class="sub-dim">illustration, not a forecast</span></h3>
    <div class="proj-controls">
      <label>Yearly return: <b id="pRetV">${_proj.ret}%</b><input id="pRet" type="range" min="0" max="12" step="0.5" value="${_proj.ret}"></label>
      <label>Years<select id="pYears">${[1,3,5,10,20].map(y=>`<option value="${y}" ${y===_proj.years?'selected':''}>${y}</option>`).join('')}</select></label>
      <label>Kept per month (${CUR})<input id="pExtra" type="number" min="0" step="100" value="${_proj.extra}"></label>
    </div>
    <div class="proj-out" id="projOut"></div><div id="projChart" class="chart-mount" style="min-height:260px"></div>
    <div class="fine">Starts from today's net worth and adds what you really keep each month (take-home averaged over 12 payslips, minus real spending).${pgoals.length?` Dated goals are paid out: ${pgoals.map(g=>`${esc(g.name)} ${CUR} ${fmt(g.amount)}`).join(', ')}.`:''} Real returns vary year to year and can be negative.</div></div>`;

  /* ── spending ── */
  const ct=categoryTrends&&categoryTrends.series?categoryTrends.series.filter(x=>x.values.length>=2):[];
  const sparkHTML=ct.length?`<div class="sec"><h2>How you spend</h2><p>Category totals by month, most recent on the right</p></div>
    <div class="spark-grid">${ct.map(x=>{const v=x.values,l=v[v.length-1],p=v[v.length-2],d=p?(l/p-1)*100:null;
      return `<div class="spark-cell"><div><div class="spark-name">${esc(x.category)}</div><div class="spark-val">${fmtK(l)}</div>
        <div class="spark-delta ${d==null||Math.abs(d)<3?'flat':d>0?'up':'down'}">${d==null?'':(d>0?'+':'')+d.toFixed(0)+'%'}</div></div>${MiniChart.spark(v,{w:96,h:40,color:d!=null&&d>8?'var(--neg)':d!=null&&d<-8?'var(--pos)':'var(--accent)'})}</div>`;}).join('')}</div>`:'';
  const mixItems=(mixData&&mixData.categories&&mixData.categories.length>=3)?mixData.categories.map((c,i)=>({label:c,delta:(mixData.latest_pct[i]||0)-(mixData.prev_pct[i]||0)})).sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta)):[];
  const mixHTML=mixItems.length?`<div class="chart-card"><h3>Shifts in your spending mix <span class="sub-dim">${esc(mixData.latest_month)} vs ${esc(mixData.prev_month)}</span></h3><div id="mixDiverge"></div>
    <div class="fine">Change in each category's share of total spend, in percentage points.</div></div>`:'';
  const calHTML=(heatmapData&&heatmapData.days&&heatmapData.days.length)?`<div class="chart-card"><h3>Daily spending <span class="sub-dim">last 12 weeks</span></h3><div id="calGrid"></div></div>`:'';
  const subHTML=(subTrend&&subTrend.months&&subTrend.months.length)?`<div class="chart-card"><h3>Bills and subscriptions <span class="sub-dim">monthly total</span></h3>
    <div id="subTrend" class="chart-mount" style="min-height:170px"></div>
    ${subTrend.trend_available?`<div class="sub-trend-note ${subTrend.creeping?'creeping':''}">${subTrend.creeping?'Creeping up: ':''}${CUR} ${fmt(subTrend.latest_total)} in ${esc(subTrend.latest_month)} vs ${CUR} ${fmt(subTrend.prev_total)} in ${esc(subTrend.prev_month)} (${subTrend.pct_change>0?'+':''}${subTrend.pct_change}%)</div>`
      :`<div class="fine">Needs two completed months to show a trend.</div>`}</div>`:'';
  const nwcHTML=(netWorthByClass&&netWorthByClass.series&&netWorthByClass.series.length&&netWorthByClass.dates.length>=4)?`<div class="chart-card wide"><h3>Net worth by asset class <span class="sub-dim">over time</span></h3><div id="nwComp" class="chart-mount"></div></div>`:'';
  const patHTML=(patterns&&patterns.patterns&&patterns.patterns.length)?`<div class="chart-card"><h3>Patterns in your data <span class="sub-dim">${patterns.months_available} months tracked</span></h3>
    ${patterns.patterns.map(p=>`<div class="pattern-row"><span class="pattern-desc">${esc(p.a)} <span class="pattern-arrow ${p.direction==='up'?'pnl-pos':'pnl-neg'}">${p.direction==='up'?'rises':'falls'}</span> when ${esc(p.b)} rises</span>
      <span class="pattern-r ${p.r>=0?'pnl-pos':'pnl-neg'}">${p.strength} <span class="sub-dim">r=${p.r>=0?'+':''}${p.r}, n=${p.n}</span></span></div>`).join('')}
    <div class="pattern-caveat">Correlation, not causation. Treat these as questions to look into.</div></div>`:'';

  /* ── goals + data health ── */
  const planGoalHTML=pgoals.map(g=>{const have=g.funded_liquid+g.from_term_assets,pct=Math.min(100,have/g.amount*100);
    return `<div class="goal-row"><div class="goal-head"><div><b>${esc(g.name)}</b> <span class="sub-dim">by ${esc(g.date)}</span></div>
      <span class="goal-verdict ${g.shortfall>0?'off-track':'on-track'}">${g.shortfall>0?'Short':'Funded'}</span></div>
      <div class="goal-bar-track"><div class="goal-bar-fill" style="width:${pct}%"></div></div>
      <div class="goal-meta">${CUR} ${fmt(have)} of ${fmt(g.amount)} set aside${g.shortfall>0?` · ${CUR} ${fmt(g.monthly_needed)} a month for ${Math.round(g.months_left)} months closes the gap`:''}</div>
      <div class="goal-eta">${g.assumes_term?`Counts ${CUR} ${fmt(g.from_term_assets)} of your fixed-term holdings. Add its maturity date on the Plan page to confirm it pays out in time.`:g.from_term_assets?`Includes ${CUR} ${fmt(g.from_term_assets)} from your fixed-term holdings.`:'Held in cash and savings, after your emergency fund.'}</div></div>`;}).join('');
  const goalsHTML=`<div class="chart-card"><div class="panel-head"><h3>Goals</h3><button class="btn ghost sm" onclick="openGoalModal()">Add net-worth goal</button></div>
    ${planGoalHTML}
    ${goals&&goals.length?goals.map(g=>{
      const pct=Math.min(g.pct_complete,100),n=monthsToReach(s.total_base,contrib0,_proj.ret,g.target_amount);
      const eta=n===0?'Reached':n==null?'Not reached within 50 years at this pace':`At ${CUR} ${fmt(contrib0)} a month and ${_proj.ret}% a year: about ${addMonths(n)}`;
      const late=n!=null&&n>0&&g.days_remaining>0&&n*30.44>g.days_remaining;
      return `<div class="goal-row"><div class="goal-head"><div><b>${esc(g.name)}</b> <span class="sub-dim">by ${esc(g.target_date)}</span></div>
        <div style="display:flex;align-items:center;gap:8px">${g.projection_available?`<span class="goal-verdict ${g.on_track?'on-track':'off-track'}">${g.on_track?'On track':'Behind'}</span>`:late?`<span class="goal-verdict off-track">Behind</span>`:''}
        <button class="btn ghost sm" onclick="deleteGoal(${g.id})" aria-label="Delete goal" style="padding:4px 7px"><svg class="i" style="width:14px;height:14px"><use href="#i-close"/></svg></button></div></div>
        <div class="goal-bar-track"><div class="goal-bar-fill" style="width:${pct}%"></div></div>
        <div class="goal-meta">${CUR} ${fmt(g.current_total)} of ${fmt(g.target_amount)} (${g.pct_complete}%)${g.required_monthly!=null&&g.days_remaining>0?` · needs ${CUR} ${fmt(g.required_monthly)} a month to hit the date`:''}</div>
        <div class="goal-eta">${eta}</div></div>`;}).join('')
      :(planGoalHTML?'':`<div class="empty" style="padding:24px">No goals yet. Add one on the Plan page, or a net-worth target here.</div>`)}</div>`;
  const freshHTML=(freshness&&freshness.length)?`<div class="chart-card"><h3>Data freshness <span class="sub-dim">last update per source</span></h3>${freshness.map(f=>`<div class="fresh-row ${f.stale?'stale':''}">
    <span class="fresh-dot"></span><span class="fresh-label">${esc(f.label)}</span><span class="fresh-val">${f.last_date?esc(f.last_date)+' · '+f.days_ago+'d ago':'never'}</span>${f.stale?'<span class="fresh-badge">Stale</span>':''}</div>`).join('')}</div>`:'';

  document.getElementById('dash-rest').innerHTML=`
    ${kpiHTML}
    <div class="ctabs" id="dTabs">${[['now','Today'],['money','Money'],['spend','Spending'],['insights','Insights'+(attn.length?` <span class="badge">${attn.length}</span>`:'')],['data','Data']].map(([k,l])=>`<button data-t="${k}">${l}</button>`).join('')}</div>
    <div class="ctab" data-t="now">${dueHTML}${attnHTML}
      <div class="two-col" style="margin-top:20px">${briefingHTML}${cashflowHTML}</div>
      <div style="margin-top:20px">${goalsHTML}</div></div>
    <div class="ctab hidden" data-t="money">${compHTML}<div style="height:20px"></div>${projHTML}${nwcHTML?`<div style="height:20px"></div>${nwcHTML}`:''}<div id="cashHistoryArea"></div></div>
    <div class="ctab hidden" data-t="spend">${anomHTML}${sparkHTML}
      <div class="two-col" style="margin-top:20px">${mixHTML}${calHTML}</div><div class="two-col">${subHTML}${patHTML}</div>
      ${anomHTML||sparkHTML||mixHTML||calHTML||subHTML||patHTML?'':'<div class="chart-card"><div class="empty">Needs more complete statement cycles.</div></div>'}</div>
    <div class="ctab hidden" data-t="insights"><div id="leaksHost" style="margin-bottom:20px"></div><div id="ai-dashboard"></div>
      <div class="sec"><h2>Everything found</h2><p>From your own numbers. Dismiss what you have dealt with</p></div>
      <div class="chart-card">${attn.length?attn.map((i,k)=>attnRow(i,k)).join(''):'<div class="empty">Nothing to flag.</div>'}</div></div>
    <div class="ctab hidden" data-t="data">${freshHTML||'<div class="chart-card"><div class="empty">No data sources yet.</div></div>'}</div>`;

  const drawComp=k=>{
    const box=document.getElementById('comp'),extra=document.getElementById('compExtra');if(!box)return;
    const items=k==='class'?s.by_class.map(c=>({label:CLASS_LABELS[c.label]||c.label,value:c.value})):k==='geo'?s.by_geography:s.by_currency;
    MiniChart.stackBar(box,items,{max:7,label:'Share of net worth'});
    let ex='';
    if(k==='class'&&s.allocation_drift&&s.allocation_drift.length)ex=`<div class="target-list"><h4>Against your targets</h4>${s.allocation_drift.map(a=>`
      <div class="alloc-row"><div class="alloc-label">${CLASS_LABELS[a.label]||a.label}</div>
        <div class="alloc-bar-track"><div class="alloc-bar-fill" style="width:${Math.min(a.actual_pct,100)}%"></div><div class="alloc-target-marker" style="left:${Math.min(a.target_pct,100)}%" title="Target ${a.target_pct}%"></div></div>
        <div class="alloc-nums"><span class="alloc-actual">${a.actual_pct}% of ${a.target_pct}%</span><span class="alloc-drift ${a.drift>0?'over':a.drift<0?'under':''}">${a.drift>0?'+':''}${a.drift}</span></div></div>`).join('')}
      <div class="fine">The tick is your target; the bar is where you are. Positive means above target.</div></div>`;
    if(k==='ccy'&&fxExposure&&fxExposure.exposures&&fxExposure.exposures.length)ex=`<div class="target-list"><h4>Currency risk</h4>${fxExposure.exposures.map(e=>`<div class="fxexp-sens" style="font-size:13px;margin:6px 0;color:var(--ink-dim)"><b style="color:var(--ink)">${esc(e.currency)}</b>: ${CUR} ${fmt(e.exposure_base)} (${e.pct_of_total}%). A 1% move in ${esc(e.currency)} changes your net worth by about ${CUR} ${fmt(e.sensitivity_1pct_base)}.</div>`).join('')}</div>`;
    extra.innerHTML=ex;
  };

  const drawProj=()=>{
    const yrs=_proj.years,n=yrs*12,step=Math.max(1,Math.ceil(n/12));
    const withR=growthPath(s.total_base,_proj.extra,_proj.ret,n,outs),noR=growthPath(s.total_base,_proj.extra,0,n,outs);
    const idx=[];for(let m=0;m<=n;m+=step)idx.push(m);if(idx[idx.length-1]!==n)idx.push(n);
    MiniChart.projection(document.getElementById('projChart'),idx.map(m=>m===0?'Now':addMonths(m)),
      [{name:'Savings only, no growth',data:idx.map(m=>noR[m])},{name:`With ${_proj.ret}% a year`,data:idx.map(m=>withR[m])}],
      (goals||[]).map(g=>({name:g.name,amount:g.target_amount})));
    const end=withR[n],put=_proj.extra*n,paid=Object.entries(outs).filter(([m])=>+m<=n).reduce((a,[,v])=>a+v,0);
    document.getElementById('projOut').innerHTML=`<div><b>${CUR} ${fmt(end)}</b><span>in ${yrs} year${yrs>1?'s':''}</span></div>
      <div><b>${CUR} ${fmt(put)}</b><span>you add</span></div>${paid?`<div><b>${CUR} ${fmt(paid)}</b><span>paid out for goals</span></div>`:''}<div><b>${CUR} ${fmt(Math.max(0,end-s.total_base-put+paid))}</b><span>from growth</span></div>`;
  };

  const draws={
    now(){
      animateReadinessRings();
      if(bs&&bs.net_typical&&document.getElementById('cashflow'))MiniChart.stackBar(document.getElementById('cashflow'),[
        {label:'Real spending',value:bs.spend},{label:'Savings',value:bs.saved-bs.invest},
        {label:'Investing',value:bs.invest},{label:'Left over',value:Math.max(0,bs.flow_typical),color:'var(--track)'}].filter(x=>x.value>0),{total:bs.net_typical,label:'Share of take-home'});
    },
    money(){
      drawComp('class');
      document.querySelectorAll('#compTabs button').forEach(b=>b.onclick=()=>{
        document.querySelectorAll('#compTabs button').forEach(x=>x.classList.toggle('active',x===b));drawComp(b.dataset.k);});
      drawProj();
      document.getElementById('pRet').oninput=e=>{_proj.ret=+e.target.value;document.getElementById('pRetV').textContent=_proj.ret+'%';drawProj();};
      document.getElementById('pYears').onchange=e=>{_proj.years=+e.target.value;drawProj();};
      document.getElementById('pExtra').oninput=e=>{_proj.extra=Math.max(0,+e.target.value||0);drawProj();};
      if(nwcHTML)MiniChart.stackedArea(document.getElementById('nwComp'),netWorthByClass.dates,netWorthByClass.series.map(x=>({name:CLASS_LABELS[x.asset_class]||x.asset_class,values:x.values})));
      renderCashHistory();
    },
    spend(){
      if(mixItems.length)MiniChart.diverge(document.getElementById('mixDiverge'),mixItems.slice(0,8),{format:v=>(v>0?'+':'')+v.toFixed(1)+' pt'});
      if(heatmapData&&heatmapData.days&&heatmapData.days.length)MiniChart.calendar(document.getElementById('calGrid'),heatmapData.days);
      if(subTrend&&subTrend.months&&subTrend.months.length)MiniChart.bar(document.getElementById('subTrend'),subTrend.months.map((m,i)=>({label:fmtMonth(m),value:subTrend.totals[i]})),{height:170});
    },
    insights(){api('/api/wealth/leaks').then(l=>{const h=document.getElementById('leaksHost');if(h)h.innerHTML=leaksHTML(l);}).catch(()=>{});loadAIInsights('dashboard','ai-dashboard');}
  };
  const dDone={};
  window.showDashTab=(t,noScroll)=>{
    const root=document.getElementById('dash-rest');if(!root||!root.querySelector(`.ctab[data-t="${t}"]`))t='now';
    root.querySelectorAll('.ctab').forEach(e=>e.classList.toggle('hidden',e.dataset.t!==t));
    root.querySelectorAll('#dTabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
    try{localStorage.setItem('dashTab',t);}catch(e){}
    if(!dDone[t]){dDone[t]=true;if(draws[t])draws[t]();}
    if(!noScroll){const bar=document.getElementById('dTabs');if(bar&&bar.getBoundingClientRect().top<0)bar.scrollIntoView({behavior:'instant'});}
  };
  document.getElementById('dTabs').onclick=e=>{const b=e.target.closest('button');if(b)showDashTab(b.dataset.t);};
  document.getElementById('dash-rest').onclick=async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.go)go(b.dataset.go);
    else if(b.dataset.ask&&window.askAssistant)askAssistant(b.dataset.ask);
    else if(b.dataset.tab)showDashTab(b.dataset.tab);
    else if(b.dataset.x!==undefined){const i=attn[+b.dataset.x];
      if(i.pid)await api('/api/plan/insights/'+encodeURIComponent(i.pid)+'/dismiss',{method:'POST'});else if(i.key)await api('/api/insights/'+encodeURIComponent(i.key)+'/dismiss',{method:'POST'});
      renderDashboard();}
  };
  let t0='now';try{t0=localStorage.getItem('dashTab')||'now';}catch(e){}
  showDashTab(t0,true);
}

/* ─── goals ─────────────────────────────────────────────── */
function openGoalModal(){
  setV('g_name','');setV('g_target_amount','');setV('g_target_date','');
  document.getElementById('goalOverlay').classList.add('show');
}
function closeGoalModal(){document.getElementById('goalOverlay').classList.remove('show');}
async function saveGoal(){
  const name=getV('g_name').trim(),amount=parseFloat(getV('g_target_amount')),tdate=getV('g_target_date');
  if(!name||!amount||!tdate){toast('Fill in all fields','err');return;}
  try{
    await api('/api/goals',{method:'POST',body:{name,target_amount:amount,target_date:tdate}});
    closeGoalModal();renderDashboard();toast('Goal added');
  }catch{toast('Failed to save goal','err');}
}
async function deleteGoal(id){
  if(!await askConfirm('This goal will be removed.',{title:'Delete goal?',ok:'Delete'}))return;
  await api('/api/goals/'+id,{method:'DELETE'});
  renderDashboard();toast('Goal deleted');
}

async function renderCashHistory(){
  const area=document.getElementById('cashHistoryArea');
  if(!area)return;
  const cfg=await api('/api/bank-statement/config').catch(()=>null);
  if(!cfg||(!cfg.current_holding_id&&!cfg.savings_holding_id)){area.innerHTML='';return;}
  const holdings=await api('/api/holdings');
  const byId=Object.fromEntries(holdings.map(h=>[h.id,h]));
  const parts=[];
  if(cfg.current_holding_id&&byId[cfg.current_holding_id])parts.push({id:cfg.current_holding_id,label:byId[cfg.current_holding_id].name,canvas:'histCurrent'});
  if(cfg.savings_holding_id&&byId[cfg.savings_holding_id])parts.push({id:cfg.savings_holding_id,label:byId[cfg.savings_holding_id].name,canvas:'histSavings'});
  if(!parts.length){area.innerHTML='';return;}
  area.innerHTML=`<div class="chart-grid">
    ${parts.map(p=>`<div class="chart-card"><h3>${esc(p.label)} <span class="sub-dim" style="font-weight:400;font-size:11px">balance over time</span></h3><div id="${p.canvas}" class="chart-mount"></div></div>`).join('')}
  </div>`;
  for(const p of parts){
    const hist=await api(`/api/holdings/${p.id}/history`);
    MiniChart.line(document.getElementById(p.canvas),hist.map(x=>({snap_date:x.snap_date,total:x.value_base})));
  }
}

async function dismissInsight(key){
  try{await api(`/api/insights/${encodeURIComponent(key)}/dismiss`,{method:'POST'});renderDashboard();}catch{}
}

/* ─── AI Analysis (LLM-generated, cached per scope) ────────── */
const AI_SCOPE_TITLES={dashboard:'Overall',portfolio:'Portfolio',cards:'Cards',budget:'Budget',market:'Market'};
function timeAgo(iso){
  if(!iso)return '';
  const mins=Math.max(0,Math.round((Date.now()-new Date(iso).getTime())/60000));
  if(mins<1)return 'just now';
  if(mins<60)return mins+'m ago';
  const hrs=Math.round(mins/60);
  if(hrs<24)return hrs+'h ago';
  return Math.round(hrs/24)+'d ago';
}
function aiInsightsMarkup(scope,d,loading,failed){
  const insights=d?d.insights||[]:[];
  const notConfigured=d&&d.available===false;
  const freshness=d&&d.generated_at?timeAgo(d.generated_at)+(d.stale?' · stale':''):'';
  return `<div class="ai-insights-card">
    <div class="ai-insights-head">
      <span>AI analysis <span class="sub-dim" style="font-weight:400;font-size:11px">${loading?'analyzing…':esc(freshness)}</span></span>
      <button class="btn ghost sm" onclick="refreshAIInsights('${scope}')" ${loading?'disabled':''}>Refresh</button>
    </div>
    ${loading?`<div class="empty" style="padding:16px"><span class="search-spin on" style="position:static"></span> Analyzing your ${esc((AI_SCOPE_TITLES[scope]||scope).toLowerCase())} data…</div>`
      :failed?`<div class="empty" style="padding:16px">Couldn't load AI analysis right now.</div>`
      :notConfigured?`<div class="empty" style="padding:16px">AI analysis needs an OpenAI API key configured on the server (see Settings).</div>`
      :insights.length?(()=>{const row=i=>`
        <div class="ai-insight-row sev-${i.severity}">
          <span class="ai-insight-icon sev-ic-${i.severity}">${sevIcon(i.severity)}</span>
          <div><div class="ai-insight-title">${esc(i.title)}</div><div class="ai-insight-body">${esc(i.body)}</div></div>
        </div>`;
        return insights.slice(0,3).map(row).join('')+(insights.length>3?`<details class="more"><summary>Show ${insights.length-3} more</summary>${insights.slice(3).map(row).join('')}</details>`:'');})()
      :`<div class="empty" style="padding:16px">Nothing notable right now.</div>`}
  </div>`;
}
// render token per container: if this page re-renders (e.g. nav away and back) while
// an older loadAIInsights/refreshAIInsights call for the same container is still in
// flight, that stale call must not clobber whatever the newer call already wrote —
// same pattern as the donut chart's render-token guard in charts.js.
const _aiInsightsToken={};
async function loadAIInsights(scope,containerId){
  if(!document.getElementById(containerId))return;
  const myToken=(_aiInsightsToken[containerId]=(_aiInsightsToken[containerId]||0)+1);
  try{
    const d=await api('/api/ai-insights/'+scope);
    if(_aiInsightsToken[containerId]!==myToken)return;
    const el=document.getElementById(containerId);
    if(el)el.innerHTML=aiInsightsMarkup(scope,d,false);
  }catch{
    if(_aiInsightsToken[containerId]!==myToken)return;
    const el=document.getElementById(containerId);
    if(el)el.innerHTML=aiInsightsMarkup(scope,null,false,true);
  }
}
async function refreshAIInsights(scope){
  const containerId='ai-'+scope;
  const myToken=(_aiInsightsToken[containerId]=(_aiInsightsToken[containerId]||0)+1);
  const el0=document.getElementById(containerId);
  if(el0)el0.innerHTML=aiInsightsMarkup(scope,null,true);
  try{
    const d=await api('/api/ai-insights/'+scope+'/refresh',{method:'POST'});
    if(_aiInsightsToken[containerId]!==myToken)return;
    const el=document.getElementById(containerId);
    if(el)el.innerHTML=aiInsightsMarkup(scope,d,false);
    toast('AI analysis refreshed');
  }catch(e){
    if(_aiInsightsToken[containerId]!==myToken)return;
    const el=document.getElementById(containerId);
    if(el)el.innerHTML=aiInsightsMarkup(scope,null,false,true);
    toast('Refresh failed','err');
  }
}

function fxLine(){if(!summary)return '';return Object.entries(summary.fx).filter(([k])=>k!==CUR).map(([k,v])=>`1 ${k} = ${(+v).toFixed(k==='INR'?4:2)} ${CUR}`).join('   ·   ');}
function updateBadges(){
  if(!summary)return;
  document.querySelectorAll('.badge[data-count]').forEach(b=>{const c=summary.class_counts[b.dataset.count]||0;b.textContent=c||'';});
}

/* ─── category pages ────────────────────────────────────── */
async function renderCategory(cls){
  const list=await api('/api/holdings'+(cls==='all'?'':'?class='+cls));
  if(!summary)summary=await api('/api/summary');
  updateBadges();
  const total=list.reduce((s,h)=>s+h.value_base,0);
  const el=document.getElementById('page-category');
  const showUnits=['stock','etf','mutual_fund','all'].includes(cls);
  const rows=list.map(h=>{
    const live=h.price_mode!=='manual'&&h.last_price>0;
    const pnl=h.pnl_pct!=null?`<div class="${h.pnl_pct>=0?'pnl-pos':'pnl-neg'}" style="font-size:11px">${h.pnl_pct>=0?'+':''}${h.pnl_pct}%</div>`:'';
    const isManual=h.price_mode==='manual';
    const updateBtn=isManual?`<button class="btn ghost sm" onclick="event.stopPropagation();openQuickUpdate(${h.id},'${ejs(h.name)}')" style="font-size:11px;padding:4px 8px">Update</button>`:'';
    return `<tr onclick="openEditHolding(${h.id})">
      <td><div class="nm">${esc(h.name)}</div>${h.symbol?`<div class="sym">${live?'<span class=live-dot></span>':''}${esc(h.symbol)}</div>`:''}</td>
      <td><span class="sub-dim">${esc(h.platform||'—')}</span></td>
      ${cls==='all'?`<td><span class="sub-dim">${CLASS_LABELS[h.asset_class]||h.asset_class}</span></td>`:''}
      <td><span class="sub-dim">${esc(h.geography)}</span></td>
      <td><span class="pill ccy-${h.currency}">${h.currency}</span></td>
      ${showUnits?`<td class="num">${h.units?fmt2(h.units):'—'}</td><td class="num">${h.last_price?(h.currency!==CUR?`<span class="sub-dim" style="font-size:10px">${esc(h.currency)} </span>`:'')+fmt2(h.last_price):'—'}</td>`:''}
      <td class="num">${h.currency!==CUR?`<span class="sub-dim" style="font-size:10px">${esc(h.currency)} </span>`:''}${fmt(h.native_value)}</td>
      <td class="num"><div><span class="sub-dim" style="font-size:10px">${CUR} </span>${fmt(h.value_base)}</div>${pnl}</td>
      <td class="num">${total?(h.value_base/total*100).toFixed(1):0}%</td>
      <td>${updateBtn}</td>
    </tr>`;}).join('');
  const chipClasses=['all','stock','etf','mutual_fund','bond','savings','gold','crypto','cash'];
  const filterChips=chipClasses.map(c=>{
    const count=c==='all'?Object.values(summary?.class_counts||{}).reduce((a,b)=>a+b,0):(summary?.class_counts?.[c]||0);
    return `<button class="filter-chip ${c===cls?'active':''}" onclick="go('${c}')">${CLASS_LABELS[c]}${count?`<span class="fc-count">${count}</span>`:''}</button>`;
  }).join('');
  el.innerHTML=`
    <div class="filter-chip-row">${filterChips}</div>
    <div class="page-head">
      <div><div class="page-title">${CLASS_LABELS[cls]}</div><div class="page-sub">${CLASS_SUB[cls]||''}</div></div>
      <div style="display:flex;gap:10px;align-items:center">
        <div class="stat" style="padding:12px 18px"><div class="lbl">Subtotal</div><div class="val" style="font-size:22px"><span class="cur">${CUR}</span>${fmt(total)}</div></div>
        <button class="btn solid" onclick="openAddHolding('${cls==='all'?'stock':cls}')">+ Add</button>
      </div>
    </div>
    ${list.filter(h=>h.value_base>0).length>2?`<div class="two-col"><div class="chart-card"><h3>Largest positions <span class="sub-dim">share of ${cls==='all'?'your assets':'this class'}</span></h3><div id="topPositions"></div></div>
      <div class="chart-card"><h3>Gains and losses <span class="sub-dim">vs cost basis, where known</span></h3><div id="pnlBars"></div></div></div>`:''}
    ${cls==='all'&&list.length?`<div id="ai-portfolio"></div>`:''}
    ${list.length?`<div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Platform</th>${cls==='all'?'<th>Class</th>':''}<th>Region</th><th>Ccy</th>
        ${showUnits?'<th class="num">Units</th><th class="num">Price</th>':''}<th class="num">Value (native)</th><th class="num">Value (${CUR})</th><th class="num">%</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table></div>`
    :`<div class="table-wrap"><div class="empty"><div class="big">∅</div>No ${(CLASS_LABELS[cls]||cls).toLowerCase()} yet.<br>Click "+ Add" to get started.</div></div>`}`;
  const pos=list.filter(h=>h.value_base>0),posTotal=pos.reduce((a,h)=>a+h.value_base,0);
  MiniChart.rankBars(document.getElementById('topPositions'),pos.slice().sort((a,b)=>b.value_base-a.value_base).map(h=>({label:h.name,value:h.value_base/posTotal*100})),
    {limit:6,max:100,format:v=>v.toFixed(0)+'%'});
  const pnl=list.filter(h=>h.pnl_pct!=null);
  const pnlEl=document.getElementById('pnlBars');
  if(pnlEl){
    if(pnl.length)MiniChart.diverge(pnlEl,pnl.sort((a,b)=>Math.abs(b.pnl_pct)-Math.abs(a.pnl_pct)).slice(0,6).map(h=>({label:h.name,delta:h.pnl_pct})),{goodUp:true,format:v=>(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(0)+'%'});
    else MiniChart.note(pnlEl,'Add a cost basis to a holding to see gains and losses');
  }
  if(cls==='all'&&list.length)loadAIInsights('portfolio','ai-portfolio');
}

/* ─── credit cards ──────────────────────────────────────── */
let _cardsTxnCache=null;
let _spendFilter={category:null,sub:null,trip:null,month:null};
let _cardsPeriod=(()=>{try{return localStorage.getItem('cardsPeriod')||'c6';}catch(e){return 'c6';}})();   // one period for the Spending, Rewards and Abroad tabs
window.getCardsPeriod=()=>_cardsPeriod;
let _cyc=null;        // /api/spend-cycles: per statement cycle totals + which cycles are complete

const PERIODS={m3:'Last 3 months',m6:'Last 6 months',m12:'Last 12 months',c1:'Latest cycle',c3:'Last 3 cycles',c6:'Last 6 cycles',c12:'Last 12 cycles',all:'All complete cycles'};
const completeCycles=()=>((_cyc&&_cyc.months)||[]).filter(m=>m.complete).map(m=>m.month);
/* Each card closes its statement on a different day, so calendar months are never complete for every card
   at once. A statement cycle is: every card's statement for that billing month. */
const monthLong=ym=>new Date(ym+'-01T00:00:00').toLocaleDateString('en-GB',{month:'long',year:'numeric'});
function periodLabel(p){
  if(PERIODS[p])return PERIODS[p];
  if(p.startsWith('mon:'))return monthLong(p.slice(4));
  if(p.startsWith('cyc:'))return monthLong(p.slice(4))+' statement cycle';
  if(p.startsWith('rng:'))return p.slice(4).replace('..',' to ');
  return p;
}
function filterByPeriod(txns,period){
  if(period.startsWith('mon:'))return txns.filter(t=>t.txn_date.slice(0,7)===period.slice(4));
  if(period.startsWith('cyc:'))return txns.filter(t=>t.cycle===period.slice(4));
  if(period.startsWith('rng:')){const [a,b]=period.slice(4).split('..');return txns.filter(t=>t.txn_date>=a&&t.txn_date<=b);}
  if(period[0]==='m'){const d=new Date();d.setDate(d.getDate()-Math.round(+period.slice(1)*30.44));const since=d.toISOString().slice(0,10);return txns.filter(t=>t.txn_date>=since);}
  const all=completeCycles(),n=period==='all'?all.length:+period.slice(1);
  const keep=new Set(all.slice(-n));
  return txns.filter(t=>keep.has(t.cycle));
}
function coverageNote(){
  if(!_cyc||!_cyc.months.length)return '';
  const done=_cyc.months.filter(m=>m.complete),last=done[done.length-1];
  const d=iso=>iso?new Date(iso+'T00:00:00').toLocaleDateString('en-US',{day:'numeric',month:'short'}):'never';
  const cards=(_cyc.coverage||[]).filter(c=>c.active).map(c=>`${esc(c.name)} to ${d(c.covered_to)}`).join(' · ');
  const quiet=(_cyc.coverage||[]).filter(c=>!c.active).map(c=>esc(c.name));
  const skipped=_cyc.months.filter(m=>!m.complete).length;
  return `<div class="fine" style="margin:-4px 0 18px;line-height:1.6">Cards close their statements on different days, so spending is grouped by <b>statement cycle</b>, not calendar month.
    ${last?`Complete through the <b>${fmtMonth(last.month)}</b> cycle.`:'No cycle has every card yet.'} ${skipped?`${skipped} earlier cycle${skipped>1?'s are':' is'} left out of trends because a card's statement is missing. `:''}
    Data per card: ${cards}.${quiet.length?` No recent statement from ${quiet.join(', ')}.`:''}</div>`;
}

const ordinal=n=>{const v=n%100;return n+(['th','st','nd','rd'][(v-20)%10]||['th','st','nd','rd'][v]||'th');};
function statementPatternLine(sp){
  if(!sp)return '';
  const fmtD=iso=>new Date(iso+'T00:00:00').toLocaleDateString('en-US',{day:'numeric',month:'short'});
  const learned=sp.source==='history'&&sp.samples>=2;
  return `<div class="cc-meta" title="${learned?`Learned from your last ${sp.samples} statements`:'Based on one statement or the day set on the card'}">
    <span>Statement</span><b>${learned?'~':''}${ordinal(sp.typical_day)} each month</b>
    <span>Next</span><b class="${sp.late_days>0?'cc-late':''}">${sp.late_days>0?'due '+fmtD(sp.next_expected)+', not in':'~'+fmtD(sp.next_expected)}</b>
    ${sp.due_gap_days?`<span>Payment due</span><b>${sp.due_gap_days} days after</b>`:''}
  </div>`;
}
/* ─── custom spending categories ─────────────────────────── */
const CATEGORY_SUGGESTIONS=['Charity','Gifts','Family support','Medical','Kids','Pets','Insurance','Fuel','Rent','Hobbies'];
async function createCategoryDialog(pre=''){
  const r=await dialog({title:'New category',ok:'Add category',
    text:'Optional keywords (comma separated) file matching merchants automatically, for example "charity, donation".',
    fields:[{key:'name',label:'Name',value:pre},{key:'keywords',label:'Keywords (optional)'}]});
  if(!r||!r.name.trim())return null;
  try{
    const d=await api('/api/spending/categories',{method:'POST',body:{name:r.name,keywords:r.keywords}});
    SPEND_CATEGORIES=null;_cardsTxnCache=null;
    toast(d.moved?`Added ${d.name}. ${d.moved} transaction${d.moved>1?'s':''} filed under it.`:`Added ${d.name}`);
    return d.name;
  }catch(e){let m='Could not add';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');return null;}
}
async function manageCategories(){
  const cur=await api('/api/spending/categories/custom').catch(()=>[]);
  const have=new Set(cur.map(c=>c.name.toLowerCase()));
  document.getElementById('stmtDetailTitle').textContent='Spending categories';
  document.getElementById('stmtDetailBody').innerHTML=`
    <div class="fine" style="margin:0 0 12px">Add your own categories. Built-in ones (Groceries, Dining, Travel...) stay as they are.</div>
    ${cur.length?cur.map(c=>`<div class="budget-row"><span class="br-label"><b>${esc(c.name)}</b>${c.keywords.length?` <span class="sub-dim">${esc(c.keywords.join(', '))}</span>`:''}</span>
      <button class="btn ghost sm" onclick="removeCategory('${ejs(c.name)}')">Remove</button></div>`).join(''):'<div class="sub-dim" style="margin-bottom:10px">No custom categories yet.</div>'}
    <div class="fine" style="margin:14px 0 6px">Quick add</div>
    <div>${CATEGORY_SUGGESTIONS.filter(n=>!have.has(n.toLowerCase())).map(n=>`<span class="due-chip" onclick="quickAddCategory('${n}')">+ ${n}</span>`).join('')}</div>
    <div style="margin-top:16px"><button class="btn solid sm" onclick="closeStmtDetail();createCategoryDialog().then(n=>{if(n)manageCategories();})">New category…</button></div>`;
  document.getElementById('stmtDetailOverlay').classList.add('show');
}
async function quickAddCategory(name){
  try{await api('/api/spending/categories',{method:'POST',body:{name}});SPEND_CATEGORIES=null;toast('Added '+name);manageCategories();}
  catch(e){let m='Could not add';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}
async function removeCategory(name){
  if(!await askConfirm(`"${name}" is removed and its transactions go back to Other.`,{title:'Remove category?',ok:'Remove'}))return;
  await api('/api/spending/categories/'+encodeURIComponent(name),{method:'DELETE'});
  SPEND_CATEGORIES=null;_cardsTxnCache=null;toast('Removed');manageCategories();if(currentPage==='cards')renderCards();
}
/* a category <select> with a "+ New category…" entry: creates it, then applies it */
function categoryOptions(selected,includeOther=true){
  return SPEND_CATEGORIES.filter(c=>includeOther||c!=='Other').map(c=>`<option value="${esc(c)}" ${c===selected?'selected':''}>${esc(c)}</option>`).join('')+'<option value="__new__">+ New category…</option>';
}
async function resolveNewCategory(sel){
  if(sel.value!=='__new__')return sel.value;
  const name=await createCategoryDialog();
  if(!name){sel.value=sel.dataset.prev||'';return null;}
  return name;
}

/* ─── "Other" is the biggest category? Let the user name the biggest merchants once; it's remembered ─── */
async function renderCards(){
  const [cards,dues,stats,txns,cyc]=await Promise.all([
    api('/api/cards'),api('/api/dues?days=30'),api('/api/cards/stats'),
    api('/api/spending/transactions?days=800'),api('/api/spend-cycles').catch(()=>null)]);
  _cyc=cyc;
  _cardsTxnCache=txns;_spendFilter={category:null,sub:null,trip:null,month:null};
  const el=document.getElementById('page-cards');
  // update badge
  const overdue=dues.filter(d=>d.urgency==='overdue').length;
  const urgent=dues.filter(d=>d.urgency==='urgent').length;
  const dueBadge=document.getElementById('dueBadge');
  const dueBadgeMobile=document.getElementById('dueBadgeMobile');
  if(dues.length){dueBadge.textContent=dues.length;dueBadge.style.display='';
    if(dueBadgeMobile){dueBadgeMobile.textContent=dues.length;dueBadgeMobile.style.display='';}}
  else{dueBadge.style.display='none';if(dueBadgeMobile)dueBadgeMobile.style.display='none';}

  const cardHTML=cards.map(c=>{
    const s=c.latest_statement;
    const status=s?s.status:'none';
    const isPaid=status==='paid';
    const dueDate=s&&!isPaid?s.due_date:'';
    const totalDue=s&&!isPaid?s.total_due:0;
    const today_=today();
    const daysLeft=dueDate?Math.round((new Date(dueDate)-new Date(today_))/86400000):null;
    let urgency='';
    if(daysLeft!==null&&!isPaid){urgency=daysLeft<0?'overdue':daysLeft<=3?'urgent':'';}
    const grad=c.card_color||DEFAULT_GRAD;
    return `<div class="credit-card" style="background:${grad}" onclick="showCardDetail(${c.id})">
      <div class="cc-top">
        <div class="cc-bank">${esc(c.bank||c.name)}</div>
        <div class="cc-status ${status}">${status==='paid'?'✓ PAID':status==='partial'?'PARTIAL':status==='unpaid'?'UNPAID':'—'}</div>
      </div>
      <div class="cc-mid">
        <div class="cc-name">${esc(c.name)}</div>
        ${c.last_four?`<div class="cc-num">•••• ${esc(c.last_four)}</div>`:''}
      </div>
      <div class="cc-bottom">
        <div>
          ${isPaid?'':'<div class="cc-info">Due amount</div>'}
          <div class="cc-amount">${isPaid?'All settled':`${c.currency} ${s?fmt2(totalDue):'—'}`}</div>
          <div class="cc-due ${urgency}">${isPaid?(s?`Paid ${s.paid_date||''}`:''):(dueDate?`Due ${dueDate}${daysLeft!==null?(daysLeft<0?' (OVERDUE)':daysLeft===0?' (TODAY)':` (${daysLeft}d)`):''}`:'')}</div>
        </div>
        ${statementPatternLine(c.statement_pattern)}
      </div>
    </div>`;}).join('');

  el.innerHTML=`
    <div class="page-head">
      <div><div class="page-title">Credit cards</div><div class="page-sub">Statements, payments and where the money went</div></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${cards.length?`<button class="btn ghost sm" onclick="recategorizeAll()" title="Re-run categorization over all imported transactions">Re-classify spend</button>`:''}
        <button class="btn ghost sm" onclick="importCardCsv()">Import CSV</button>
        <button class="btn ghost sm" onclick="openEmailSyncInfo()">Email sync</button>
        <button class="btn solid sm" onclick="openAddCard()">Add card</button>
      </div>
    </div>
    ${cards.length?`<div class="ctabs" id="ctabs">${[['overview','Overview'],['spending','Spending'],['rewards','Rewards'],['abroad','Abroad']].map(([k,l])=>`<button data-t="${k}" onclick="showCardsTab('${k}')">${l}</button>`).join('')}<span class="ctabs-fill"></span><span id="cardsPeriodWrap" style="display:none"><select id="cardsPeriod" aria-label="Period">${PERIODS[_cardsPeriod]?'':`<option value="${esc(_cardsPeriod)}" selected>${esc(periodLabel(_cardsPeriod))}</option>`}${Object.entries(PERIODS).map(([k,v])=>`<option value="${k}" ${k===_cardsPeriod?'selected':''}>${v}</option>`).join('')}<option value="__pick">Pick a month or dates…</option></select></span></div>`:''}
    <div class="ctab" data-t="overview">
    ${dues.length?`<div class="alert-bar"><span class="alert-icon"><svg class="i"><use href="#i-alert"/></svg></span>
      <div class="alert-text"><strong>${dues.length} payment${dues.length>1?'s':''} due within 30 days</strong><br>
      ${dues.map(d=>`<span class="due-chip ${d.urgency}">${esc(d.card_name)} · ${CUR} ${fmt(d.total_due)} · ${d.days_until_due<0?'overdue':d.days_until_due===0?'today':'in '+d.days_until_due+' days'}</span>`).join('')}
      </div></div>`:''}
    ${cards.length?`<div class="stat-row">
      <div class="stat hero"><div class="lbl">Total outstanding</div>
        <div class="val"><span class="cur">${CUR}</span>${fmt(stats.total_outstanding)}</div>
        <div class="meta">${stats.unpaid_count} unpaid · ${stats.paid_count} paid</div></div>
      <div class="stat"><div class="lbl">Paid this month</div><div class="val">${fmt(stats.paid_this_month)}</div><div class="meta">${CUR} settled</div></div>
    </div>`:''}
    ${cards.length?`<div class="card-grid">${cardHTML}</div>`
    :`<div class="table-wrap"><div class="empty">No cards yet. Add your first card to track statements and spending.</div></div>`}
    <div id="cardDetailArea"></div>
    <div id="cardInsights"></div>
    </div>
    <div class="ctab hidden" data-t="spending">
    ${cards.length?`<div id="ai-cards"></div>
    <div id="spKpis"></div>
    ${coverageNote()}
    <div class="pl-acts" style="margin:0 0 14px"><button class="btn ghost sm" onclick="manageCategories()">Manage categories</button></div>
    <div id="spendFilterArea"></div>
    <div class="two-col">
      <div class="chart-card"><h3>Where it went <span class="sub-dim">pick a category, then a type</span></h3><div id="cardCatChart"></div></div>
      <div class="chart-card"><h3>Trips <span class="sub-dim">pick one to filter</span></h3><div id="spTrips"></div></div>
    </div>
    <div id="uncatArea"></div>
    <div class="chart-grid"><div class="chart-card wide"><div class="cv-top"><h3>Spend per statement cycle <span class="sub-dim">pick a cycle to filter · * means a statement is missing</span></h3><span class="sp-seg" id="spStackSeg"></span></div><div id="cardMonthChart" class="chart-mount"></div></div></div>
    <div class="two-col">
      <div class="chart-card"><h3>Top merchants</h3><div id="cardMerchChart"></div></div>
      <div class="chart-card"><h3>By card</h3><div id="cardByCardChart"></div></div>
    </div>
    <div class="chart-card"><h3>By day of week <span class="sub-dim">busiest day highlighted</span></h3><div id="cardDowChart" class="chart-mount" style="min-height:200px"></div></div>
    <div id="spendTxnArea"></div>`:''}
    </div>
    <div class="ctab hidden" data-t="rewards"><div id="rewardsArea"></div></div>
    <div class="ctab hidden" data-t="abroad"><div id="cardValueArea"></div></div>`;
  _cardsTabDone={};

  if(cards.length){
    const ps=document.getElementById('cardsPeriod');
    if(ps)ps.onchange=async e=>{
      let v=e.target.value;
      if(v==='__pick'){v=await pickPeriod();if(!v){e.target.value=_cardsPeriod;return;}}
      _cardsPeriod=v;try{localStorage.setItem('cardsPeriod',_cardsPeriod);}catch(x){}
      _spendFilter.month=null;const cur=(document.querySelector('#ctabs button.active')||{dataset:{}}).dataset.t||'overview';_cardsTabDone={overview:true};
      if(!PERIODS[v]||!ps.querySelector(`option[value="${v}"]`))renderCards();else showCardsTab(cur,true);};
    loadAIInsights('cards','ai-cards');
    if(window.renderCardInsights)renderCardInsights();
    let t='overview';try{t=localStorage.getItem('cardsTab')||'overview';}catch(e){}
    showCardsTab(t);
  }
}

let _cardsTabDone={};
function showCardsTab(t,keepScroll){
  const root=document.getElementById('page-cards');if(!root)return;
  if(!root.querySelector(`.ctab[data-t="${t}"]`))t='overview';
  root.querySelectorAll('.ctab').forEach(e=>e.classList.toggle('hidden',e.dataset.t!==t));
  root.querySelectorAll('#ctabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  try{localStorage.setItem('cardsTab',t);}catch(e){}
  const pw=document.getElementById('cardsPeriodWrap');if(pw)pw.style.display=t==='overview'?'none':'';
  if(!_cardsTabDone[t]){      // charts need a visible container, so each tab draws itself the first time it opens
    _cardsTabDone[t]=true;
    if(t==='spending')renderSpendCharts();
    else if(t==='rewards'&&window.renderRewards)renderRewards();
    else if(t==='abroad'&&window.renderCardValue)renderCardValue();
  }
  if(!keepScroll)window.scrollTo({top:0,behavior:'instant'});
}
window.showCardsTab=showCardsTab;

async function showCardDetail(cardId){
  const [stmts,card]=await Promise.all([api(`/api/cards/${cardId}/statements`),api('/api/cards').then(cs=>cs.find(c=>c.id===cardId))]);
  const area=document.getElementById('cardDetailArea');
  if(!card){area.innerHTML='';return;}
  const rows=stmts.map(s=>`<tr class="clickable-row" onclick="openStatementDetail(${s.id})">
    <td>${s.statement_date}</td><td>${s.due_date}</td>
    <td class="num">${fmt2(s.total_due)}</td>
    <td class="num">${fmt2(s.minimum_due)}</td>
    <td class="num">${s.paid_amount?fmt2(s.paid_amount):'—'}</td>
    <td><span class="status-pill ${s.status}">${s.status}</span></td>
    <td class="num">
      ${s.source==='email'?'<span class="sub-dim" title="From email">email</span>':''}
      ${s.status!=='paid'?`<button class="btn pay-btn" onclick="event.stopPropagation();openPay(${s.id},${s.total_due},'${ejs(card.name)}')">Pay</button>`:''}
    </td>
  </tr>`).join('');
  area.innerHTML=`<hr class="section-sep">
    <div class="page-head" style="margin-top:8px">
      <div><div class="page-title" style="font-size:20px">${esc(card.name)} — Statements</div></div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" onclick="openEditCard(${cardId})">Edit Card</button>
        <button class="btn solid sm" onclick="openAddStmt(${cardId},'${ejs(card.name)}')">+ Add Statement</button>
      </div>
    </div>
    ${stmts.length?`<div class="table-wrap"><table class="stmt-table">
      <thead><tr><th>Statement Date</th><th>Due Date</th><th class="num">Total Due</th><th class="num">Minimum</th><th class="num">Paid</th><th>Status</th><th class="num">Actions</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
    :`<div class="table-wrap"><div class="empty" style="padding:30px"><div class="big" style="font-size:24px"></div>No statements yet. Add one manually or sync from email.</div></div>`}`;
}

/* ─── statement detail modal ────────────────────────────── */
let SPEND_CATEGORIES=null;
async function openStatementDetail(sid){
  const s=await api(`/api/statements/${sid}`);
  if(!SPEND_CATEGORIES)SPEND_CATEGORIES=await api('/api/spending/categories');
  const flat=await spTaxonomy();
  document.getElementById('stmtDetailTitle').textContent=`${s.card_name} — ${s.statement_date}`;

  const spendTxns=s.transactions.filter(t=>t.amount>0);
  const catTotals={};
  spendTxns.forEach(t=>{catTotals[t.category]=(catTotals[t.category]||0)+t.amount;});
  const catChips=Object.entries(catTotals).sort((a,b)=>b[1]-a[1])
    .map(([cat,tot])=>`<span class="due-chip">${esc(cat)} · ${s.currency} ${fmt2(tot)}</span>`).join('');

  const txnRows=s.transactions.map(t=>`<tr>
    <td>${t.txn_date}</td>
    <td>${esc(t.description)}</td>
    <td class="num" style="color:${t.amount>0?'var(--danger)':'var(--sage)'}">${t.amount>0?'':'+'}${fmt2(Math.abs(t.amount))}</td>
    ${stmtCatCell(t,flat)}
  </tr>`).join('');

  document.getElementById('stmtDetailBody').innerHTML=`
    <div class="form-grid" style="margin-bottom:14px">
      <div><div class="cc-info">Total Due</div><div style="font-family:var(--display);font-size:22px">${s.currency} ${fmt2(s.total_due)}</div></div>
      <div><div class="cc-info">Minimum Due</div><div style="font-size:16px">${s.currency} ${fmt2(s.minimum_due)}</div></div>
      <div><div class="cc-info">Due Date</div><div style="font-size:16px">${s.due_date}</div></div>
      <div><div class="cc-info">Status</div><div><span class="status-pill ${s.status}">${s.status}</span></div></div>
    </div>
    ${catChips?`<div style="margin-bottom:14px">${catChips}</div>`:''}
    ${s.transactions.length?`<div class="table-wrap" style="max-height:340px;overflow-y:auto"><table class="stmt-table">
      <thead><tr><th>Date</th><th>Description</th><th class="num">Amount</th><th>Category</th></tr></thead>
      <tbody>${txnRows}</tbody></table></div>`
    :`<div class="table-wrap"><div class="empty" style="padding:20px"><div class="big" style="font-size:20px"></div>No transaction line items captured for this statement.</div></div>`}
  `;
  document.getElementById('stmtDetailOverlay').classList.add('show');
}
function closeStmtDetail(){document.getElementById('stmtDetailOverlay').classList.remove('show');}
async function updateTxnCategory(tid,category,sub){
  try{
    const r=await api('/api/transactions/'+tid,{method:'PUT',body:{category,subcategory:sub||''}});
    toast(r.updated>1?`Category updated, applied to ${r.updated} transactions from this merchant`:'Category updated');
    _cardsTxnCache=await api('/api/spending/transactions?days=800').catch(()=>null);   // refetch now: the Spending tab redraws from this cache
    if(currentPage==='cards'&&document.getElementById('cardCatChart'))renderSpendCharts();
  }
  catch{toast('Failed','err');}
}

/* ─── plain-language money words: hover or tap the dotted label ─── */
const TERMS={
  runway:['Cash runway','How many months your cash and savings would last at what you really spend each month, if your income stopped.'],
  rate:['Savings rate','The share of your gross pay that you save or invest. 20% is a common target.'],
  readiness:['Financial readiness','A 0 to 100 score from your monthly cash flow, runway, bills on time and spending trend.'],
  free:['Free to invest','Money left after your emergency fund and dated goals are covered. Safe to invest for 5 or more years.'],
  idle:['Idle cash','Money sitting in a current account that earns nothing, above the buffer you chose to keep there.'],
  hurdle:['Hurdle','The return a riskier investment has to beat: what your savings account already pays you with no risk.'],
  tranche:['Tranche','One of several equal purchases spread over time, instead of investing everything at once.'],
  emergency:['Emergency fund','Cash for essentials only if income stops. Sized in months of essential spending (food, transport, health, family, education).'],
  swings:['Swings (volatility)','How much the price jumps around in a year. Higher means a bumpier ride, not a worse investment.'],
  worst:['Worst fall','The deepest drop from a peak in the last 5 years. It tells you what you must be able to sit through.'],
  overlap:['Overlap with the core fund','How closely it moves with your main fund. Near 1 means buying it adds little diversity; low means it moves independently.'],
  cagr:['Yearly growth','Average growth per year, compounded. Distributions are not included for funds that pay them out.'],
  rsi:['RSI','A 0 to 100 gauge of short-term momentum. Above 75 is called overheated, below 30 oversold.'],
  term:['Fixed-term holding','A deposit or bond that pays a fixed rate until its maturity date.'],
  irregular:['Irregular line','A cost that comes in lumps (trips, school fees). It is budgeted as a plan and judged over a year, not by one month.'],
  needs:['Needs and wants','Needs are costs you cannot easily stop (food, transport, health, family, education). Wants are the rest. A common guide is 50% needs, 30% wants, 20% saved.'],
  lean:['Essential spending','What you would still have to pay in a bad month: groceries, transport, health, bills, education and family support.']};
const tip=(label,key)=>{const t=TERMS[key];return t?`<span class="tip" tabindex="0" data-tip="${esc(t[1])}">${label}</span>`:label;};

/* ─── ways to keep more, needs and wants (backend/wealth.py) ─── */
function leaksHTML(l){
  if(!l||!l.items.length)return `<div class="chart-card"><h3>Ways to keep more</h3><div class="fine">Nothing is leaking right now: no fees, markup or idle cash worth chasing.</div></div>`;
  return `<div class="chart-card"><div class="panel-head"><h3>Ways to keep more <span class="sub-dim">money lost for no benefit</span></h3></div>
    <p style="margin:0 0 4px;font-size:15px"><b>About ${CUR} ${fmt(l.sure_per_year)} a year</b> you could simply stop losing${l.invested_value_10y?`. Invested at 8% that is about ${CUR} ${fmt(l.invested_value_10y)} in 10 years`:''}${l.once?`, plus ${CUR} ${fmt(l.once)} of unused rewards to claim once`:''}.</p>
    ${l.items.map(i=>`<div class="plan-item"><span class="plan-icon sev-ic-${i.kind==='sure'?'medium':'low'}">${sevIcon(i.kind==='sure'?'medium':'low')}</span><div style="flex:1;min-width:0">
      <div class="mk-sh"><b>${esc(i.title)}</b><span class="rw-chip">${CUR} ${fmt(i.per_year)}${i.once?' once':' a year'}</span></div><div class="fine" style="margin-top:3px">${esc(i.do)}</div></div></div>`).join('')}
    <div class="fine" style="margin-top:8px">${l.choice_per_year?`Another ${CUR} ${fmt(l.choice_per_year)} a year is spending you may decide to keep (recurring charges, wants above the guide). `:''}Fees and markup are certain losses; the rest is your choice.</div></div>`;
}
function needsHTML(n){
  if(!n)return '';
  const row=(l,v,g,hi)=>`<div class="pl-wf-top" style="padding:7px 0;border-bottom:1px solid var(--line)"><span>${l}</span><span><b>${v}%</b> <span class="sub-dim">guide ${g}%</span> <span style="color:${hi?'var(--warn)':'var(--pos)'}">${hi?(v>g?'above':'below'):'on track'}</span></span></div>`;
  return `<div class="chart-card"><h3>${tip('Needs, wants','needs')} and what you keep <span class="sub-dim">share of take-home, a usual month</span></h3><div id="needBar"></div>
    ${row('Needs <span class="sub-dim">rent-type costs, food, transport, family, education</span>',n.pct.needs,n.guide.needs,false)}${row('Wants <span class="sub-dim">dining out, shopping, trips, treats</span>',n.pct.wants,n.guide.wants,n.pct.wants>n.guide.wants+2)}${row('Kept <span class="sub-dim">saved and invested</span>',n.pct.keep,n.guide.keep,n.pct.keep<n.guide.keep-2)}
    <div class="fine" style="margin-top:8px">${n.tagging?`Sorting ${n.pending} more merchants into needs and wants in the background. Reload in a minute and these figures settle. `:''}${esc(n.note)}</div>
    <details class="more" id="mrDet"><summary>Check how my merchants are sorted</summary><div id="mrList" class="fine">Loading…</div></details></div>`;
}
function reportHTML(r){
  if(!r||!r.lines.length)return '';
  const d=r.actual_total-r.planned_total,mx=Math.max(...r.lines.filter(l=>!l.lump).map(l=>Math.max(l.planned,l.actual)),1);
  return `<div class="chart-card"><h3>How the last cycle went <span class="sub-dim">${esc(fmtMonth(r.cycle))} statements against your plan</span></h3>
    <p style="margin:0 0 8px">Everyday lines: planned <b>${CUR} ${fmt(r.planned_total)}</b>, actual <b>${CUR} ${fmt(r.actual_total)}</b>, <b style="color:${d>0?'var(--neg)':'var(--pos)'}">${d>0?CUR+' '+fmt(d)+' over':CUR+' '+fmt(-d)+' under'}</b>. Irregular lines show the running year: unspent allowance carries over.</p>
    ${r.lines.map(l=>l.lump&&l.months?`<div class="rp-row"><span class="rp-n">${esc(l.label)} <span class="sub-dim">irregular</span></span>
      <span class="rp-bars"><i class="rp-p" style="width:100%"></i><i class="rp-a ${l.carry<0?'over':''}" style="width:${Math.min(100,l.year_spent/Math.max(l.year_allowed,1)*100)}%"></i></span><span class="rp-v" title="This cycle ${fmt(l.actual)}. The allowance of ${CUR} ${fmt(l.planned)} a month builds up and unspent months carry over.">${fmt(l.year_spent)} <span class="sub-dim">of ${fmt(l.year_allowed)} over ${l.months} mo</span> <b style="color:${l.carry<0?'var(--neg)':'var(--pos)'}">${l.carry<0?fmt(-l.carry)+' over':fmt(l.carry)+' to carry'}</b></span></div>`
      :`<div class="rp-row"><span class="rp-n">${esc(l.label)}</span>
      <span class="rp-bars"><i class="rp-p" style="width:${l.planned/mx*100}%"></i><i class="rp-a ${l.actual>l.planned*1.1+50?'over':''}" style="width:${l.actual/mx*100}%"></i></span><span class="rp-v">${fmt(l.actual)} <span class="sub-dim">of ${fmt(l.planned)}</span></span></div>`).join('')}
    <div class="fine" style="margin-top:6px">Grey is the plan, the coloured bar is what happened. Statements arrive after a cycle closes, so this is last month, not this week.</div></div>`;
}
function drawNeedBar(n){
  const det=document.getElementById('mrDet');
  if(det)det.addEventListener('toggle',async()=>{if(!det.open||det.dataset.done)return;det.dataset.done=1;
    const m=await api('/api/wealth/merchants');
    document.getElementById('mrList').innerHTML=m.map(x=>`<div class="tk-row" data-k="${esc(x.key)}" style="grid-template-columns:1fr 110px 62px"><span class="tk-n" title="${esc(x.label)}">${esc(x.label)} <span class="sub-dim">${esc(x.category)}</span></span>
      <select class="mr-side"><option value="need" ${x.side==='need'?'selected':''}>Need</option><option value="want" ${x.side==='want'?'selected':''}>Want</option><option value="give" ${x.side==='give'?'selected':''}>Giving</option></select><b>${fmt(x.total)}</b></div>`).join('');
    document.getElementById('mrList').onchange=async e=>{const s=e.target.closest('.mr-side');if(!s)return;await api('/api/wealth/need',{method:'PUT',body:{key:s.closest('[data-k]').dataset.k,need:s.value}});toast('Saved. The split updates next time you open Budget.');};
  });
  const el=document.getElementById('needBar');if(!el||!n)return;
  MiniChart.stackBar(el,[{label:'Needs',value:n.needs},{label:'Wants',value:n.wants},{label:'Kept',value:n.saved,color:'var(--pos)'}].filter(x=>x.value>0),{total:n.take_home,label:'Share of take-home'});
}

/* ─── budget ────────────────────────────────────────────── */
let _bd=null;
const BL_TYPES={income:{title:'Income',sub:'Money coming in each month, after payroll deductions',color:'var(--pos)',add:'Add income'},
  expense:{title:'Spending',sub:'Planned against what your cards really spent (your usual month: median of each category over the last 6 complete statement cycles)',color:'var(--ink)',add:'Add spending line'},
  savings:{title:'Savings',sub:'Money you put aside',color:'var(--info)',add:'Add savings line'},
  invest:{title:'Investing',sub:'Money you put into investments',color:'var(--accent)',add:'Add investment'}};
const FREQ_TXT={monthly:'',quarterly:' every 3 months',semester:' every 4 months',yearly:' a year'};
const SRC_TXT={payslip_typical:'follows your payslips (typical month)',payslip_average:'follows your payslips (12-month average, with bonus)'};

async function renderBudget(){
  const [data,act,inc,chk,nw,lk,rc]=await Promise.all([api('/api/budget'),api('/api/budget/actuals').catch(()=>null),api('/api/income-basis').catch(()=>null),api('/api/budget/checkup').catch(()=>[]),api('/api/wealth/needs-wants').catch(()=>null),api('/api/wealth/leaks').catch(()=>null),api('/api/budget/report-card').catch(()=>null)]);
  _bd=data;
  const el=document.getElementById('page-budget');
  const planned=data.expenses+data.savings+data.invest;
  const gross=inc&&inc.gross?inc.gross:data.income;
  const sRate=gross?(data.savings+data.invest)/gross*100:0;
  const real=act&&act.cycles.length?act.realistic_surplus:null;
  const gapTxt=v=>v==null?'':`${v>0?'+':'−'}${fmt(Math.abs(v))}`;
  const rows=type=>{
    const items=data.items.filter(i=>i.type===type),isExp=type==='expense';
    if(!items.length)return `<div class="sub-dim" style="padding:12px 0">Nothing here yet.</div>`;
    return (isExp?`<div class="bl-row bl-head"><span>Line</span><span class="bl-num">Planned / mo</span><span class="bl-num bl-actual">Actual / mo</span><span class="bl-num bl-gap">Difference</span><span></span></div>`:'')+
      items.map(i=>{
        const actual=isExp&&i.categories.length&&act?act.items[i.id]:null,gap=actual!=null?actual-i.monthly:null;
        const sub=[i.frequency!=='monthly'?`${CUR} ${fmt(i.amount_shown)}${FREQ_TXT[i.frequency]}`:'',SRC_TXT[i.source]||'',i.lump?'irregular':'',i.categories.length?'':(isExp?(i.off_card?'paid outside your cards':'<not linked yet>'):'')].filter(Boolean).join(' · ');
        return `<div class="bl-row ${isExp?'':'noact'}">
          <div><div class="bl-name">${esc(i.label)}</div>${sub?`<div class="bl-sub" ${sub.includes('<not linked yet>')?'style="color:var(--warn)"':''}>${esc(sub.replace('<not linked yet>','not linked to your cards yet'))}</div>`:''}</div>
          <div class="bl-num">${fmt(i.monthly)}</div>
          ${isExp?`<div class="bl-num bl-actual">${actual!=null?fmt(actual):'<span class="sub-dim">—</span>'}</div>
            <div class="bl-num bl-gap ${gap==null?'':gap>i.monthly*0.1+50?'over':gap<-(i.monthly*0.1+50)?'under':''}">${gap!=null?gapTxt(gap):''}</div>`:''}
          <div class="bl-act"><button class="btn ghost sm" onclick="openBudgetItem(${i.id})" aria-label="Edit ${esc(i.label)}">Edit</button></div>
        </div>`;}).join('');
  };
  const section=type=>{const m=BL_TYPES[type],total=data.items.filter(i=>i.type===type).reduce((a,i)=>a+i.monthly,0);
    return `<div class="chart-card" style="margin-bottom:20px"><div class="panel-head"><h3 style="color:${type==='expense'?'var(--ink)':m.color}">${m.title} <span class="sub-dim">${CUR} ${fmt(total)} a month</span></h3>
      <button class="btn ghost sm" onclick="openBudgetItem(null,{type:'${type}'})">${m.add}</button></div>
      ${rows(type)}</div>`;};
  const uns=act&&act.unsorted?act.unsorted:[];
  const unb=act&&act.unbudgeted&&!uns.length?act.unbudgeted:[];
  const unlinked=uns.length?`<div class="chart-card" style="margin-bottom:20px"><h3>Link your spending lines <span class="sub-dim">${uns.length} to go</span></h3>
      ${uns.map(u=>`<div class="bl-row noact"><div class="bl-name">${esc(u.label)}</div><div class="bl-num">${fmt(u.monthly)}</div><div class="bl-act"><button class="btn ghost sm" onclick="openBudgetItem(${u.id})">Link</button></div></div>`).join('')}</div>`:'';
  const nolines=unb.length?`<div class="chart-card" style="margin-bottom:20px"><div class="panel-head"><h3>Spending without a line <span class="sub-dim">per month</span></h3><button class="btn ghost sm" onclick="addAllUnbudgeted()">Add a line for each</button></div>
      ${unb.map(u=>`<div class="bl-row noact"><div class="bl-name">${esc(u.category)}</div><div class="bl-num">${fmt(u.monthly)}</div>
        <div class="bl-act"><button class="btn ghost sm" onclick="openBudgetItem(null,{type:'expense',label:'${ejs(u.category)}',amount:${Math.round(u.monthly/50)*50},categories:['${ejs(u.category)}']})">Add line</button></div></div>`).join('')}</div>`:'';
  const payroll=act&&act.payroll.items.length?`<details class="more"><summary>Already taken from your salary: ${CUR} ${fmt(act.payroll.monthly)} a month</summary>
      ${act.payroll.items.map(p=>`<div class="budget-row"><span class="br-label">${esc(p.label)}</span><span class="br-amount">${CUR} ${fmt(p.monthly)}</span></div>`).join('')}</details>`:'';
  const checks=chk&&chk.length?`<div class="chart-card"><h3>Things to check</h3>${chk.map(c=>`<div class="plan-item"><span class="plan-icon sev-ic-${c.level==='ask'?'medium':'low'}">${sevIcon(c.level==='ask'?'medium':'low')}</span><p><b>${esc(c.title)}</b><br><span class="fine">${esc(c.body)}</span></p></div>`).join('')}</div>`:'';
  el.innerHTML=`
    <div class="page-head"><div><div class="page-title">Budget</div></div><button class="btn solid sm" onclick="openBudgetItem(null)">Add line</button></div>
    <div class="ctabs" id="bgTabs"><button data-t="plan">Plan</button><button data-t="last">Last cycle</button><button data-t="improve">Improve</button></div>
    <div class="ctab" data-t="plan">
      <div class="kpis" style="margin-top:0">
        <div class="kpi"><div class="kpi-l">Take-home</div><div class="kpi-v">${fmt(data.income)}<small>${CUR}</small></div><div class="kpi-m">Typical month.</div></div>
        <div class="kpi"><div class="kpi-l">Planned left over</div><div class="kpi-v" style="${data.surplus<0?'color:var(--neg)':''}">${fmt(data.surplus)}<small>${CUR}</small></div><div class="kpi-m">After ${fmt(data.expenses)} spending, ${fmt(data.savings+data.invest)} saved and invested.</div></div>
        <div class="kpi ${real==null?'':real<0?'bad':real<planned*0.05?'warn':'good'}"><div class="kpi-l">Realistic left over</div><div class="kpi-v">${real!=null?fmt(real):'—'}<small>${CUR}</small></div><div class="kpi-m">${real!=null?`At your real spending of the last ${act.cycles.length} cycles.`:(uns.length?'Link your spending lines first.':'Needs card statements.')}</div></div>
        <div class="kpi ${sRate>=20?'good':sRate>=10?'warn':'bad'}"><div class="kpi-l">${tip('Savings rate','rate')}</div><div class="kpi-v">${Math.round(sRate)}<small>%</small></div><div class="kpi-m">Of ${CUR} ${fmt(gross)} gross pay.</div></div>
      </div>
      ${real!=null&&real<data.surplus-500?`<div class="alert-bar" style="margin-top:16px"><span class="alert-icon"><svg class="i"><use href="#i-alert"/></svg></span><div class="alert-text"><strong>Reality is ${CUR} ${fmt(data.surplus-real)} a month tighter than the plan.</strong></div></div>`:''}
      <div class="chart-card" style="margin:20px 0"><h3>Where your take-home goes</h3><div id="budgetFlow"></div>${data.surplus<0?`<div class="fine" style="color:var(--neg)">Over-allocated by ${CUR} ${fmt(-data.surplus)} a month.</div>`:''}</div>
      ${['income','expense','savings','invest'].map(section).join('')}${unlinked}${nolines}${payroll}
    </div>
    <div class="ctab hidden" data-t="last">${reportHTML(rc)}<div style="margin-top:20px">${needsHTML(nw)}</div></div>
    <div class="ctab hidden" data-t="improve"><div id="ai-budget"></div><div style="margin-top:20px">${leaksHTML(lk)}</div><div style="margin-top:20px">${checks}</div></div>`;
  MiniChart.stackBar(document.getElementById('budgetFlow'),[
    {label:'Spending',value:data.expenses},{label:'Savings',value:data.savings},{label:'Investing',value:data.invest},
    {label:'Left over',value:Math.max(0,data.surplus),color:'var(--track)'}].filter(x=>x.value>0));
  const bdone={};
  const showB=t=>{el.querySelectorAll('.ctab').forEach(e=>e.classList.toggle('hidden',e.dataset.t!==t));el.querySelectorAll('#bgTabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
    try{localStorage.setItem('bgTab',t);}catch(e){}
    if(!bdone[t]){bdone[t]=1;if(t==='last')drawNeedBar(nw);else if(t==='improve')loadAIInsights('budget','ai-budget');}};
  document.getElementById('bgTabs').onclick=e=>{const b=e.target.closest('button');if(b)showB(b.dataset.t);};
  let t0='plan';try{t0=localStorage.getItem('bgTab')||'plan';}catch(e){}showB(t0);
}

let _biId=null,_biCats=new Set();
async function openBudgetItem(id,preset={}){
  if(!SPEND_CATEGORIES)SPEND_CATEGORIES=await api('/api/spending/categories');
  const it=id?_bd.items.find(i=>i.id===id):null;_biId=id||null;
  const v=it||{type:'expense',label:'',amount:'',frequency:'monthly',source:'manual',categories:[],...preset};
  document.getElementById('biTitle').textContent=it?'Edit budget line':'Add budget line';
  setV('bi_label',v.label);setV('bi_type',v.type);setV('bi_amount',it?it.amount:v.amount);setV('bi_freq',v.frequency);setV('bi_source',v.source);
  _biCats=new Set(v.categories||[]);
  document.getElementById('bi_off').checked=!!(it&&it.off_card);document.getElementById('bi_lump').checked=!!(it&&it.lump);
  if(it&&it.type==='expense'&&!it.categories.length&&!it.off_card){        // suggest categories named like the line
    SPEND_CATEGORIES.forEach(c=>{if(it.label.toLowerCase().includes(c.toLowerCase())||c.toLowerCase().includes(it.label.toLowerCase()))_biCats.add(c);});
  }
  document.getElementById('biDelete').style.display=it?'':'none';
  drawBudgetModal();
  document.getElementById('budgetOverlay').classList.add('show');
  setTimeout(()=>document.getElementById('bi_label').focus(),40);
}
function drawBudgetModal(){
  const t=getV('bi_type');
  document.getElementById('bi_srcwrap').style.display=t==='income'?'':'none';
  document.getElementById('bi_catwrap').style.display=t==='expense'?'':'none';
  document.getElementById('bi_cats').style.display=document.getElementById('bi_off').checked?'none':'';
  document.getElementById('bi_lumpwrap').style.display=document.getElementById('bi_off').checked?'none':'flex';
  document.getElementById('bi_amount').disabled=t==='income'&&getV('bi_source')!=='manual';
  document.getElementById('bi_cats').innerHTML=SPEND_CATEGORIES.filter(c=>!['Internal','Payments & Transfers'].includes(c)).map(c=>
    `<button type="button" class="filter-chip ${_biCats.has(c)?'active':''}" data-c="${esc(c)}">${esc(c)}</button>`).join('');
  document.querySelectorAll('#bi_cats .filter-chip').forEach(b=>b.onclick=()=>{const c=b.dataset.c;_biCats.has(c)?_biCats.delete(c):_biCats.add(c);b.classList.toggle('active');});
}
function closeBudgetModal(){document.getElementById('budgetOverlay').classList.remove('show');}
async function saveBudgetItem(){
  const body={label:getV('bi_label'),type:getV('bi_type'),amount:getV('bi_amount'),frequency:getV('bi_freq'),source:getV('bi_source'),off_card:document.getElementById('bi_off').checked,lump:document.getElementById('bi_lump').checked,categories:document.getElementById('bi_off').checked?[]:[..._biCats]};
  try{
    if(_biId)await api('/api/budget/'+_biId,{method:'PUT',body});else await api('/api/budget',{method:'POST',body});
    closeBudgetModal();toast(_biId?'Line updated':'Line added');renderBudget();
  }catch(e){let m='Could not save';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}
async function deleteBudgetItem(){
  if(!_biId)return;const it=_bd.items.find(i=>i.id===_biId);
  if(!await askConfirm(`"${it?it.label:'This line'}" will be removed from your budget.`,{title:'Remove this line?',ok:'Remove'}))return;
  await api('/api/budget/'+_biId,{method:'DELETE'});closeBudgetModal();toast('Line removed');renderBudget();
}
async function addAllUnbudgeted(){
  const act=await api('/api/budget/actuals');
  if(!await askConfirm(`${act.unbudgeted.length} spending lines will be added, each set to your recent average and linked to its category. You can edit or remove any of them.`,{title:'Add lines for your real spending?',ok:'Add them',danger:false}))return;
  for(const u of act.unbudgeted)await api('/api/budget',{method:'POST',body:{label:u.category,type:'expense',amount:Math.round(u.monthly/50)*50,categories:[u.category]}});
  toast('Lines added');renderBudget();
}

/* ─── holding modal ─────────────────────────────────────── */
function applyMode(){
  const m=getV('f_price_mode');
  document.querySelectorAll('.mode-ticker').forEach(e=>e.style.display=(m==='ticker'||m==='nav')?'flex':'none');
  document.querySelectorAll('.mode-manual').forEach(e=>e.style.display=m==='manual'?'flex':'none');
}
function openAddHolding(cls){
  editingHoldingId=null;
  document.getElementById('modalTitle').textContent='Add Holding';
  document.getElementById('deleteHoldingBtn').style.display='none';
  document.getElementById('searchZone').style.display='block';
  document.getElementById('searchInput').value='';
  document.getElementById('searchResults').innerHTML='';
  ['f_name','f_platform','f_symbol','f_units','f_last_price','f_manual_value','f_cost_basis','f_target_pct','f_notes'].forEach(id=>setV(id,''));
  setV('f_asset_class',cls||'stock');setV('f_geography','');setV('f_currency',CUR);
  setV('f_price_mode',(cls==='stock'||cls==='etf')?'ticker':(cls==='mutual_fund'?'nav':'manual'));
  document.getElementById('f_is_liability').checked=false;
  applyMode();
  document.getElementById('modalOverlay').classList.add('show');
}
async function openEditHolding(id){
  editingHoldingId=id;
  const all=await api('/api/holdings');const h=all.find(x=>x.id===id);if(!h)return;
  document.getElementById('modalTitle').textContent='Edit Holding';
  document.getElementById('deleteHoldingBtn').style.display='block';
  document.getElementById('searchZone').style.display='none';
  setV('f_name',h.name);setV('f_asset_class',h.asset_class);setV('f_price_mode',h.price_mode);
  setV('f_platform',h.platform);setV('f_geography',h.geography);setV('f_currency',h.currency);
  setV('f_symbol',h.symbol);setV('f_units',h.units);setV('f_last_price',h.last_price);
  setV('f_manual_value',h.manual_value);setV('f_cost_basis',h.cost_basis);setV('f_target_pct',h.target_pct);
  setV('f_notes',h.notes);document.getElementById('f_is_liability').checked=!!h.is_liability;
  applyMode();document.getElementById('modalOverlay').classList.add('show');
}
function closeHoldingModal(){document.getElementById('modalOverlay').classList.remove('show');}
async function saveHolding(){
  const body={name:getV('f_name'),asset_class:getV('f_asset_class'),price_mode:getV('f_price_mode'),
    platform:getV('f_platform'),geography:getV('f_geography'),currency:getV('f_currency'),
    symbol:getV('f_symbol'),units:parseFloat(getV('f_units'))||0,last_price:parseFloat(getV('f_last_price'))||0,
    manual_value:parseFloat(getV('f_manual_value'))||0,cost_basis:parseFloat(getV('f_cost_basis'))||0,
    target_pct:parseFloat(getV('f_target_pct'))||0,notes:getV('f_notes'),
    is_liability:document.getElementById('f_is_liability').checked};
  try{
    if(editingHoldingId)await api('/api/holdings/'+editingHoldingId,{method:'PUT',body});
    else await api('/api/holdings',{method:'POST',body});
    closeHoldingModal();summary=null;go(currentPage);toast('Saved');
  }catch(e){toast('Failed','err');}
}
async function deleteHolding(){
  if(!editingHoldingId||!await askConfirm('It will be removed from your portfolio and net worth.',{title:'Delete this holding?',ok:'Delete'}))return;
  await api('/api/holdings/'+editingHoldingId,{method:'DELETE'});
  closeHoldingModal();summary=null;go(currentPage);toast('Removed');
}

/* ─── search ────────────────────────────────────────────── */
let _st;
function onSearch(){
  const q=document.getElementById('searchInput').value.trim();
  clearTimeout(_st);
  if(q.length<2){document.getElementById('searchResults').innerHTML='';return;}
  const spin=document.getElementById('searchSpin');spin.classList.add('on');
  _st=setTimeout(async()=>{
    try{
      const cls=getV('f_asset_class');
      const kind=cls==='mutual_fund'?'mutual_fund':'all';
      const res=await api(`/api/search?q=${encodeURIComponent(q)}&kind=${kind}`);
      renderSearchResults(res);
    }catch{document.getElementById('searchResults').innerHTML='<div class="sr"><span class="sr-name">Search unavailable — enter manually.</span></div>';}
    finally{spin.classList.remove('on');}
  },350);
}
function renderSearchResults(res){
  const box=document.getElementById('searchResults');
  if(!res.length){box.innerHTML='<div class="sr"><span class="sr-name sub-dim">No matches. Enter manually below.</span></div>';return;}
  box.innerHTML=res.slice(0,10).map(r=>`<div class="sr" onclick='pickResult(${JSON.stringify(r).replace(/'/g,"&#39;")})'>
    <span class="sr-sym">${esc(r.symbol)}</span><span class="sr-name">${esc(r.name)}</span>
    <span class="sr-ex">${esc(r.exchange||'')}</span></div>`).join('');
}
async function pickResult(r){
  setV('f_name',r.name||r.symbol);setV('f_symbol',r.symbol);
  if(r.currency)setV('f_currency',r.currency);
  const isMF=r.type==='mutual_fund';
  setV('f_price_mode',isMF?'nav':'ticker');
  if(r.type==='etf')setV('f_asset_class','etf');
  else if(r.type==='equity')setV('f_asset_class','stock');
  else if(isMF)setV('f_asset_class','mutual_fund');
  else if(r.type==='cryptocurrency')setV('f_asset_class','crypto');
  if(r.currency==='USD')setV('f_geography','US');
  applyMode();
  document.getElementById('searchResults').innerHTML='<div class="sr"><span class="sr-name">Fetching price…</span></div>';
  try{
    const qd=await api(`/api/quote?symbol=${encodeURIComponent(r.symbol)}&mode=${isMF?'nav':'ticker'}`);
    if(qd&&qd.price){
      setV('f_last_price',qd.price);
      document.getElementById('searchResults').innerHTML=`<div class="sr"><span class="sr-sym">${esc(r.symbol)}</span><span class="sr-name">${esc(qd.name||r.name)}</span><span class="sr-price">${fmt2(qd.price)} ${qd.currency||''}</span></div>`;
    }else document.getElementById('searchResults').innerHTML='';
  }catch{document.getElementById('searchResults').innerHTML='';}
}

/* ─── card modal ────────────────────────────────────────── */
function openAddCard(){
  editingCardId=null;
  document.getElementById('cardModalTitle').textContent='Add Credit Card';
  document.getElementById('deleteCardBtn').style.display='none';
  ['c_name','c_last_four','c_notes','c_email_sender','c_email_subject','c_amount_label'].forEach(id=>setV(id,''));
  setV('c_bank','');api('/api/cards/banks').then(l=>{const d=document.getElementById('bankList');if(d)d.innerHTML=l.map(b=>`<option>${esc(b)}</option>`).join('');}).catch(()=>{});setV('c_currency',CUR);setV('c_stmt_day',1);setV('c_due_days',25);
  document.getElementById('cardModalOverlay').classList.add('show');
}
async function openEditCard(id){
  editingCardId=id;
  const cards=await api('/api/cards');const c=cards.find(x=>x.id===id);if(!c)return;
  document.getElementById('cardModalTitle').textContent='Edit Card';
  document.getElementById('deleteCardBtn').style.display='block';
  setV('c_name',c.name);setV('c_bank',c.bank);setV('c_last_four',c.last_four);
  setV('c_currency',c.currency);
  setV('c_stmt_day',c.statement_day);setV('c_due_days',c.due_days);
  setV('c_pdf_password',c.pdf_password||'');setV('c_notes',c.notes);
  setV('c_email_sender',c.email_sender||'');setV('c_email_subject',c.email_subject||'');
  setV('c_amount_label',c.amount_label||'');
  document.getElementById('cardModalOverlay').classList.add('show');
}
function closeCardModal(){document.getElementById('cardModalOverlay').classList.remove('show');}
async function saveCard(){
  const body={name:getV('c_name'),bank:getV('c_bank'),last_four:getV('c_last_four'),
    currency:getV('c_currency'),
    statement_day:parseInt(getV('c_stmt_day'))||1,due_days:parseInt(getV('c_due_days'))||25,
    pdf_password:getV('c_pdf_password'),notes:getV('c_notes'),
    email_sender:getV('c_email_sender'),email_subject:getV('c_email_subject'),
    amount_label:getV('c_amount_label')};
  try{
    if(editingCardId)await api('/api/cards/'+editingCardId,{method:'PUT',body});
    else await api('/api/cards',{method:'POST',body});
    closeCardModal();renderCards();toast('Saved');
  }catch{toast('Failed','err');}
}
async function deleteCard(){
  if(!editingCardId||!await askConfirm('The card and all of its statements will be deleted.',{title:'Delete this card?',ok:'Delete'}))return;
  await api('/api/cards/'+editingCardId,{method:'DELETE'});
  closeCardModal();renderCards();toast('Removed');
}

/* ─── statement modal ───────────────────────────────────── */
function openAddStmt(cardId,cardName){
  addStmtCardId=cardId;
  document.getElementById('stmtCardName').textContent=cardName;
  setV('s_stmt_date',today());setV('s_due_date','');setV('s_total',0);setV('s_min',0);setV('s_notes','');
  document.getElementById('stmtModalOverlay').classList.add('show');
}
function closeStmtModal(){document.getElementById('stmtModalOverlay').classList.remove('show');}
async function saveStmt(){
  const body={statement_date:getV('s_stmt_date'),due_date:getV('s_due_date'),
    total_due:parseFloat(getV('s_total'))||0,minimum_due:parseFloat(getV('s_min'))||0,
    notes:getV('s_notes'),source:'manual'};
  try{
    await api('/api/cards/'+addStmtCardId+'/statements',{method:'POST',body});
    closeStmtModal();renderCards();toast('Statement added');
  }catch{toast('Failed','err');}
}

/* ─── pay modal ─────────────────────────────────────────── */
function openPay(stmtId,total,cardName){
  payingStmtId=stmtId;
  document.getElementById('payStmtInfo').textContent=`${cardName} · Total due: ${CUR} ${fmt2(total)}`;
  setV('p_amount',total);setV('p_date',today());
  document.getElementById('payModalOverlay').classList.add('show');
}
function closePayModal(){document.getElementById('payModalOverlay').classList.remove('show');}
async function savePay(){
  try{
    await api('/api/statements/'+payingStmtId+'/pay',{method:'POST',body:{
      amount:parseFloat(getV('p_amount'))||0,paid_date:getV('p_date')}});
    closePayModal();renderCards();toast('Payment recorded ✓');
  }catch{toast('Failed','err');}
}

/* ─── quick update ──────────────────────────────────────── */
function openQuickUpdate(holdingId,name){
  quickUpdateHoldingId=holdingId;
  document.getElementById('quickUpdateName').textContent=name;
  setV('qu_value','');document.getElementById('qu_csv').value='';
  document.getElementById('quickUpdateOverlay').classList.add('show');
}
function closeQuickUpdate(){document.getElementById('quickUpdateOverlay').classList.remove('show');}
async function saveQuickUpdate(){
  const csvFile=document.getElementById('qu_csv').files[0];
  const val=parseFloat(getV('qu_value'));
  try{
    if(csvFile){
      const fd=new FormData();fd.append('file',csvFile);fd.append('holding_id',quickUpdateHoldingId);
      const r=await fetch('/api/import/statement',{method:'POST',body:fd,headers:{'X-Wealth':'1'}});
      const d=await r.json();
      if(d.ok){closeQuickUpdate();summary=null;go(currentPage);toast(`Updated from CSV: ${CUR} ${fmt2(d.balance)}`);}
      else toast('Could not read balance from CSV','err');
    }else if(val>0){
      await api('/api/holdings/'+quickUpdateHoldingId+'/quick-update',{method:'POST',body:{value:val}});
      closeQuickUpdate();summary=null;go(currentPage);toast('Balance updated');
    }else toast('Enter a value or select a CSV file','err');
  }catch{toast('Failed','err');}
}

/* ─── email sync ────────────────────────────────────────── */
async function loadEmailStatus(){
  try{
    const s=await api('/api/email/status');
    const imap=s.method==='imap',expired=s.status==='expired',connected=s.status==='connected';
    document.getElementById('esStatus').textContent=connected?(imap?`Connected via App Password (${s.account})`:'Connected'):expired?'Access expired, reconnect':'Not connected';
    document.getElementById('esStatus').style.color=connected?'var(--pos)':expired?'var(--neg)':'var(--ink-dim)';
    document.getElementById('esLastSync').textContent=s.last_sync?s.last_sync.slice(0,16).replace('T',' '):'Never';
    document.getElementById('esFound').textContent=s.emails_found||0;
    document.getElementById('imapForm').style.display=imap?'none':'';
    document.getElementById('imapSaveBtn').style.display=imap?'none':'';
    document.getElementById('imapRemoveBtn').style.display=imap?'':'none';
    document.getElementById('connectGmailBtn').style.display=(connected||!s.libs_available||!s.creds_found)?'none':'';
    document.getElementById('connectGmailBtn').textContent=expired?'Reconnect with Google':'Connect with Google sign-in';
    document.getElementById('syncEmailBtn').style.display=connected?'':'none';
    document.getElementById('backfillEmailBtn').style.display=connected?'':'none';
    document.getElementById('disconnectGmailBtn').style.display=(connected&&!imap)||expired?'':'none';
    document.getElementById('esNote').textContent=expired
      ?'Google revoked the saved sign-in (it expires every 7 days while your Google Cloud app is in Testing). Use an App Password above instead; it never expires.'
      :imap?'Using your App Password over IMAP. Read-only; nothing is changed in your mailbox.'
      :(s.libs_available&&s.creds_found)?'Or sign in with Google (read-only).':'Google sign-in is not set up; the App Password above works without it.';
  }catch(e){document.getElementById('esStatus').textContent='Error';}
}
async function saveImap(){
  const btn=document.getElementById('imapSaveBtn');btn.disabled=true;btn.textContent='Checking…';
  try{
    await api('/api/email/imap',{method:'PUT',body:{user:getV('im_user'),password:getV('im_pass')}});
    setV('im_pass','');toast('Gmail connected');
  }catch(e){let m='Could not connect';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
  finally{btn.disabled=false;btn.textContent='Connect with App Password';await loadEmailStatus();}
}
async function removeImap(){
  if(!await askConfirm('The saved App Password will be deleted from this device.',{title:'Remove App Password?',ok:'Remove'}))return;
  await api('/api/email/imap',{method:'DELETE'});await loadEmailStatus();toast('App Password removed');
}
function openEmailSyncInfo(){openSettings();}

/* ─── bank account statement sync ──────────────────────────── */
async function loadBankStmtSection(){
  const [holdings,cfg]=await Promise.all([api('/api/holdings'),api('/api/bank-statement/config')]);
  const opts=h=>`<option value="">— none —</option>`+holdings.map(x=>
    `<option value="${x.id}" ${x.id===h?'selected':''}>${esc(x.name)} (${esc(x.platform||x.asset_class)})</option>`).join('');
  document.getElementById('bs_current_holding').innerHTML=opts(cfg.current_holding_id);
  document.getElementById('bs_savings_holding').innerHTML=opts(cfg.savings_holding_id);
  setV('bs_sender',cfg.sender);setV('bs_subject',cfg.subject);
  document.getElementById('bs_pdf_password').placeholder=cfg.pdf_password_set?'Password saved (leave blank to keep)':'Leave blank to reuse a card\'s password';
  document.getElementById('bsLastSync').textContent=cfg.last_sync?cfg.last_sync.slice(0,16).replace('T',' '):'Never';
  document.getElementById('bsLastCurrent').textContent=cfg.last_current!=null?`${CUR} ${fmt2(cfg.last_current)}`:'—';
  document.getElementById('bsLastSavings').textContent=cfg.last_savings!=null?`${CUR} ${fmt2(cfg.last_savings)}`:'—';
}
async function saveBankStmtConfig(){
  const body={sender:getV('bs_sender'),subject:getV('bs_subject'),
    current_holding_id:getV('bs_current_holding')||null,savings_holding_id:getV('bs_savings_holding')||null};
  const pw=getV('bs_pdf_password');if(pw)body.pdf_password=pw;
  try{await api('/api/bank-statement/config',{method:'PUT',body});setV('bs_pdf_password','');toast('Mapping saved');await loadBankStmtSection();}
  catch{toast('Failed','err');}
}
async function syncBankStatement(){
  const btn=document.getElementById('bsSyncBtn');btn.disabled=true;btn.textContent='Syncing…';
  try{
    const r=await api('/api/bank-statement/sync',{method:'POST'});
    if(r.ok){toast(`Synced — Current ${r.current!=null?fmt2(r.current):'—'}, Savings ${r.savings!=null?fmt2(r.savings):'—'}`);summary=null;await loadBankStmtSection();}
    else toast(r.error||'Sync failed','err');
  }catch{toast('Sync failed','err');}
  finally{btn.disabled=false;btn.textContent='Sync now';}
}

/* ─── settings ──────────────────────────────────────────── */
async function loadAiUsage(){
  const box=document.getElementById('aiUsageBox');if(!box)return;
  try{const u=await api('/api/ai/usage?days=30'),t=u.total;
    box.innerHTML=u.features.length?`<div class="table-wrap"><table class="pl-t"><thead><tr><th>Feature</th><th>AI calls</th><th>Tokens (in / out)</th><th>Cheap decisions</th><th>Answered from cache</th></tr></thead><tbody>
      ${u.features.map(f=>`<tr><td>${esc(f.feature)}</td><td>${f.calls}</td><td>${fmt(f.tokens_in)} / ${fmt(f.tokens_out)}</td><td>${f.decisions}</td><td>${f.cache_hits}</td></tr>`).join('')}
      <tr><td><b>Total</b></td><td><b>${t.calls}</b></td><td><b>${fmt(t.tokens_in)} / ${fmt(t.tokens_out)}</b></td><td><b>${t.decisions}</b></td><td><b>${t.cache_hits}</b></td></tr></tbody></table></div>
      <div class="fine" style="margin-top:6px">Analyses are only rewritten when your numbers change, web research is reused for hours, and yes/no style choices go to the cheap Decisions classifier.</div>`:'No AI calls recorded yet.';}
  catch(e){box.textContent='Usage is unavailable.';}
}
async function openSettings(){
  loadAiUsage();if(window.loadSecurity)loadSecurity();
  const [fx,s]=await Promise.all([api('/api/fx'),api('/api/settings')]);
  const list=document.getElementById('fxList');list.innerHTML='';
  Object.entries(fx).forEach(([ccy,rate])=>{
    if(ccy===CUR)return;
    const row=document.createElement('div');row.className='fx-row';
    row.innerHTML=`<span class="fx-ccy">${ccy}</span><input type="number" step="0.0001" value="${rate}" data-ccy="${ccy}"><span class="fx-unit">${CUR} per 1 ${ccy}</span>`;
    list.appendChild(row);
  });
  setV('s_title',s.app_title||'');setV('s_income',s.monthly_income||'');setV('s_currency',CUR);setV('s_payday',s.pay_day||'');setV('s_dateorder',s.date_order||'dmy');setV('s_home',s.home_country||'');setV('s_tz',s.timezone||'');setV('s_spendwords',s.payslip_spend_words||'');fillPrefLists();loadBankSources();setV('s_myname',s.my_name||'');setV('s_family',s.family_names||'');
  document.getElementById('s_income_lbl').textContent='Monthly income ('+CUR+')';document.getElementById('fxUnitNote').textContent='('+CUR+' per 1 unit)';
  const curTheme=document.documentElement.getAttribute('data-theme')||'dark';
  document.querySelectorAll('#themeToggle .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.theme===curTheme));
  document.getElementById('autoSyncEnabled').checked=s.auto_sync_enabled==='1';
  setV('autoSyncInterval',s.auto_sync_interval_hours||12);
  document.getElementById('autoSyncLastRun').textContent=s.auto_sync_last_run
    ?'Last run: '+s.auto_sync_last_run.slice(0,16).replace('T',' '):'Never run yet';
  document.getElementById('billRemindersEnabled').checked=s.bill_reminders_enabled==='1';
  const note=document.getElementById('reminderNote');
  note.textContent=('Notification' in window)?`Browser notification permission: ${Notification.permission}`:'Notifications not supported in this browser';
  await loadEmailStatus();
  await loadBankStmtSection();
  await loadIbkrStatus();
  await loadEtoroStatus();
  await loadPayslipStatus();
  await loadFeeds();
  document.getElementById('settingsOverlay').classList.add('show');
}
/* compares the typed-in budget income with what the payslips actually show */
function incomeCheck(budget,inc){
  if(!inc||inc.source!=='payslip'||!budget||!budget.income)return '';
  const diffTypical=budget.income-inc.typical_net;
  const msg=`Payslips: ${fmtPeriod(inc.period)} net ${CUR} ${fmt(inc.net)}, gross ${CUR} ${fmt(inc.gross)}. Typical month ${CUR} ${fmt(inc.typical_net)}; with the annual bonus spread out ${CUR} ${fmt(inc.avg_net)}.`;
  const warn=diffTypical>2500?` Your budget assumes ${CUR} ${fmt(budget.income)}, about ${CUR} ${fmt(diffTypical)} more than a typical month, so non-bonus months will run tighter than this budget shows.`:'';
  return `<div class="fine">${msg}${warn}</div>`;
}
/* ─── Income (payslips) ─────────────────────────────────────── */
const fmtPeriod=p=>p?`${MONTHS_SHORT[+p.slice(5,7)-1]} ${p.slice(0,4)}`:'';
async function renderIncome(){
  const el=document.getElementById('page-income');
  const d=await api('/api/payslips').catch(()=>null);
  if(currentPage!=='income')return;
  const head=`<div class="page-head"><div><div class="page-title">Income</div><div class="page-sub">Read automatically from your payslips</div></div>
    <div style="display:flex;gap:10px"><button class="btn ghost sm" onclick="openSettings()">Settings</button>
    <button class="btn solid sm" id="scanPayBtn" onclick="scanPayslips()">Scan payslips</button></div></div>`;
  if(!d||!d.count){
    el.innerHTML=head+`<div class="chart-card"><div class="empty" style="padding:44px 20px">
      ${d&&!d.password_set?'Add your payslip PDF password in <b>Settings</b>, then scan.':'No payslips imported yet. Scan your Gmail for them.'}
      ${d&&d.last_error?`<div class="fine" style="color:var(--neg);margin-top:10px">${esc(d.last_error)}</div>`:''}
      <div style="margin-top:16px"><button class="btn solid" onclick="${d&&!d.password_set?'openSettings()':'scanPayslips()'}">${d&&!d.password_set?'Open Settings':'Scan payslips'}</button></div>
      <div class="fine" style="margin-top:14px">Looks for emails titled "Pay Advice summary" or "Payslips" with the PDF attached, including forwarded bundles.</div></div></div>`;
    return;
  }
  const L=d.latest,rows=d.payslips,last12=rows.slice(-12);
  const g12=last12.reduce((a,r)=>a+r.gross,0),takeHome=g12?last12.reduce((a,r)=>a+r.net,0)/g12*100:0;      // 12 months, not the best month
  const review=rows.filter(r=>!r.ok);
  el.innerHTML=head+`
    <div class="kpis">
      <div class="kpi"><div class="kpi-l">Latest net pay · ${fmtPeriod(L.period)}</div><div class="kpi-v">${fmt(L.net)}<small>${esc(L.currency)}</small></div>
        <div class="kpi-m">Gross ${fmt(L.gross)}, deductions ${fmt(L.deductions)}.</div></div>
      <div class="kpi"><div class="kpi-l">Typical net pay</div><div class="kpi-v">${fmt(d.typical_net_3)}<small>${esc(L.currency)}</small></div>
        <div class="kpi-m">Median of the last ${Math.min(3,d.count)} payslips, so a bonus month doesn't skew it.</div></div>
      <div class="kpi"><div class="kpi-l">Take-home rate</div><div class="kpi-v">${takeHome.toFixed(0)}<small>%</small></div>
        <div class="kpi-m">Share of gross pay that reaches your account over the last ${last12.length} payslips.</div></div>
    </div>
    ${review.length?`<div class="alert-bar" style="margin-top:16px"><span class="alert-icon"><svg class="i"><use href="#i-alert"/></svg></span><div class="alert-text">
      <strong>${review.length} payslip${review.length>1?'s':''} to check.</strong> The rows didn't add up to the printed totals, so the numbers may be off.
      ${review.slice(0,6).map(r=>`<span class="due-chip urgent" onclick="openPayslip(${r.id})">${fmtPeriod(r.period)}</span>`).join('')}</div></div>`:''}
    <div class="chart-grid" style="margin-top:20px"><div class="chart-card wide"><div class="panel-head"><h3>Where each month's pay went <span class="sub-dim">gross pay split into take-home and deductions</span></h3></div>
      <div id="payChart" class="chart-mount"></div></div></div>
    <div class="two-col">
      <div class="chart-card"><h3>Deductions <span class="sub-dim">last ${d.months_12} payslip${d.months_12>1?'s':''}, total per item</span></h3><div id="dedRank"></div>
        <div class="fine">Orange marks deductions that count as spending (Settings > Payslips).</div></div>
      <div class="chart-card"><h3>Latest pay, by component <span class="sub-dim">${fmtPeriod(L.period)}</span></h3><div id="earnBar"></div></div>
    </div>
    <div class="sec"><h2>All payslips</h2><div style="display:flex;gap:10px;align-items:center"><p>${d.count} found${d.last_scan?` · scanned ${timeAgo(d.last_scan+'Z')}`:''}</p>
      <button class="btn ghost sm" onclick="applyPayslipSalary()">Make budget salary follow my payslips</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>Month</th><th class="num">Gross</th><th class="num">Deductions</th><th class="num">Net pay</th><th>Check</th></tr></thead><tbody>
      ${rows.slice().reverse().map(r=>`<tr onclick="openPayslip(${r.id})"><td><b>${fmtPeriod(r.period)}</b></td><td class="num">${fmt(r.gross)}</td><td class="num">${fmt(r.deductions)}</td><td class="num"><b>${fmt(r.net)}</b></td>
        <td>${r.ok?'<span class="status-pill paid">Adds up</span>':'<span class="status-pill partial">Check</span>'}</td></tr>`).join('')}
    </tbody></table></div>`;
  MiniChart.stackedColumns(document.getElementById('payChart'),rows.slice(-24).map(r=>fmtMonth(r.period)),
    [{name:'Take-home',data:rows.slice(-24).map(r=>r.net)},{name:'Deductions',data:rows.slice(-24).map(r=>r.deductions)}]);
  MiniChart.rankBars(document.getElementById('dedRank'),d.deductions_12.map(x=>({label:x.label,value:x.total,color:x.spend?'var(--warn)':null})),{limit:8,showPct:true,format:v=>fmtK(v)});
  MiniChart.stackBar(document.getElementById('earnBar'),d.latest_items.filter(i=>i.kind==='earning').map(i=>({label:i.label,value:i.amount})),{max:6,format:v=>fmt(v)});
}
async function scanPayslips(){
  const btn=document.getElementById('scanPayBtn'),sbtn=document.getElementById('psScanBtn');
  [btn,sbtn].forEach(b=>{if(b){b.disabled=true;b.dataset.t=b.textContent;b.textContent='Scanning…';}});
  toast('Scanning Gmail for payslips. This can take a minute.');
  try{
    const r=await api('/api/payslips/scan',{method:'POST'});
    toast(r.ok?`Imported ${r.imported} payslip${r.imported===1?'':'s'}${r.needs_review?`, ${r.needs_review} to check`:''}${r.locked?`, ${r.locked} locked`:''}`:r.error,r.ok?'ok':'err');
  }catch(e){let m='Scan failed';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
  finally{[btn,sbtn].forEach(b=>{if(b){b.disabled=false;b.textContent=b.dataset.t;}});
    if(currentPage==='income')renderIncome();if(document.getElementById('settingsOverlay').classList.contains('show'))loadPayslipStatus();}
}
async function openPayslip(id){
  const p=await api('/api/payslips/'+id);
  const side=k=>p.items.filter(i=>i.kind===k).map(i=>`<div class="budget-row"><span class="br-label">${esc(i.label)}</span><span class="br-amount">${fmt2(i.amount)}</span></div>`).join('')||'<div class="sub-dim">None found</div>';
  document.getElementById('stmtDetailTitle').textContent=`Payslip · ${fmtPeriod(p.period)}`;
  document.getElementById('stmtDetailBody').innerHTML=`
    ${p.ok?'':`<div class="alert-bar"><span class="alert-icon"><svg class="i"><use href="#i-alert"/></svg></span><div class="alert-text">${esc(p.notes)}. Compare with the original PDF.</div></div>`}
    <div class="form-grid" style="margin-bottom:6px">
      <div><div class="fine" style="margin:0">Payments</div>${side('earning')}<div class="budget-row"><span class="br-label"><b>Total</b></span><span class="br-amount">${fmt2(p.gross)}</span></div></div>
      <div><div class="fine" style="margin:0">Deductions</div>${side('deduction')}<div class="budget-row"><span class="br-label"><b>Total</b></span><span class="br-amount">${fmt2(p.deductions)}</span></div></div>
    </div>
    <div class="budget-row" style="border-top:2px solid var(--line2)"><span class="br-label"><b>Net pay</b></span><span class="br-amount">${esc(p.currency)} ${fmt2(p.net)}</span></div>
    <details class="more"><summary>Show what was read from the PDF</summary><pre style="white-space:pre-wrap;font-size:12px;color:var(--ink-dim);max-height:260px;overflow:auto">${esc(p.raw_text)}</pre></details>`;
  document.getElementById('stmtDetailOverlay').classList.add('show');
}
async function applyPayslipSalary(){
  const b=await api('/api/budget');const item=b.items.find(i=>i.type==='income');
  if(!item){toast('No income line in Budget yet','err');return;}
  if(!await askConfirm(`"${item.label}" will follow your payslips: the typical (median) net pay of the last 12, so bonus months do not inflate the budget.`,{title:'Follow the payslips?',ok:'Update',danger:false}))return;
  await api('/api/budget/'+item.id,{method:'PUT',body:{source:'payslip_typical'}});toast('Budget salary now follows your payslips');
}

/* ─── Settings: payslips + emailed balances ─────────────────── */
async function loadPayslipStatus(){
  const d=await api('/api/payslips').catch(()=>null);if(!d)return;
  document.getElementById('psCount').textContent=d.count?`${d.count} found${d.needs_review?`, ${d.needs_review} to check`:''}`:'None yet';
  document.getElementById('psLast').textContent=d.last_scan?timeAgo(d.last_scan+'Z'):'Never';
  document.getElementById('ps_pw').placeholder=d.password_set?'Password saved. Leave blank to keep it':'PDF password';
  document.getElementById('psNote').textContent=d.last_error||'Payslips are scanned automatically every 12 hours once Gmail is connected.';
}
async function savePayslipPw(){
  const pw=getV('ps_pw');if(!pw){toast('Enter the PDF password','err');return;}
  await api('/api/payslips/config',{method:'PUT',body:{password:pw}});setV('ps_pw','');toast('Password saved');await loadPayslipStatus();
}
const emailAddr=s=>(String(s).match(/<([^>]+)>/)||[0,String(s).trim()])[1];
async function loadFeeds(){
  const feeds=await api('/api/feeds').catch(()=>[]);
  document.getElementById('feedList').innerHTML=feeds.length?feeds.map(f=>`<div class="fx-row" style="flex-wrap:wrap">
    <div style="flex:1;min-width:200px"><b>${esc(f.holding_name||f.name||'Account')}</b><div class="sub-dim">${esc(f.sender)}${f.subject?' · '+esc(f.subject):''}</div>
      <div class="fine" style="margin:2px 0 0;${f.last_error?'color:var(--neg)':''}">${f.last_error?esc(f.last_error):f.last_value!=null?`Last balance ${esc(f.currency||'')} ${fmt2(f.last_value)} · ${timeAgo(f.last_sync+'Z')}`:'Not synced yet'}</div></div>
    <button class="btn ghost sm" onclick="feedAction(${f.id},'test')">Test</button>
    <button class="btn ghost sm" onclick="feedAction(${f.id},'sync')">Sync</button>
    <button class="btn ghost sm" onclick="removeFeed(${f.id})" aria-label="Remove"><svg class="i" style="width:14px;height:14px"><use href="#i-close"/></svg></button></div>`).join('')
    :'<div class="es-note">No accounts yet. Add one to keep its balance current from the bank\'s statement email.</div>';
}
async function addFeedDialog(pre={}){
  const hs=(await api('/api/holdings')).filter(h=>!h.is_liability);
  const opts=hs.map(h=>`${h.name} (${h.currency}) #${h.id}`);
  const r=await dialog({title:'Add account balance feed',ok:'Add',text:'The newest email from this sender is read, and the amount after the label becomes the account balance.',fields:[
    {key:'holding',label:'Account to update',options:opts,value:opts[0]},
    {key:'sender',label:'Sender email',value:pre.sender||'',type:'email'},
    {key:'subject',label:'Subject contains (optional)',value:pre.subject||''},
    {key:'label',label:'Balance label (optional, e.g. Closing Balance)',value:pre.label||''},
    {key:'pdf_password',label:'PDF password (if the statement is locked)',type:'password'}]});
  if(!r)return;
  const hid=+r.holding.split('#').pop();
  try{await api('/api/feeds',{method:'POST',body:{holding_id:hid,sender:r.sender,subject:r.subject,label:r.label,pdf_password:r.pdf_password,name:r.holding}});
    toast('Feed added. Press Test to check it.');await loadFeeds();
  }catch(e){let m='Could not add';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}
async function feedAction(id,act){
  toast(act==='test'?'Reading the latest statement…':'Syncing…');
  try{
    const r=await api(`/api/feeds/${id}/${act}`,{method:'POST'});
    if(r.ok)toast(act==='test'?`Found balance ${fmt2(r.value)}: "${(r.snippet||'').slice(0,60)}"`:`Balance updated to ${fmt2(r.value)}`);
    else{toast(r.error,'err');if(r.balance_lines&&r.balance_lines.length)await dialog({title:'Lines mentioning "balance"',text:r.balance_lines.join('  |  '),ok:'OK'});}
  }catch(e){let m='Failed';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
  await loadFeeds();if(act==='sync')summary=null;
}
async function removeFeed(id){
  if(!await askConfirm('The account keeps its current balance but will stop updating automatically.',{title:'Remove this feed?',ok:'Remove'}))return;
  await api('/api/feeds/'+id,{method:'DELETE'});await loadFeeds();
}
async function discoverFeed(){
  const r=await dialog({title:'Find statement emails',text:'Search your mailbox for the bank name or the word "statement".',ok:'Search',fields:[{key:'q',label:'Search for',value:'statement'}]});
  if(!r||!r.q.trim())return;
  try{
    const d=await api('/api/feeds/discover?q='+encodeURIComponent(r.q));
    window._hits=d.hits;
    document.getElementById('stmtDetailTitle').textContent='Emails found';
    document.getElementById('stmtDetailBody').innerHTML=d.hits.length?`<table class="stmt-table"><thead><tr><th>From</th><th>Subject</th><th></th></tr></thead><tbody>${d.hits.map((h,i)=>
      `<tr><td>${esc(h.from)}</td><td>${esc(h.subject)}</td><td><button class="btn ghost sm" onclick="useHit(${i})">Use</button></td></tr>`).join('')}</tbody></table>`
      :'<div class="empty">No emails matched. Try the bank name or a word from the subject.</div>';
    document.getElementById('stmtDetailOverlay').classList.add('show');
  }catch(e){let m='Search failed';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}
function useHit(i){const h=window._hits[i];closeStmtDetail();addFeedDialog({sender:emailAddr(h.from),subject:h.subject.replace(/[0-9]{2,}.*$/,'').trim().slice(0,40)});}

/* ─── eToro sync ────────────────────────────────────────────── */
async function loadEtoroStatus(){
  const st=await api('/api/etoro/status').catch(()=>null);if(!st)return;
  const i=st.info||{};
  document.getElementById('etStatus').textContent=st.last_error?'Needs attention':st.configured?(st.last_sync?'Connected':'Saved, not synced yet'):'Not connected';
  document.getElementById('etStatus').style.color=st.last_error?'var(--neg)':st.configured?'var(--pos)':'';
  document.getElementById('etLastSync').textContent=st.last_sync?timeAgo(st.last_sync+'Z'):'—';
  document.getElementById('etPositions').textContent=i.positions!=null?`${i.positions}${i.skipped_leveraged_or_short?` (${i.skipped_leveraged_or_short} skipped)`:''}`:'—';
  setV('et_api','');setV('et_user','');document.getElementById('et_cfd').checked=!!st.include_cfd;
  document.getElementById('et_api').placeholder=st.configured?'Saved. Leave blank to keep':'Paste once; stored locally';
  document.getElementById('et_user').placeholder=st.configured?'Saved. Leave blank to keep':'Paste once; stored locally';
  document.getElementById('etDisconnectBtn').style.display=st.configured?'':'none';
  const rb=document.getElementById('etRestoreBtn');rb.style.display=(st.ignored&&st.ignored.length)?'':'none';rb.textContent=`Restore deleted holdings (${(st.ignored||[]).length})`;
  const cf=(i.cfds||[]);
  document.getElementById('etNote').textContent=st.last_error?'Last sync failed: '+st.last_error
    :cf.length?`${st.include_cfd?'Included':'Not included'}: ${cf.map(c=>`${c.symbol} ${c.short?'short':c.leverage+'x'} (equity about $${fmt(c.equity)})`).join(', ')}. Syncs every 6 hours.`
    :'Syncs every 6 hours. Leveraged, short and copy-trading positions are skipped unless you tick the box above.';
}
async function saveEtoro(){
  const btn=document.getElementById('etSaveBtn');btn.disabled=true;
  try{await api('/api/etoro/config',{method:'PUT',body:{api_key:getV('et_api'),user_key:getV('et_user'),include_cfd:document.getElementById('et_cfd').checked}});await syncEtoro();}
  catch{toast('Could not save eToro keys','err');}
  finally{btn.disabled=false;}
}
async function syncEtoro(){
  const btn=document.getElementById('etSyncBtn');btn.disabled=true;btn.textContent='Syncing…';
  try{
    const r=await api('/api/etoro/sync',{method:'POST'});
    toast(`eToro synced: ${r.created} new, ${r.updated} updated${r.archived?`, ${r.archived} closed`:''}`);summary=null;
  }catch(e){let m='eToro sync failed';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
  finally{btn.disabled=false;btn.textContent='Sync now';await loadEtoroStatus();}
}
async function disconnectEtoro(){
  if(!await askConfirm('Holdings already imported stay in your portfolio.',{title:'Disconnect eToro?',ok:'Disconnect'}))return;
  await api('/api/etoro/config',{method:'DELETE'});await loadEtoroStatus();toast('eToro disconnected');
}

/* ─── Interactive Brokers sync ─────────────────────────────── */
async function loadIbkrStatus(){
  const st=await api('/api/ibkr/status').catch(()=>null);
  if(!st)return;
  const p=st.profile||{};
  document.getElementById('ibStatus').textContent=st.last_error?'Needs attention':st.configured?(st.last_sync?'Connected':'Saved, not synced yet'):'Not connected';
  document.getElementById('ibStatus').style.color=st.last_error?'var(--neg)':st.configured?'var(--pos)':'';
  document.getElementById('ibAccount').textContent=p.account_id?`${p.account_id}${p.name?' · '+p.name:''}${p.base_currency?' · '+p.base_currency:''}`:'—';
  document.getElementById('ibLastSync').textContent=st.last_sync?`${timeAgo(st.last_sync+'Z')}${p.positions!=null?' · '+p.positions+' positions':''}`:'—';
  setV('ib_query',st.query_id||'');setV('ib_interval',st.interval_hours||6);setV('ib_token','');
  document.getElementById('ib_token').placeholder=st.token_set?'Token saved — leave blank to keep it':'Paste once — stored locally, never shown again';
  document.getElementById('ibDisconnectBtn').style.display=st.configured?'':'none';
  const rb=document.getElementById('ibRestoreBtn');rb.style.display=(st.ignored&&st.ignored.length)?'':'none';
  rb.textContent=`Restore deleted holdings (${(st.ignored||[]).length})`;
  if(st.last_error)document.getElementById('ibNote').textContent='Last sync failed: '+st.last_error;
}
async function saveIbkr(){
  const btn=document.getElementById('ibSaveBtn');btn.disabled=true;
  try{
    await api('/api/ibkr/config',{method:'PUT',body:{token:getV('ib_token'),query_id:getV('ib_query'),interval_hours:getV('ib_interval')}});
    await syncIbkr(true);
  }catch{toast('Could not save IBKR settings','err');}
  finally{btn.disabled=false;}
}
async function syncIbkr(quiet){
  const btn=document.getElementById('ibSyncBtn');btn.disabled=true;btn.textContent='Syncing…';
  try{
    const r=await api('/api/ibkr/sync',{method:'POST'});
    toast(`IBKR synced: ${r.created} new, ${r.updated} updated${r.archived?`, ${r.archived} closed`:''}`);
    summary=null;
  }catch(e){
    let m='IBKR sync failed';try{m=JSON.parse(e.message).error||m;}catch{}
    toast(m,'err');
  }
  finally{btn.disabled=false;btn.textContent='Sync now';await loadIbkrStatus();}
}
async function disconnectIbkr(){
  if(!await askConfirm('Holdings already imported stay in your portfolio.',{title:'Disconnect Interactive Brokers?',ok:'Disconnect'}))return;
  await api('/api/ibkr/config',{method:'DELETE'});await loadIbkrStatus();toast('IBKR disconnected');
}
function fillPrefLists(){
  const tz=document.getElementById('tzList');if(tz&&!tz.children.length&&Intl.supportedValuesOf)tz.innerHTML=Intl.supportedValuesOf('timeZone').map(z=>`<option>${z}</option>`).join('');
  const hm=document.getElementById('homeList');if(hm&&!hm.children.length)api('/api/countries').then(l=>{hm.innerHTML=l.map(c=>`<option>${esc(c)}</option>`).join('');}).catch(()=>{});
}
let _bankSrc=[];
async function loadBankSources(){
  try{_bankSrc=(await api('/api/bank/sources')).sources;}catch{_bankSrc=[];}
  drawBankSources();
}
function drawBankSources(){
  const el=document.getElementById('bankSrcList');if(!el)return;
  el.innerHTML=_bankSrc.map((b,i)=>`<div class="form-grid" data-i="${i}" style="margin-bottom:8px"><label>Name<input data-f="name" value="${esc(b.name)}" placeholder="e.g. My current account"></label>
    <label>Sender address or domain<input data-f="sender" value="${esc(b.sender)}" placeholder="statements@yourbank.com"></label>
    <label>Subject contains<input data-f="subject" value="${esc(b.subject)}" placeholder="optional, e.g. Monthly statement"></label>
    <button class="btn ghost sm" type="button" data-rm="${i}">Remove</button></div>`).join('')||'<div class="fine">None yet.</div>';
  el.querySelectorAll('[data-rm]').forEach(b=>b.onclick=()=>{syncBankRows();_bankSrc.splice(+b.dataset.rm,1);drawBankSources();});
}
function syncBankRows(){document.querySelectorAll('#bankSrcList [data-i]').forEach(r=>{const o=_bankSrc[+r.dataset.i];r.querySelectorAll('[data-f]').forEach(i=>o[i.dataset.f]=i.value);});}
async function saveBankSources(){syncBankRows();await api('/api/bank/sources',{method:'PUT',body:{sources:_bankSrc}});}
document.addEventListener('click',e=>{if(e.target&&e.target.id==='bankSrcAdd'){syncBankRows();_bankSrc.push({name:'',sender:'',subject:''});drawBankSources();}});
function pickCsv(){return new Promise(res=>{const i=document.createElement('input');i.type='file';i.accept='.csv,text/csv,.txt';i.onchange=()=>{const f=i.files[0];if(!f)return res(null);const r=new FileReader();r.onload=()=>res(r.result);r.readAsText(f);};i.click();});}
async function importCardCsv(){
  const cards=await api('/api/cards').catch(()=>[]);if(!cards.length){toast('Add a card first','err');return;}
  const text=await pickCsv();if(!text)return;
  const r=await dialog({title:'Import a CSV file',text:'Which card is this file from? Purchases are recognised by the sign of the amounts.',ok:'Import',fields:[{key:'card',label:'Card',options:cards.map(c=>c.name),value:cards[0].name}]});
  if(!r)return;const card=cards.find(c=>c.name===r.card);
  try{const d=await api('/api/cards/'+card.id+'/import-csv',{method:'POST',body:{text}});toast(`Imported ${d.transactions} transactions in ${d.statements} statement${d.statements>1?'s':''}`);summary=null;go(currentPage);}
  catch(e){let m='Could not read that file';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}
function closeSettings(){document.getElementById('settingsOverlay').classList.remove('show');}
async function saveSettings(){
  for(const inp of document.querySelectorAll('#fxList input'))
    await api('/api/fx',{method:'PUT',body:{currency:inp.dataset.ccy,rate:parseFloat(inp.value)}});
  const newCur=getV('s_currency')||CUR;
  if(newCur!==CUR&&!await askConfirm('Amounts already entered are not converted: they keep their numbers and are shown in '+newCur+'. Change currency only to set up a fresh install, or if every figure is already in '+newCur+'.',{title:'Switch to '+newCur+'?',ok:'Switch'}))return;
  await saveBankSources();
  const r=await api('/api/settings',{method:'PUT',body:{monthly_income:getV('s_income'),app_title:getV('s_title'),base_currency:newCur,my_name:getV('s_myname'),family_names:getV('s_family'),pay_day:getV('s_payday'),date_order:getV('s_dateorder'),home_country:getV('s_home'),timezone:getV('s_tz'),payslip_spend_words:getV('s_spendwords')}});
  if(r&&r.currency_changed){location.reload();return;}
  closeSettings();summary=null;
  const s=await api('/api/settings');appSettings=s;document.getElementById('appTitle').textContent=s.app_title||'Aurum';
  go(currentPage);toast('Settings saved');
}

/* ─── bill reminders (browser notifications, while the app is open) ─── */
function checkBillReminders(dues){
  if(appSettings.bill_reminders_enabled!=='1')return;
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  const key='remindersNotified_'+today();
  let notified=[];
  try{notified=JSON.parse(localStorage.getItem(key)||'[]');}catch(e){}
  const toNotify=(dues||[]).filter(d=>(d.urgency==='overdue'||d.urgency==='urgent')&&!notified.includes(d.id));
  if(!toNotify.length)return;
  toNotify.forEach(d=>{
    try{
      new Notification(`${d.card_name} payment ${d.urgency==='overdue'?'overdue':'due soon'}`,{
        body:`${CUR} ${fmt(d.total_due)} ${d.urgency==='overdue'?'was due '+Math.abs(d.days_until_due)+'d ago':'due in '+d.days_until_due+'d'}`,
        tag:'due-'+d.id,
      });
    }catch(e){}
  });
  try{localStorage.setItem(key,JSON.stringify(notified.concat(toNotify.map(d=>d.id))));}catch(e){}
}

/* ─── market ────────────────────────────────────────────── */
let _marketSel=null,_marketSt;
async function renderWatchTab(){
  const watch=await api('/api/watchlist');
  const el=document.getElementById('marketWatchHost');
  if(!el)return;
  el.innerHTML=`
    <div class="search-zone" style="margin:0 0 18px">
      <label class="search-label">Search a ticker or company</label>
      <div class="search-input-wrap">
        <input id="marketSearchInput" type="text" placeholder="e.g. AAPL, VTI, Tesla…" autocomplete="off">
        <span class="search-spin" id="marketSearchSpin"></span>
      </div>
      <div class="search-results" id="marketSearchResults"></div>
    </div>
    <div id="marketQuoteArea"></div>
    <div id="mkSignals"></div>
    <details class="more" style="margin-top:16px"><summary>Quotes table</summary><div id="marketWatchlist"></div></details>`;
  renderWatchlist(watch);
  document.getElementById('marketSearchInput').oninput=onMarketSearch;
  if(window.drawSignals)drawSignals();
}

function renderWatchlist(watch){
  const box=document.getElementById('marketWatchlist');
  if(!box)return;
  box.innerHTML=watch.length?`<div class="table-wrap"><table>
    <thead><tr><th>Symbol</th><th>Name</th><th class="num">Price</th><th></th></tr></thead>
    <tbody>${watch.map(w=>`<tr onclick="selectMarketSymbol('${ejs(w.symbol)}','${ejs(w.name||'')}')">
      <td><span class="sym" style="font-size:13px">${esc(w.symbol)}</span></td>
      <td>${esc(w.name||'—')}</td>
      <td class="num">${w.price!=null?fmt2(w.price)+' '+esc(w.currency||''):'—'}</td>
      <td class="num"><button class="btn ghost sm" onclick="event.stopPropagation();removeWatch('${ejs(w.symbol)}')">Remove</button></td>
    </tr>`).join('')}</tbody></table></div>`
    :`<div class="table-wrap"><div class="empty" style="padding:30px"><div class="big" style="font-size:22px"></div>No symbols watched yet. Search above and add some.</div></div>`;
}

function onMarketSearch(){
  const q=document.getElementById('marketSearchInput').value.trim();
  clearTimeout(_marketSt);
  if(q.length<2){document.getElementById('marketSearchResults').innerHTML='';return;}
  const spin=document.getElementById('marketSearchSpin');spin.classList.add('on');
  _marketSt=setTimeout(async()=>{
    try{
      const res=await api(`/api/market/search?q=${encodeURIComponent(q)}`);
      const box=document.getElementById('marketSearchResults');
      box.innerHTML=res.length?res.slice(0,10).map(r=>`<div class="sr" onclick="selectMarketSymbol('${ejs(r.symbol)}','${ejs(r.name)}')">
        <span class="sr-sym">${esc(r.symbol)}</span><span class="sr-name">${esc(r.name)}</span><span class="sr-ex">${esc(r.exchange||'')}</span>
      </div>`).join(''):'<div class="sr"><span class="sr-name sub-dim">No matches.</span></div>';
    }catch{document.getElementById('marketSearchResults').innerHTML='';}
    finally{spin.classList.remove('on');}
  },350);
}

async function selectMarketSymbol(symbol,name){
  _marketSel={symbol,name};
  document.getElementById('marketSearchResults').innerHTML='';
  document.getElementById('marketSearchInput').value='';
  const area=document.getElementById('marketQuoteArea');
  area.innerHTML=`<div class="chart-card wide"><div class="empty" style="padding:20px"><span class="search-spin on" style="position:static"></span> Loading ${esc(symbol)}…</div></div>`;
  const [q,hist]=await Promise.all([
    api(`/api/market/quote/${encodeURIComponent(symbol)}`).catch(()=>null),
    api(`/api/market/history/${encodeURIComponent(symbol)}?range=3mo`).catch(()=>null)]);
  if(!q){area.innerHTML=`<div class="chart-card wide"><div class="empty">Could not load a quote for ${esc(symbol)}.</div></div>`;return;}
  const isWatched=(await api('/api/watchlist')).some(w=>w.symbol===symbol.toUpperCase());
  area.innerHTML=`<div class="chart-card wide">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;margin-bottom:8px">
      <div><div style="font-family:var(--display);font-size:20px;font-weight:600">${esc(q.name||symbol)}</div>
        <div class="sub-dim">${esc(symbol.toUpperCase())}</div></div>
      <div style="text-align:right">
        <div style="font-family:var(--display);font-size:26px;font-weight:600">${q.price!=null?fmt2(q.price):'—'} <span style="font-size:13px;color:var(--ink-faint)">${esc(q.currency||'')}</span></div>
        <div style="display:flex;gap:8px;margin-top:6px;justify-content:flex-end">
          <button class="btn ${isWatched?'ghost':'solid'} sm" id="watchToggleBtn">${isWatched?'Watching':'Watch'}</button>
          <button class="btn ghost sm" onclick="openAddHolding('stock');setTimeout(()=>{setV('f_name','${ejs(q.name||symbol)}');setV('f_symbol','${ejs(symbol.toUpperCase())}');setV('f_currency','${ejs(q.currency||'USD')}');setV('f_price_mode','ticker');setV('f_last_price',${q.price||0});},50)">+ Add to Portfolio</button>
        </div>
      </div>
    </div>
    <div id="marketChart" class="chart-mount"></div>
  </div>`;
  document.getElementById('watchToggleBtn').onclick=async()=>{
    if(isWatched)await removeWatch(symbol);else await addWatch(symbol,q.name||symbol);
    selectMarketSymbol(symbol,q.name||symbol);
  };
  if(hist&&hist.points&&hist.points.length){
    const chartEl=document.getElementById('marketChart');
    chartEl.classList.add('loading');
    await MiniChart.line(chartEl,hist.points.map(p=>({snap_date:p.date,total:p.close})));
    chartEl.classList.remove('loading');
  }
}

async function addWatch(symbol,name){
  await api('/api/watchlist',{method:'POST',body:{symbol,name}});
  toast('Added to watchlist');renderWatchlist(await api('/api/watchlist'));if(window.drawSignals)drawSignals();
}
async function removeWatch(symbol){
  await api('/api/watchlist/'+encodeURIComponent(symbol),{method:'DELETE'});
  toast('Removed from watchlist');renderWatchlist(await api('/api/watchlist'));if(window.drawSignals)drawSignals();
  if(document.getElementById('watchToggleBtn'))document.getElementById('marketQuoteArea').innerHTML='';
}

/* ─── AI assistant (global) ────────────────────────────────── */
let _assistantLoaded=false;
function openAssistant(){
  document.getElementById('assistantPanel').classList.add('show');
  document.getElementById('assistantFab').classList.add('hide');
  if(!_assistantLoaded){_assistantLoaded=true;loadAssistantHistory();}
  setTimeout(()=>document.getElementById('assistantInput').focus(),200);
}
function closeAssistant(){
  document.getElementById('assistantPanel').classList.remove('show');
  document.getElementById('assistantFab').classList.remove('hide');
}
async function loadAssistantHistory(){
  try{
    const hist=await api('/api/assistant/history');
    if(hist.length){
      const body=document.getElementById('assistantBody');
      body.innerHTML=hist.map(m=>assistantBubble(m.role,m.content)).join('');
      body.scrollTop=body.scrollHeight;
      document.getElementById('assistantSuggest').style.display='none';
    }
  }catch{}
}
function mdLite(s){
  return s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/^-\s+/gm,'• ');
}
function assistantBubble(role,text){
  return `<div class="assistant-msg assistant-msg-${role==='user'?'user':'bot'}">${mdLite(esc(text)).replace(/\n/g,'<br>')}</div>`;
}
async function sendAssistantMessage(text){
  text=(text||document.getElementById('assistantInput').value).trim();
  if(!text)return;
  const body=document.getElementById('assistantBody');
  document.getElementById('assistantSuggest').style.display='none';
  body.insertAdjacentHTML('beforeend',assistantBubble('user',text));
  document.getElementById('assistantInput').value='';
  const typingId='typing-'+Date.now();
  body.insertAdjacentHTML('beforeend',`<div class="assistant-msg assistant-msg-bot" id="${typingId}"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>`);
  body.scrollTop=body.scrollHeight;
  try{
    const r=await api('/api/assistant/chat',{method:'POST',body:{message:text}});
    document.getElementById(typingId)?.remove();
    if(r.ok)body.insertAdjacentHTML('beforeend',assistantBubble('assistant',r.reply));
    else body.insertAdjacentHTML('beforeend',assistantBubble('assistant',''+(r.error||'Something went wrong.')));
  }catch{
    document.getElementById(typingId)?.remove();
    body.insertAdjacentHTML('beforeend',assistantBubble('assistant','Could not reach the assistant.'));
  }
  body.scrollTop=body.scrollHeight;
}
async function clearAssistant(){
  if(!await askConfirm('The chat history will be cleared.',{title:'Clear conversation?',ok:'Clear'}))return;
  await api('/api/assistant/clear',{method:'POST'});
  document.getElementById('assistantBody').innerHTML=assistantBubble('assistant','Conversation cleared. Ask me anything about your finances.');
  document.getElementById('assistantSuggest').style.display='';
}

/* ─── actions ───────────────────────────────────────────── */
async function syncPrices(){
  const note=document.getElementById('syncNote');note.textContent='Syncing…';
  try{
    const r=await api('/api/refresh',{method:'POST'});
    summary=null;go(currentPage);
    note.textContent=r.live?`Updated ${r.prices?.length||0} live · ${new Date().toLocaleTimeString()}`:'Offline — kept values';
    if(r.ibkr&&r.ibkr.ok===false)toast('IBKR: '+r.ibkr.error,'err');
    else toast(r.live?`Synced ${r.prices?.length||0} prices${r.ibkr?' + IBKR':''}`:'Offline — using stored values',r.live?'ok':'err');
  }catch{note.textContent='Sync failed';toast('Sync unavailable','err');}
}
async function takeSnapshot(){await api('/api/snapshot',{method:'POST'});if(currentPage==='dashboard')renderDashboard();toast('Snapshot saved');}

/* ─── command palette (global search, Ctrl+K) ──────────────── */
let _paletteIndex=[],_paletteFiltered=[],_paletteSelected=0;

const PALETTE_ACTIONS=[
  {type:'page',icon:'⌂',title:'Overview',sub:'Go to page',action:()=>go('dashboard')},
  {type:'page',icon:'▤',title:'Portfolio',sub:'Go to page',action:()=>go('all')},
  {type:'page',icon:'▭',title:'Cards',sub:'Go to page',action:()=>go('cards')},
  {type:'page',icon:'✦',title:'Assistant',sub:'Go to page',action:()=>go('assistant')},
  {type:'page',icon:'$',title:'Income',sub:'Go to page',action:()=>go('income')},
  {type:'page',icon:'≡',title:'Budget',sub:'Go to page',action:()=>go('budget')},
  {type:'page',icon:'↗',title:'Market',sub:'Go to page',action:()=>go('market')},
  {type:'action',icon:'+',title:'Add Holding',sub:'Quick action',action:()=>{go('all');setTimeout(()=>openAddHolding('stock'),80);}},
  {type:'action',icon:'+',title:'Add Card',sub:'Quick action',action:()=>{go('cards');setTimeout(()=>openAddCard(),80);}},
  {type:'action',icon:'●',title:'Save snapshot',sub:'Quick action',action:()=>takeSnapshot()},
  {type:'action',icon:'↻',title:'Sync prices',sub:'Quick action',action:()=>syncPrices()},
  {type:'action',icon:'⚙',title:'Settings',sub:'Quick action',action:()=>openSettings()},
  {type:'action',icon:'✦',title:'Ask Assistant',sub:'Quick action',action:()=>openAssistant()},
];

async function openPalette(){
  document.getElementById('paletteOverlay').classList.add('show');
  const input=document.getElementById('paletteInput');
  input.value='';
  setTimeout(()=>input.focus(),10);
  renderPaletteResults('');
  await buildPaletteIndex();
  if(document.getElementById('paletteOverlay').classList.contains('show'))renderPaletteResults(input.value);
}
function closePalette(){document.getElementById('paletteOverlay').classList.remove('show');}

async function buildPaletteIndex(){
  try{
    const[holdings,cards,budget]=await Promise.all([api('/api/holdings'),api('/api/cards'),api('/api/budget')]);
    _paletteIndex=[
      ...PALETTE_ACTIONS,
      ...holdings.map(h=>({type:'holding',icon:'▤',title:h.symbol?`${h.name} · ${h.symbol}`:h.name,sub:`${CLASS_LABELS[h.asset_class]||h.asset_class} · ${h.platform||'—'}`,val:`${CUR} ${fmt(h.value_base)}`,
        action:()=>{go(h.asset_class);setTimeout(()=>openEditHolding(h.id),150);}})),
      ...cards.map(c=>({type:'card',icon:'▭',title:c.name,sub:`${c.bank||'Credit card'}${c.last_four?' · ····'+c.last_four:''}`,
        action:()=>{go('cards');setTimeout(()=>showCardDetail(c.id),200);}})),
      ...budget.items.map(b=>({type:'budget',icon:'≡',title:b.label,sub:`Budget · ${b.type}`,val:`${CUR} ${fmt(b.amount)}`,
        action:()=>go('budget')})),
    ];
  }catch{_paletteIndex=[...PALETTE_ACTIONS];}
}

const PALETTE_GROUP_LABELS={page:'Pages',action:'Quick Actions',holding:'Holdings',card:'Credit Cards',budget:'Budget'};
function renderPaletteResults(q){
  const query=q.trim().toLowerCase();
  _paletteFiltered=query?_paletteIndex.filter(r=>(r.title+' '+(r.sub||'')).toLowerCase().includes(query))
    :_paletteIndex.filter(r=>r.type==='page'||r.type==='action');
  _paletteSelected=0;
  const box=document.getElementById('paletteResults');
  if(!_paletteFiltered.length){box.innerHTML='<div class="palette-empty">No matches. Try a different search.</div>';return;}
  const groups={};
  _paletteFiltered.forEach((r,i)=>{r._idx=i;(groups[r.type]=groups[r.type]||[]).push(r);});
  let html='';
  for(const type of ['page','action','holding','card','budget']){
    if(!groups[type])continue;
    html+=`<div class="palette-group">${PALETTE_GROUP_LABELS[type]}</div>`;
    html+=groups[type].map(r=>`<div class="palette-row" data-idx="${r._idx}" onclick="selectPaletteResult(${r._idx})">
      <div class="palette-row-icon">${r.icon}</div>
      <div class="palette-row-main"><div class="palette-row-title">${esc(r.title)}</div><div class="palette-row-sub">${esc(r.sub||'')}</div></div>
      ${r.val?`<div class="palette-row-val">${esc(r.val)}</div>`:''}
    </div>`).join('');
  }
  box.innerHTML=html;
  highlightPaletteRow();
}
function highlightPaletteRow(){
  document.querySelectorAll('.palette-row').forEach(el=>el.classList.toggle('active',+el.dataset.idx===_paletteSelected));
  document.querySelector('.palette-row.active')?.scrollIntoView({block:'nearest'});
}
function selectPaletteResult(idx){
  const r=_paletteFiltered[idx];if(!r)return;
  closePalette();r.action();
}

/* ─── wire up ───────────────────────────────────────────── */
document.getElementById('nav').addEventListener('click',e=>{
  const b=e.target.closest('.tab');if(!b||!b.dataset.page)return;go(b.dataset.page);
});
document.getElementById('bottomNav').addEventListener('click',e=>{
  const b=e.target.closest('.bn-item');if(!b||!b.dataset.page)return;go(b.dataset.page);
});
document.getElementById('refreshBtn').onclick=syncPrices;
document.getElementById('snapshotBtn').onclick=takeSnapshot;
document.getElementById('settingsBtn').onclick=openSettings;

// command palette
document.getElementById('searchBtn').onclick=openPalette;
document.getElementById('paletteOverlay').onclick=e=>{if(e.target.id==='paletteOverlay')closePalette();};
document.getElementById('paletteInput').addEventListener('input',e=>renderPaletteResults(e.target.value));
document.getElementById('paletteInput').addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'){e.preventDefault();_paletteSelected=Math.min(_paletteSelected+1,_paletteFiltered.length-1);highlightPaletteRow();}
  else if(e.key==='ArrowUp'){e.preventDefault();_paletteSelected=Math.max(_paletteSelected-1,0);highlightPaletteRow();}
  else if(e.key==='Enter'){e.preventDefault();selectPaletteResult(_paletteSelected);}
});
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette();}
  else if(e.key==='Escape'&&document.getElementById('paletteOverlay').classList.contains('show')){closePalette();}
});

// AI assistant widget
document.getElementById('assistantFab').onclick=openAssistant;
document.getElementById('assistantCloseBtn').onclick=closeAssistant;
document.getElementById('assistantClearBtn').onclick=clearAssistant;
document.getElementById('assistantSendBtn').onclick=()=>sendAssistantMessage();
document.getElementById('assistantInput').addEventListener('keydown',e=>{if(e.key==='Enter')sendAssistantMessage();});
document.getElementById('assistantSuggest').addEventListener('click',e=>{
  const b=e.target.closest('.asg-chip');if(!b)return;sendAssistantMessage(b.dataset.q);
});

// holding modal
document.getElementById('saveHoldingBtn').onclick=saveHolding;
document.getElementById('cancelHoldingBtn').onclick=closeHoldingModal;
document.getElementById('deleteHoldingBtn').onclick=deleteHolding;
document.getElementById('f_price_mode').onchange=applyMode;
document.getElementById('searchInput').oninput=onSearch;
document.getElementById('modalOverlay').onclick=e=>{if(e.target.id==='modalOverlay')closeHoldingModal();};

// card modal
document.getElementById('saveCardBtn').onclick=saveCard;
document.getElementById('cancelCardBtn').onclick=closeCardModal;
document.getElementById('deleteCardBtn').onclick=deleteCard;
document.getElementById('cardModalOverlay').onclick=e=>{if(e.target.id==='cardModalOverlay')closeCardModal();};

// statement modal
document.getElementById('saveStmtBtn').onclick=saveStmt;
document.getElementById('cancelStmtBtn').onclick=closeStmtModal;
document.getElementById('stmtModalOverlay').onclick=e=>{if(e.target.id==='stmtModalOverlay')closeStmtModal();};

// statement detail modal
document.getElementById('closeStmtDetailBtn').onclick=closeStmtDetail;
document.getElementById('stmtDetailOverlay').onclick=e=>{if(e.target.id==='stmtDetailOverlay')closeStmtDetail();};

// pay modal
document.getElementById('savePayBtn').onclick=savePay;
document.getElementById('cancelPayBtn').onclick=closePayModal;
document.getElementById('payModalOverlay').onclick=e=>{if(e.target.id==='payModalOverlay')closePayModal();};

// quick update
document.getElementById('saveQuickBtn').onclick=saveQuickUpdate;
document.getElementById('cancelQuickBtn').onclick=closeQuickUpdate;
document.getElementById('saveGoalBtn').onclick=saveGoal;
document.getElementById('cancelGoalBtn').onclick=closeGoalModal;
document.getElementById('quickUpdateOverlay').onclick=e=>{if(e.target.id==='quickUpdateOverlay')closeQuickUpdate();};

// settings
document.getElementById('settingsSaveBtn').onclick=saveSettings;
document.getElementById('settingsCancelBtn').onclick=closeSettings;
document.getElementById('settingsOverlay').onclick=e=>{if(e.target.id==='settingsOverlay')closeSettings();};
document.querySelectorAll('#themeToggle .seg-btn').forEach(b=>b.onclick=()=>setTheme(b.dataset.theme));
document.getElementById('autoSyncEnabled').onchange=async e=>{
  appSettings.auto_sync_enabled=e.target.checked?'1':'0';
  await api('/api/settings',{method:'PUT',body:{auto_sync_enabled:appSettings.auto_sync_enabled}});
  toast(e.target.checked?'Auto-sync enabled':'Auto-sync disabled');
};
document.getElementById('autoSyncInterval').onchange=async e=>{
  appSettings.auto_sync_interval_hours=parseFloat(e.target.value)||24;
  await api('/api/settings',{method:'PUT',body:{auto_sync_interval_hours:appSettings.auto_sync_interval_hours}});
};
document.getElementById('billRemindersEnabled').onchange=async e=>{
  if(e.target.checked&&'Notification' in window&&Notification.permission==='default'){
    const perm=await Notification.requestPermission();
    if(perm!=='granted'){e.target.checked=false;toast('Notification permission denied','err');return;}
  }
  appSettings.bill_reminders_enabled=e.target.checked?'1':'0';
  await api('/api/settings',{method:'PUT',body:{bill_reminders_enabled:appSettings.bill_reminders_enabled}});
  document.getElementById('reminderNote').textContent=('Notification' in window)?`Browser notification permission: ${Notification.permission}`:'Notifications not supported in this browser';
  toast(e.target.checked?'Bill reminders enabled':'Bill reminders disabled');
};
document.getElementById('connectGmailBtn').onclick=()=>window.location.href='/api/email/connect';
document.getElementById('disconnectGmailBtn').onclick=async()=>{await api('/api/email/disconnect',{method:'POST'});await loadEmailStatus();toast('Gmail disconnected');};
document.getElementById('bsSaveBtn').onclick=saveBankStmtConfig;
document.getElementById('bsSyncBtn').onclick=syncBankStatement;
document.getElementById('biSave').onclick=saveBudgetItem;
document.getElementById('biCancel').onclick=closeBudgetModal;
document.getElementById('biDelete').onclick=deleteBudgetItem;
document.getElementById('bi_type').onchange=drawBudgetModal;
document.getElementById('bi_off').onchange=drawBudgetModal;
document.getElementById('bi_source').onchange=drawBudgetModal;
document.getElementById('psSaveBtn').onclick=savePayslipPw;
document.getElementById('psScanBtn').onclick=scanPayslips;
document.getElementById('feedAddBtn').onclick=()=>addFeedDialog();
document.getElementById('feedFindBtn').onclick=discoverFeed;
document.getElementById('etSaveBtn').onclick=saveEtoro;
document.getElementById('etSyncBtn').onclick=()=>syncEtoro();
document.getElementById('etDisconnectBtn').onclick=disconnectEtoro;
document.getElementById('etRestoreBtn').onclick=async()=>{await api('/api/etoro/ignored',{method:'DELETE'});await loadEtoroStatus();toast('Deleted eToro holdings will come back on the next sync');};
document.getElementById('ibSaveBtn').onclick=saveIbkr;
document.getElementById('ibSyncBtn').onclick=()=>syncIbkr();
document.getElementById('ibDisconnectBtn').onclick=disconnectIbkr;
document.getElementById('ibRestoreBtn').onclick=async()=>{await api('/api/ibkr/ignored',{method:'DELETE'});await loadIbkrStatus();toast('Deleted IBKR holdings will come back on the next sync');};
document.getElementById('backfillEmailBtn').onclick=async()=>{
  toast('Importing statement history. This can take a minute.');
  try{const r=await api('/api/email/sync',{method:'POST',body:{days_back:420,backfill:true}});
    toast(r.ok?`Imported ${r.imported} older statement${r.imported===1?'':'s'}`:`Failed: ${r.error}`,r.ok?'ok':'err');_cardsTxnCache=null;}
  catch{toast('Import failed','err');}
};
document.getElementById('imapSaveBtn').onclick=saveImap;
document.getElementById('imapRemoveBtn').onclick=removeImap;
document.getElementById('syncEmailBtn').onclick=async()=>{
  toast('Scanning emails…');
  try{const r=await api('/api/email/sync',{method:'POST',body:{days_back:90}});
    await loadEmailStatus();renderCards();
    toast(r.ok?`Found ${r.found} emails, imported ${r.imported} statements`:`Sync failed: ${r.error}`,r.ok?'ok':'err');
  }catch{toast('Email sync failed','err');}
};


// handle email callback redirect
if(location.search.includes('email=connected'))toast('Gmail connected! Now scan emails from Settings.','ok');
if(location.search.includes('email=error'))toast('Gmail connection failed. Check GMAIL-SETUP.md','err');

// init
(async()=>{
  const s=await api('/api/settings');
  document.getElementById('appTitle').textContent=s.app_title||'Aurum';
  appSettings=s;CUR=s.base_currency||'USD';
  if(!localStorage.getItem('theme')&&s.theme)applyTheme(s.theme);
  go('dashboard');
})();
let _rsz;window.addEventListener('resize',()=>{clearTimeout(_rsz);_rsz=setTimeout(()=>go(currentPage),250);});

if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{navigator.serviceWorker.register('/static/sw.js',{scope:'/'}).catch(()=>{});});
}


/* Long subtitles and captions are cut to one line; the full text is in the hover tooltip. */
(function(){
  const LIM=[['h3 .sub-dim',34],['.sec p',48],['.fine',150],['.kpi-m',90],['.page-sub',70]];
  function trim(root){
    LIM.forEach(([sel,lim])=>root.querySelectorAll(sel).forEach(e=>{
      if(e.dataset.tr)return;e.dataset.tr=1;
      const t=e.textContent.trim();
      if(t.length<=lim||e.querySelector('a,button,input,select,details'))return;
      e.title=t;e.classList.add(sel==='.kpi-m'?'tr-2':'tr-1');}));
  }
  const host=document.querySelector('main')||document.body;
  let q;new MutationObserver(()=>{clearTimeout(q);q=setTimeout(()=>trim(host),60);}).observe(host,{childList:true,subtree:true});
})();
