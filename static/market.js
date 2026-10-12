/* Market page: briefing (conditions, steps with reasons, headlines), buy timing for the monthly fund purchase, ideas compared, watchlist.
   Numbers come from backend/intel.py; this file only lays them out. */
(function(){
const $=id=>document.getElementById(id);
const amtc=n=>CUR+' '+fmt(n);
const pc=(v,d=2)=>v==null?'-':(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(d)+'%';
const TABS=[['brief','Briefing'],['timing','Buy timing'],['ideas','Ideas'],['watch','Watchlist']];
let done={},lastTiming=null;

async function renderMarket(){
  const el=$('page-market');done={};
  el.innerHTML=`<div class="page-head"><div><div class="page-title">Market</div><div class="page-sub">What conditions look like, when to buy, and what else is worth considering. Describes; never predicts</div></div>
    <div style="display:flex;gap:10px"><button class="btn solid sm" id="mkAsk">Brief me with today's news</button></div></div>
    <div class="ctabs" id="mkTabs">${TABS.map(([k,l])=>`<button data-t="${k}">${l}</button>`).join('')}</div>
    <div class="ctab" data-t="brief"><div id="mkBrief"><div class="chart-card"><div class="empty" style="padding:40px 20px">Reading the markets…</div></div></div></div>
    <div class="ctab hidden" data-t="timing"><div id="mkTiming"></div></div>
    <div class="ctab hidden" data-t="ideas"><div id="mkIdeas"><div class="chart-card"><div class="empty" style="padding:40px 20px">Comparing the options on 10 years of prices…</div></div></div></div>
    <div class="ctab hidden" data-t="watch"><div id="marketWatchHost"></div></div>`;
  $('mkTabs').onclick=e=>{const b=e.target.closest('button');if(b)showTab(b.dataset.t);};
  $('mkAsk').onclick=()=>window.askAssistant&&askAssistant("Search the web for what is moving markets today and this week (US stocks, gold, rates, and anything affecting my funds). Then, using my plan from get_money_plan and my monthly fund purchase after payday, tell me in 5 bullet points what, if anything, I should do differently this month and why. Cite sources.");
  let t='brief';try{t=localStorage.getItem('mkTab')||'brief';}catch(e){}
  showTab(t,true);
}
function showTab(t,noScroll){
  const root=$('page-market');if(!root||!root.querySelector(`.ctab[data-t="${t}"]`))t='brief';
  root.querySelectorAll('.ctab').forEach(e=>e.classList.toggle('hidden',e.dataset.t!==t));
  root.querySelectorAll('#mkTabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  try{localStorage.setItem('mkTab',t);}catch(e){}
  if(!done[t]){done[t]=true;({brief:loadBrief,timing:()=>loadTiming(),ideas:loadIdeas,watch:()=>window.renderWatchTab&&renderWatchTab()})[t]();}
  if(!noScroll)window.scrollTo({top:0,behavior:'instant'});
}

/* ───────── briefing ───────── */
async function loadBrief(){
  let d;try{d=await api('/api/market/briefing');}catch(e){$('mkBrief').innerHTML='<div class="chart-card"><div class="empty">Market data is unavailable right now.</div></div>';return;}
  if(!$('mkBrief'))return;
  const n=d.next[0],dot={good:'var(--pos)',warn:'var(--warn)',bad:'var(--neg)'},mc=dot[d.mood.tone]||'var(--ink-dim)';
  const neutral=/VIX|yield/i;
  const tile=r=>{const up=r.chg_1m_pct>0,c=neutral.test(r.label)||r.chg_1m_pct==null?'var(--ink-dim)':up?'var(--pos)':'var(--neg)';
    return `<div class="mk-tile"><div class="mk-tl">${esc(r.label.replace(/ \(.*\)/,''))}</div><div class="mk-tv">${r.error?'-':fmt2(r.last)}</div>
      <div class="mk-tc" style="color:${c}">${pc(r.chg_1m_pct,1)} <span class="sub-dim">1 month</span></div>
      <div class="mk-ts">${r.spark&&r.spark.length>5?MiniChart.spark(r.spark,{w:120,h:34,color:c}):''}</div>
      <div class="fine">${r.from_1y_high_pct==null?'':(r.from_1y_high_pct>-0.5?'At its 1-year high':pc(r.from_1y_high_pct,1)+' from its high')}</div></div>`;};
  const src=t=>{const i=t.lastIndexOf(' - ');return i>20?[t.slice(0,i),t.slice(i+3)]:[t,''];};
  $('mkBrief').innerHTML=`
    <div class="mk-hero">
      <div class="chart-card mk-mood" style="border-top:3px solid ${mc}"><div class="fine">Market mood</div><div class="mk-big" style="color:${mc}">${esc(d.mood.label)}</div>
        <p style="margin:6px 0 0">${esc(d.mood.detail)} ${esc(d.read.replace(/^Markets are calm \(VIX under 16\): nothing forces a rush or a wait\. ?/,''))}</p></div>
      <div class="chart-card mk-next" style="border-top:3px solid var(--accent)"><div class="fine">Your next buy</div><div class="mk-big">${esc(n.buy_dow)} ${esc(n.buy.slice(8))} ${new Date(n.buy+'T00:00:00').toLocaleDateString('en-GB',{month:'short'})}</div>
        <p style="margin:6px 0 0">USD ${fmt(d.invest_usd)} into ${esc(d.invest_label)} (about ${amtc(d.invest_usd*3.6725)}). Salary around ${esc(n.pay_dow)} ${esc(n.pay.slice(8))}, which is ${d.days_to_pay} day${d.days_to_pay===1?'':'s'} away. Buy as soon as it reaches IBKR; <a href="#" id="mkGoTiming">why that day</a>.</p></div></div>
    <div class="mk-grid">${d.weather.map(tile).join('')}</div>
    <div class="sec"><h2>What to do, in order</h2><p>From your plan and today's conditions, each with the reason</p></div>
    <div class="mk-steps">${d.steps.map((s,i)=>`<div class="chart-card mk-step"><div class="pl-n">${i+1}</div><div style="flex:1;min-width:0"><div class="mk-sh"><b>${esc(s.title)}</b>${s.amount?`<span class="rw-chip">${amtc(s.amount)}</span>`:''}</div>
      <div class="fine" style="margin-top:4px">${esc(s.why)}</div></div></div>`).join('')}</div>
    <div class="sec"><h2>What conditions mean for you</h2><p>${esc(d.caveat)}</p></div>
    <div class="chart-card">${d.reasons.map(r=>`<div class="plan-item"><span class="plan-icon" style="color:${dot[r.tone]||'var(--ink-dim)'}">●</span><p>${esc(r.text)}</p></div>`).join('')}</div>
    ${(d.holdings||[]).length?`<div class="sec"><h2>What you hold</h2><p>Where each holding stands, with its latest headlines</p></div>
    <div class="mk-grid">${d.holdings.map(r=>tile({...r,label:r.held_as+' '+r.label})).join('')}</div>
    <div class="two-col">${d.holdings.filter(r=>r.news&&r.news.length).map(r=>`<div class="chart-card"><h3>${esc(r.held_as)}</h3>${r.news.map(h=>{const [ti,so]=src(h.title);return `<div class="plan-item"><p><a href="${esc(h.link)}" target="_blank" rel="noopener noreferrer">${esc(ti)}</a><br><span class="fine">${esc(so)}${so?' · ':''}${esc(h.date)}</span></p></div>`;}).join('')}</div>`).join('')}</div>`:''}
    <div class="sec"><h2>Headlines</h2><p>From Google News, last few days. Titles only; open the source to read them</p></div>
    <div class="two-col">${['stock market S&P 500','gold price','index fund ETF'].map(t=>{const hs=d.headlines.filter(h=>h.topic===t).slice(0,4);
      return `<div class="chart-card"><h3>${esc({'stock market S&P 500':'Markets','gold price':'Gold','index fund ETF':'Index funds'}[t])}</h3>${hs.length?hs.map(h=>{const [ti,so]=src(h.title);return `<div class="plan-item"><span class="plan-icon" title="${h.tone?esc(h.tone)+' for markets':''}" style="color:${{good:'var(--pos)',bad:'var(--neg)',neutral:'var(--ink-dim)'}[h.tone]||'transparent'}">●</span><p><a href="${esc(h.link)}" target="_blank" rel="noopener noreferrer">${esc(ti)}</a><br><span class="fine">${esc(so)}${so?' · ':''}${esc(h.date)}</span></p></div>`;}).join(''):'<div class="fine">No headlines found.</div>'}</div>`;}).join('')}</div>`;
  const g=$('mkGoTiming');if(g)g.onclick=e=>{e.preventDefault();showTab('timing');};
}

/* ───────── buy timing ───────── */
function timingControls(sym,lag,usd){
  return `<div class="chart-card"><div class="pl-ctl"><label>Fund<input id="tmSym" value="${esc(sym)}" maxlength="12"></label>
    <label>Money reaches your broker<select id="tmLag"><option value="0" ${lag==0?'selected':''}>Same day as salary</option><option value="1" ${lag==1?'selected':''}>1 trading day after</option><option value="2" ${lag==2?'selected':''}>2 trading days after</option><option value="3" ${lag==3?'selected':''}>3 trading days after</option></select></label>
    <label>Amount (USD)<input id="tmUsd" type="number" value="${usd}"></label><button class="btn solid sm" id="tmGo">Analyse</button></div>
    <div class="fine">Tests every month since the fund began: pay arriving on your payday (Settings), then each trading day in the next two weeks, using closing prices.</div></div>`;
}
async function loadTiming(sym='',lag=1,usd=1000){
  $('mkTiming').innerHTML=timingControls(sym,lag,usd)+`<div id="tmOut"><div class="chart-card" style="margin-top:20px"><div class="empty" style="padding:30px">Testing ${esc(sym)} over its whole history…</div></div></div>`;
  $('tmGo').onclick=()=>loadTiming($('tmSym').value.trim(),+$('tmLag').value,+$('tmUsd').value||1000);
  let d;try{d=await api(`/api/market/timing?symbol=${encodeURIComponent(sym)}&lag=${lag}&usd=${usd}`);}catch(e){d={error:'request failed'};}
  if(!$('tmOut'))return;
  if(d.error){$('tmOut').innerHTML=`<div class="chart-card" style="margin-top:20px"><div class="empty">${esc(d.error)}</div></div>`;return;}
  lastTiming=d;
  const n=d.next[0],cost=p=>Math.abs(p)/100*d.usd*3.6725,more=(p)=>p>=0?`${pc(p)} higher`:`${pc(-p)} lower`;
  const w=d.offsets.filter(o=>o.offset>d.lag),w5=w.find(o=>o.offset-d.lag===5)||w[w.length-1];
  const best=d.recommended_offset-d.lag;
  $('tmOut').innerHTML=`
    <div class="chart-card pl-verdict" style="margin-top:20px;border-left:3px solid var(--accent)">
      <div class="fine" style="margin:0 0 4px">The answer</div>
      <p style="margin:0;font-size:20px;line-height:1.4"><b>Buy on ${esc(n.buy_dow)} ${esc(n.buy)}</b>, as soon as your money reaches IBKR.</p>
      <p style="margin:10px 0 0;line-height:1.55">${best>0?esc(d.verdict):`Don't wait for a "better" day. Over the ${d.months} paydays since ${esc(d.from.slice(0,4))}, the price a few days later was usually the same or higher, and no weekday was cheaper.`}</p></div>
    <div class="chart-card" style="margin-top:20px"><h3>How to read this page</h3>
      <p class="fine" style="margin:0 0 6px">Every month I pretended you got paid on your payday and bought ${esc(d.symbol)} on your first possible day (day 1 = the day your money reaches IBKR). Then I checked what would have happened if you had waited a few more days instead. Same test, ${d.months} times.</p>
      <p class="fine" style="margin:0">Red means waiting made you pay more. Green means waiting would have saved you money. If the numbers are tiny and the labels say "could be luck", the data cannot tell the days apart, so the simple choice (buy right away) is the right one.</p></div>
    <div class="chart-card" style="margin-top:20px"><h3>1. Should I wait a few days after the money lands?</h3>
      <p style="margin:0 0 10px">${w5?`On average, waiting ${w5.offset-d.lag} more days would have made the price <b style="color:${w5.mean_pct>0?'var(--neg)':'var(--pos)'}">${more(w5.mean_pct)}</b>, about ${CUR} ${fmt(cost(w5.mean_pct))} ${w5.mean_pct>0?'more':'less'} on USD ${fmt(d.usd)}.`:''}</p>
      <div class="table-wrap"><table class="pl-t"><thead><tr><th>If you waited</th><th>The price was, on average</th><th>On USD ${fmt(d.usd)}</th><th>Can we trust it?</th></tr></thead><tbody>
      ${w.map(o=>`<tr><td>${o.offset-d.lag} more day${o.offset-d.lag>1?'s':''}</td><td style="color:${o.mean_pct>0?'var(--neg)':'var(--pos)'}">${more(o.mean_pct)}</td><td>${CUR} ${fmt(cost(o.mean_pct))} ${o.mean_pct>0?'more':'less'}</td><td>${o.reliable?'<b>Yes</b>':'<span class="sub-dim">Could be luck</span>'}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="two-col" style="margin-top:20px">
      <div class="chart-card"><h3>2. Is any weekday cheaper?</h3><p style="margin:0 0 8px">${d.cheapest_weekday?esc(d.weekday_note):'No. Every weekday is within a rounding error of the others.'}</p>
        ${d.weekdays.map(x=>`<div class="pl-wf-top" style="padding:5px 0;border-bottom:1px solid var(--line)"><span>${x.day}</span><span class="sub-dim">${x.mean_pct>0?'slightly dearer':'slightly cheaper'} (${pc(x.mean_pct)})</span></div>`).join('')}</div>
      <div class="chart-card"><h3>3. Should I split it in two?</h3><p style="margin:0 0 8px">${d.split.mean_pct>0?'No.':'Maybe.'} Putting half in now and half ${d.horizon-d.lag} days later cost <b>${pc(d.split.mean_pct)}</b> on average compared with all at once (${CUR} ${fmt(cost(d.split.mean_pct))} on USD ${fmt(d.usd)}).</p>
        <div class="fine">${d.split.reliable?'This is not luck: prices drift up, so holding cash back costs a little.':'Could be luck, but prices tend to drift up, so holding cash back usually costs a little.'}</div></div></div>
    <div class="two-col" style="margin-top:20px">
      <div class="chart-card"><h3>Your next three buys</h3>${d.next.map(x=>`<div class="pl-wf-top" style="padding:8px 0;border-bottom:1px solid var(--line)"><span>Salary ${esc(x.pay_dow)} ${esc(x.pay)}</span><b>Buy ${esc(x.buy_dow)} ${esc(x.buy)}</b></div>`).join('')}
        <div class="fine" style="margin-top:10px">Weekends are skipped. London holidays are not known, so check the calendar around Christmas and New Year.</div></div>
      <div class="chart-card"><h3>Placing the order <span class="sub-dim">your time</span></h3>${d.hours.tips.map(t=>`<div class="plan-item"><p class="fine" style="margin:0">${esc(t)}</p></div>`).join('')}</div></div>
    <details class="more" style="margin-top:14px"><summary>How reliable is this test?</summary><div class="chart-card"><p class="fine" style="margin:0">${esc(d.caution)} "Could be luck" means the average difference is smaller than twice its margin of error, so repeating the test on other months could easily give the opposite answer.</p></div></details>`;
}

/* ───────── ideas ───────── */
async function loadIdeas(){
  let d;try{d=await api('/api/market/ideas');}catch(e){$('mkIdeas').innerHTML='<div class="chart-card"><div class="empty">Could not load price history right now.</div></div>';return;}
  if(!$('mkIdeas'))return;
  const col=(v,good)=>v==null?'':`style="color:${v>=good?'var(--pos)':v<0?'var(--neg)':'inherit'}"`;
  $('mkIdeas').innerHTML=`
    <div id="mkDiscover" style="margin-bottom:20px"></div>
    <div class="chart-card pl-verdict" style="border-left:3px solid var(--accent)"><p style="margin:0;font-size:16px;line-height:1.55">Cash at ${d.cash_rate}% is the bar every option has to beat for the risk. ${(()=>{const eq=d.rows.filter(r=>!r.inr&&!/Bonds|Gold/.test(r.role)&&r.cagr_3y!=null),beat=eq.filter(r=>r.vs_cash>0).length,dd=eq.map(r=>-r.max_drawdown);return eq.length?`${beat} of ${eq.length} share funds beat it over 3 years, but their worst falls in the last 5 years were ${Math.min(...dd).toFixed(0)}% to ${Math.max(...dd).toFixed(0)}%.`:'';})()} Three strong years flatter all of them, so judge them by the worst fall as much as the return. Diversifiers are the ones that do not move with the core fund.</p></div>
    <div class="chart-card" style="margin-top:20px"><div class="table-wrap"><table class="pl-t"><thead><tr><th>Option</th><th>1 year</th><th>${tip('3 yrs a year','cagr')}</th><th>5 yrs a year</th><th>${tip('Swings','swings')}</th><th>${tip('Worst fall','worst')}</th><th>${tip('Overlap with the core fund','overlap')}</th><th>3 yrs vs cash</th><th></th></tr></thead><tbody>
      ${d.rows.map(r=>`<tr><td><b>${esc(r.name)}</b><div class="fine">${esc(r.role)} · ${esc(r.symbol)}</div></td>
        <td>${pc(r.ret_1y,1)}</td><td>${pc(r.cagr_3y,1)}</td><td>${pc(r.cagr_5y,1)}</td><td>${r.vol}%</td><td>${pc(r.max_drawdown,1)}</td>
        <td>${r.corr_core==null?'-':r.corr_core===1?'-':r.corr_core}<div class="fine">${r.corr_core==null||r.corr_core===1?'':r.corr_core<0.5?'diversifies':r.corr_core<0.8?'partly':'moves with it'}</div></td>
        <td ${col(r.vs_cash,0)}>${pc(r.vs_cash,1)}</td>
        <td>${r.inr?'':`<button class="btn ghost sm" data-w="${esc(r.symbol)}" data-n="${esc(r.name)}">Watch</button>`}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="two-col" style="margin-top:20px">
      <div class="chart-card"><h3>Reading it</h3>${d.rows.filter(r=>r.note).map(r=>`<div class="plan-item"><p><b>${esc(r.name)}.</b> <span class="fine">${esc(r.note)}</span></p></div>`).join('')}</div>
      <div class="chart-card"><h3>How these are measured</h3><p class="fine" style="margin-top:0">${esc(d.how)}</p>${d.fx_3y_pct_a_year!=null?`<p class="fine">Foreign-priced funds' returns include the ${pc(d.fx_3y_pct_a_year,1)} a year their currency moved against yours over 3 years.</p>`:''}<p class="fine" style="color:var(--warn)">${esc(d.caution)}</p></div></div>`;
  drawDiscover();
  $('mkIdeas').addEventListener('click',async e=>{const b=e.target.closest('.table-wrap [data-w]');if(!b)return;
    await api('/api/watchlist',{method:'POST',body:{symbol:b.dataset.w,name:b.dataset.n}});toast('Added to watchlist');b.disabled=true;});
}


/* ───────── signals: buy / hold / avoid ───────── */
const VL={strong_buy:['Strong buy','var(--pos)'],buy:['Buy','var(--pos)'],hold:['Hold','var(--warn)'],avoid:['Avoid','var(--neg)'],sell:['Sell','var(--neg)']};
const QC={'Strong uptrend':'var(--pos)',Uptrend:'var(--pos)',Sideways:'var(--warn)',Downtrend:'var(--neg)'};
const pill=(t,c)=>`<span class="mk-pill" style="color:${c};border-color:${c}">${esc(t)}</span>`;
function detailHTML(x){
  const ai=x.ai,q=x.quant||{};
  return `<div class="mk-detail">
    <div class="mk-cols">
      <div><h4>The price says</h4>${q.notes?q.notes.map(n=>`<div class="fine mk-li">${esc(n)}</div>`).join(''):`<div class="fine">${esc(q.error||'')}</div>`}</div>
      ${ai?`<div><h4>Experts say</h4>${ai.expert_views.length?ai.expert_views.map(e=>`<div class="fine mk-li"><b>${esc(e.source)}:</b> ${esc(e.view)} <a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">source</a></div>`).join(''):'<div class="fine">No usable expert opinion was found. Treat the verdict as low confidence.</div>'}</div>
      <div><h4>Reasons</h4>${ai.for.map(t=>`<div class="fine mk-li" style="color:var(--pos)">+ <span style="color:var(--ink)">${esc(t)}</span></div>`).join('')}${ai.against.map(t=>`<div class="fine mk-li" style="color:var(--neg)">− <span style="color:var(--ink)">${esc(t)}</span></div>`).join('')}</div>`:''}
    </div>
    ${ai?`<div class="mk-take"><div><h4>For you</h4><p class="fine" style="margin:0">${esc(ai.for_you)}</p></div><div><h4>Your rules</h4><p class="fine" style="margin:0">${esc(ai.rules_fit)}</p></div><div><h4>What would change this</h4><p class="fine" style="margin:0">${esc(ai.what_changes_it)}</p></div></div>
      <div class="fine" style="margin-top:8px">${x.generated_at?'Written '+timeAgo(x.generated_at)+'. ':''}An AI summary of the evidence found online plus the price statistics, not advice. ${(x.sources||[]).length?`<a href="#" data-src="${esc(x.symbol)}">${x.sources.length} sources read</a>`:''}</div>`:''}</div>`;
}
function sigCard(x){
  const ai=x.ai,q=x.quant||{},v=ai&&VL[ai.verdict];
  return `<div class="chart-card mk-sig" data-s="${esc(x.symbol)}">
    <div class="mk-sh"><div style="min-width:0"><b>${esc(x.symbol)}</b> <span class="sub-dim">${esc(x.name||'')}</span>${q.price?`<div class="fine">${fmt2(q.price)} ${esc(q.currency||'')} · 6 months ${pc(q.ret_6m,1)} · ${pc(q.from_high,1)} from its high</div>`:''}</div>
      <div class="mk-pills">${q.label?`<span class="fine">Price</span>${pill(q.label,QC[q.label]||'var(--ink-dim)')}`:''}${v?`<span class="fine">AI + experts</span>${pill(v[0]+' · '+ai.confidence+' confidence',v[1])}`:''}
        <button class="btn ${ai?'ghost':'solid'} sm" data-an="${esc(x.symbol)}">${ai?'Refresh':'Get expert view'}</button></div></div>
    ${ai?`<p style="margin:8px 0 0;line-height:1.5">${esc(ai.headline)}</p>`:`<div class="fine" style="margin-top:6px">No expert view yet. It reads analyst opinions online and writes a verdict (about 15 seconds, uses your AI credit).</div>`}
    <details class="more" ${ai?'':'style="display:none"'}><summary>Why, and what the experts say</summary>${detailHTML(x)}</details></div>`;
}
async function drawSignals(){
  const host=$('mkSignals');if(!host)return;
  host.innerHTML='<div class="chart-card"><div class="empty" style="padding:24px">Reading price trends…</div></div>';
  let d;try{d=await api('/api/market/signals');}catch(e){host.innerHTML='';return;}
  if(!$('mkSignals'))return;
  if(!d.items.length){host.innerHTML='<div class="chart-card"><div class="empty" style="padding:28px">Search above and add something to your watchlist. Each one gets a price signal straight away and an expert-opinion verdict on request.</div></div>';return;}
  host.innerHTML=`<div class="sec" style="margin-top:6px"><h2>Should you buy?</h2><p>Price trend, what experts say online, and an AI take that knows your plan and investing rules</p></div>${d.items.map(sigCard).join('')}`;
  host.onclick=async e=>{
    const a=e.target.closest('[data-an]');
    if(a){a.disabled=true;a.textContent='Reading experts…';
      try{const r=await api('/api/market/signal?symbol='+encodeURIComponent(a.dataset.an)+'&refresh=1');
        if(r.error&&!r.ai)toast(r.error,'err');
        const card=a.closest('.mk-sig');card.outerHTML=sigCard(r);
        const nc=host.querySelector(`.mk-sig[data-s="${CSS.escape(r.symbol)}"]`);if(nc&&r.ai)nc.querySelector('details').open=true;
      }catch(x){a.disabled=false;a.textContent='Get expert view';toast('Could not reach the AI','err');}
      return;}
    const sc=e.target.closest('[data-src]');
    if(sc){e.preventDefault();const r=await api('/api/market/signal?symbol='+encodeURIComponent(sc.dataset.src));
      sc.parentElement.insertAdjacentHTML('beforeend','<div>'+(r.sources||[]).map(s=>`<div><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></div>`).join('')+'</div>');sc.remove();}
  };
}

/* ───────── ideas worth a look ───────── */
function pickCard(p){
  const q=p.quant||{};
  return `<div class="chart-card mk-sig"><div class="mk-sh"><div style="min-width:0"><b>${esc(p.symbol)}</b> <span class="sub-dim">${esc(p.name)}</span>${q.price?`<div class="fine">${fmt2(q.price)} ${esc(q.currency||'')} · 6 months ${pc(q.ret_6m,1)} · ${pc(q.from_high,1)} from its high</div>`:''}</div>
      <div class="mk-pills">${q.label?`<span class="fine">Price</span>${pill(q.label,QC[q.label]||'var(--ink-dim)')}`:''}<button class="btn ghost sm" data-w="${esc(p.symbol)}" data-n="${esc(p.name)}">Watch</button></div></div>
    <p style="margin:8px 0 6px;line-height:1.5">${esc(p.why)}</p>
    <div class="mk-take"><div><h4>Who says so</h4><p class="fine" style="margin:0">${esc(p.expert_support)} ${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">source</a>`:''}</p></div><div><h4>Risk</h4><p class="fine" style="margin:0">${esc(p.risk)}</p></div><div><h4>For you</h4><p class="fine" style="margin:0">${esc(p.fit)}</p></div></div>
    <div class="fine" style="margin-top:6px"><b>Your rules:</b> ${esc(p.rules_fit)}</div></div>`;
}
function discoverHTML(d){
  if(!d||!d.picks)return `<div class="chart-card"><h3>Worth a look</h3><p class="fine" style="margin:0 0 10px">Searches what analysts and publications are recommending to investors, drops what you already hold, and picks up to four that deserve a look for your plan. It takes about 30 seconds and uses your AI credit.</p><button class="btn solid sm" id="mkFind">Find ideas</button></div>`;
  if(d.error)return `<div class="chart-card"><div class="empty">${esc(d.error)}</div></div>`;
  return `<div class="sec" style="margin-top:0"><h2>Worth a look</h2><p>${esc(d.summary||'')} ${d.generated_at?'Found '+timeAgo(d.generated_at)+'.':''} <a href="#" id="mkFind">Search again</a></p></div>${d.picks.length?d.picks.map(pickCard).join(''):'<div class="chart-card"><div class="empty">Nothing new stood out beyond what you already hold or have on your shortlist.</div></div>'}
    <div class="fine" style="margin:6px 0 18px">AI summary of web evidence, not advice. Prices are checked against a live quote; verify fees and rules yourself before buying.</div>`;
}
async function drawDiscover(){
  const host=$('mkDiscover');if(!host)return;
  let d=null;try{d=await api('/api/market/discover');}catch(e){}
  host.innerHTML=discoverHTML(d);
  host.onclick=async e=>{
    const f=e.target.closest('#mkFind');
    if(f){e.preventDefault();host.innerHTML='<div class="chart-card"><div class="empty" style="padding:30px"><span class="search-spin on" style="position:static"></span> Reading what analysts are recommending…</div></div>';
      try{d=await api('/api/market/discover?refresh=1');}catch(x){d={error:'Could not reach the AI or the web right now.'};}
      host.innerHTML=discoverHTML(d);return;}
    const b=e.target.closest('[data-w]');if(b){await api('/api/watchlist',{method:'POST',body:{symbol:b.dataset.w,name:b.dataset.n}});toast('Added to watchlist');b.disabled=true;}
  };
}
window.drawSignals=drawSignals;

window.renderMarket=renderMarket;
})();
