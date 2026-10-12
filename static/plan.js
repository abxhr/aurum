/* Money plan page, dashboard insights feed, and the Cards "foreign spend and card value" panel.
   Numbers come from backend/planner.py; this file only lays them out. */
(function(){
const $=id=>document.getElementById(id);
const amtc=n=>CUR+' '+fmt(n);
const ask=t=>window.askAssistant&&window.askAssistant(t);
const md=t=>window.__ag?window.__ag.md(t):esc(t).replace(/\n/g,'<br>');
const SEV={high:'bad',medium:'warn',low:'good'};
let P=null,R=null;

/* ───────── dashboard feed ───────── */
async function loadPlanInsights(){
  const host=$('planInsights');if(!host)return;
  let r;try{r=await api('/api/plan/insights');}catch(e){return;}
  const list=(r.insights||[]).slice(0,5);
  if(!list.length){host.innerHTML='';return;}
  host.innerHTML=`<div class="sec"><h2>What to do next</h2><p>Found from your own numbers. Dismiss what you have dealt with</p></div>
    <div class="chart-card">${list.map(i=>`<div class="plan-item pl-ins" data-id="${esc(i.id)}">
      <span class="plan-icon sev-ic-${i.severity}">${typeof sevIcon==='function'?sevIcon(i.severity):''}</span>
      <div style="flex:1;min-width:0"><p><b>${esc(i.title)}</b></p><div class="fine" style="margin-top:3px">${esc(i.body)}</div>
        <div class="pl-acts">${i.page?`<button class="btn ghost sm" data-go="${esc(i.page)}">Open</button>`:''}${i.ask?`<button class="btn ghost sm" data-ask="${esc(i.ask)}">Ask the assistant</button>`:''}<button class="btn ghost sm" data-x="1">Dismiss</button></div></div></div>`).join('')}</div>`;
  host.onclick=async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.go)go(b.dataset.go);
    else if(b.dataset.ask)ask(b.dataset.ask);
    else if(b.dataset.x){const row=b.closest('.pl-ins');await api('/api/plan/insights/'+encodeURIComponent(row.dataset.id)+'/dismiss',{method:'POST'});row.remove();if(!host.querySelector('.pl-ins'))host.innerHTML='';}
  };
}

/* ───────── plan page ───────── */
const kpi=(l,v,m,tone)=>`<div class="kpi ${tone||''}"><div class="kpi-l">${l}</div><div class="kpi-v">${v}</div><div class="kpi-m">${m||''}</div></div>`;

function stepsHTML(steps){
  if(!steps.length)return `<div class="empty" style="padding:24px">Nothing to move right now.</div>`;
  return steps.map((s,i)=>`<div class="pl-step"><div class="pl-n">${i+1}</div><div style="flex:1;min-width:0"><b>${esc(s.title)}</b>
    <div class="fine" style="margin-top:3px">${esc(s.why)}</div>
    ${s.id==='deploy'||s.id==='contribute'?`<div class="pl-acts"><button class="btn ghost sm" data-ask="Build me a concrete plan for this: which instruments that fit my investing rules, how much into each, and when. Back it with current data.">Plan this with the assistant</button></div>`:''}
    ${s.id==='move-idle'?`<div class="pl-acts"><button class="btn ghost sm" data-ask="Check the current terms of my savings account: is there a balance cap or tiered rate that affects moving ${fmt(s.amount)} ${CUR} in?">Check the account terms</button></div>`:''}</div></div>`).join('');
}

function waterfallHTML(w,liquid){
  const max=Math.max(...w.map(r=>r.target||r.amount),1);
  return w.map(r=>{const pct=r.target?Math.min(100,r.amount/r.target*100):Math.min(100,r.amount/max*100);
    return `<div class="pl-wf"><div class="pl-wf-top"><span>${esc(r.name)}</span><b>${amtc(r.amount)}${r.target?` <span class="sub-dim">of ${fmt(r.target)}</span>`:''}</b></div>
      <div class="pl-bar"><span style="width:${pct}%;${r.target&&r.amount<r.target?'background:var(--warn)':''}"></span></div></div>`;}).join('');
}

const PLAN_TABS=[['plan','Overview'],['outlook','Outlook'],['markets','Markets'],['mine','My plan'],['more','Review']];
let _planDone={};
const sgn=n=>(n<0?'-':'+')+fmt(Math.abs(n));
function showPlanTab(t,noScroll){
  const root=$('page-plan');if(!root||!root.querySelector(`.ctab[data-t="${t}"]`))t='plan';
  root.querySelectorAll('.ctab').forEach(e=>e.classList.toggle('hidden',e.dataset.t!==t));
  root.querySelectorAll('#plTabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  try{localStorage.setItem('planTab',t);}catch(e){}
  if(!_planDone[t]){_planDone[t]=true;       // charts need a visible container, so each tab draws itself the first time it opens
    if(t==='outlook')drawFan();else if(t==='markets'){loadWeather();drawAlerts();}else if(t==='more')drawReview();}
  if(!noScroll)window.scrollTo({top:0,behavior:'instant'});
}
function verdict(p,f){
  const g=p.goals.map(g=>g.shortfall>0?`<b>${esc(g.name)}</b> is ${CUR} ${fmt(g.shortfall)} short`:`<b>${esc(g.name)}</b> (${CUR} ${fmt(g.amount)} by ${esc(g.date)}) is funded`);
  const left=f.free_after_everything,avg=f.free_after_everything_avg;
  const money=left>=0?`A typical month leaves ${CUR} ${fmt(left)} after real spending and investing.`
    :avg>=0?`A typical month is ${CUR} ${fmt(-left)} short after investing; your annual bonus covers it (average month ${sgn(avg)}).`
    :`Even with your bonus you are ${CUR} ${fmt(-avg)} a month short after investing. Cut spending or invest less.`;
  const tone=g.some((_,i)=>p.goals[i].shortfall>0)||avg<0?'var(--neg)':left<0?'var(--warn)':'var(--pos)';
  return `<div class="chart-card pl-verdict" style="border-left:3px solid ${tone}"><p style="margin:0;font-size:16px;line-height:1.55">${g.length?g.join('. ')+'. ':''}${money}</p></div>`;
}
function monthHTML(f){
  const row=(l,v,b)=>`<div class="pl-wf-top" style="padding:7px 0;border-bottom:1px solid var(--line)"><span ${b?'style="font-weight:600"':''}>${l}</span><b>${v}</b></div>`;
  const bonus=f.net_income_avg-f.net_income;
  return row('Take-home, typical month','+'+fmt(f.net_income))+row('Cards, usual month','-'+fmt(f.card_spend))+row('Outside cards and irregular <span class="sub-dim">rent, school fees, trips, cash and transfers</span>','-'+fmt(f.off_card_spend))
    +row('Invested and saved','-'+fmt(f.invested_or_saved))+row('Left in a typical month',sgn(f.free_after_everything),1)
    +row('Annual bonus, spread over 12 months','+'+fmt(bonus))+row('Left in an average month',sgn(f.free_after_everything_avg),1)
    +`<div class="fine" style="margin-top:10px">Cash and savings in 3 months, if every month is typical: ${CUR} ${fmt(f.projection[2].liquid_base)} (includes about ${CUR} ${fmt(f.interest_per_month)} a month of savings profit).</div>`;
}

async function renderPlan(){
  const el=$('page-plan');
  const head=`<div class="page-head"><div><div class="page-title">Money plan</div><div class="page-sub">Where your money should sit, and what to do next</div></div>
    <div style="display:flex;gap:10px"><button class="btn solid sm" id="plAsk">Ask the assistant</button></div></div>`;
  el.innerHTML=head+`<div class="chart-card"><div class="empty" style="padding:44px 20px">Building your plan…</div></div>`;
  let d;try{[d,R]=await Promise.all([api('/api/plan'),api('/api/plan/requirements')]);}catch(e){el.innerHTML=head+`<div class="chart-card"><div class="empty">Could not build the plan: ${esc(e.message)}</div></div>`;return;}
  if(currentPage!=='plan')return;
  P=d;_planDone={};const p=d.plan,f=d.forecast,S=d.settings,runway=p.assumptions.monthly_spend?p.balances.liquid/p.assumptions.monthly_spend:null;
  const fund=(R.invest_label||'').split(' ')[0].toUpperCase()||'VTI';
  el.innerHTML=head+`<div class="ctabs" id="plTabs">${PLAN_TABS.map(([k,l])=>`<button data-t="${k}">${l}</button>`).join('')}</div>
    <div class="ctab" data-t="plan">
      ${verdict(p,f)}
      <div class="kpis" style="margin-top:16px">${kpi(tip('Free to invest','free'),amtc(p.deploy.free_to_deploy),p.locked_term_base?`${CUR} ${fmt(p.locked_term_base)} more is locked in fixed-term holdings until it matures`:'After emergency fund and dated goals')}
        ${kpi(tip('Idle in current accounts','idle'),amtc(p.idle_current_base),p.idle_annual_gain_base?`Earning ~nothing. At ${S.savings_rate}% that is ${CUR} ${fmt(p.idle_annual_gain_base)} a year`:`Nothing above your ${S.buffer_months} month buffer`,p.idle_current_base>=2000?'warn':'good')}
        ${kpi(tip('Cash runway','runway'),runway!=null?runway.toFixed(1)+' months':'-',`${CUR} ${fmt(p.balances.liquid)} cash and savings at ${CUR} ${fmt(p.assumptions.monthly_spend)} a month of real spending`,runway==null?'':runway>=S.emergency_months?'good':'warn')}
        ${kpi('Safe pile earns',p.assumptions.safe_blended_rate_pct.toFixed(1)+'%',`Blended: ${S.savings_rate}% on savings, fixed-term holdings at their rate, current accounts nothing`)}</div>
      <div class="sec"><h2>Do these, in order</h2><p>Each step comes from your balances, goals and spending</p></div>
      <div class="chart-card" id="plSteps">${stepsHTML(p.steps)}</div>
      <div id="plHabit" style="margin-top:20px"></div>
      <div class="two-col" style="margin-top:20px">
        <div class="chart-card"><h3>Where your money sits <span class="sub-dim">filled in this order</span></h3>${waterfallHTML(p.waterfall,p.balances.liquid)}
          <div class="fine" style="margin-top:12px">Liquid cash and savings ${CUR} ${fmt(p.balances.liquid)} · growth assets ${CUR} ${fmt(Object.values(p.balances.growth).reduce((a,b)=>a+b,0))}</div></div>
        <div class="chart-card"><h3>A month in numbers <span class="sub-dim">${CUR}, from payslips and card statements</span></h3>${monthHTML(f)}</div></div>
      ${p.flags.length||p.concentration.length?`<div class="chart-card" style="margin-top:20px"><h3>Worth a look</h3>
        ${p.flags.map(x=>`<div class="plan-item"><span class="plan-icon sev-ic-medium">${typeof sevIcon==='function'?sevIcon('medium'):''}</span><p><b>${esc(x.holding)}</b><br><span class="fine">${esc(x.issue)}</span></p></div>`).join('')}
        ${p.concentration.length?`<div class="fine" style="margin-top:8px">Growth assets: ${p.concentration.map(c=>`${esc(c.name)} ${c.pct_of_growth}%`).join(' · ')}</div>`:''}</div>`:''}
    </div>
    <div class="ctab hidden" data-t="outlook">
      <div class="kpis">${kpi('In 5 years, typical','<span id="plK5">…</span>','<span id="plK5m">Simulated, with a range</span>')}
        ${kpi('Adding each month',amtc(R.invest_base+R.save_base),'From Your plan, into growth assets and savings')}</div>
      <div class="sec"><h2>Where this could take you</h2><p>Simulated 3,000 ways. Cautious and optimistic are the 10th and 90th percentile outcomes</p></div>
      <div class="chart-card"><div class="pl-ctl"><label>Years<select id="plYears"><option>5</option><option selected>10</option><option>15</option><option>20</option></select></label>
        <label>Target (${CUR})<input id="plTarget" type="number" inputmode="numeric" placeholder="e.g. 1000000"></label><span class="fine" id="plProb"></span></div><div id="plFan" class="chart-mount"></div></div>
      <div class="two-col" style="margin-top:20px">
        <div class="chart-card"><h3>What if…</h3><div class="pl-ctl">
          <label>Start (${CUR})<input id="wiStart" type="number" inputmode="numeric" value="${Math.round(p.deploy.free_to_deploy)}"></label>
          <label>Monthly (${CUR})<input id="wiMonthly" type="number" inputmode="numeric" value="${Math.round(p.deploy.monthly_contribution)}"></label>
          <label>Years<input id="wiYears" type="number" inputmode="numeric" value="5"></label>
          <label>In equities (%)<input id="wiEq" type="number" inputmode="numeric" value="70"></label>
          <label>Goal (${CUR})<input id="wiTarget" type="number" inputmode="numeric" placeholder="optional"></label>
          <button class="btn solid sm" id="wiGo">Simulate</button></div><div id="wiOut" class="pl-out"></div></div>
        <div class="chart-card"><h3>${tip('Safe rate','hurdle')} vs the market</h3>
          <table class="pl-t"><thead><tr><th>If your savings pay</th><th>Equities' expected edge</th></tr></thead><tbody>
          ${p.hurdle.map(h=>`<tr><td>${h.savings_rate_pct}%</td><td>+${h.equity_premium_pct} pts a year</td></tr>`).join('')}</tbody></table>
          <div class="fine" style="margin-top:10px">${esc(p.note)}</div></div></div>
    </div>
    <div class="ctab hidden" data-t="markets">
      <div class="sec" style="margin-top:0"><h2>Entering the market</h2><p>Conditions, evidence and your own rules. Nobody can time it, so this describes rather than predicts</p></div>
      <div class="chart-card" id="plWeather"><div class="empty" style="padding:20px">Reading the markets…</div></div>
      <div class="two-col" style="margin-top:20px">
        <div class="chart-card"><h3>Lump sum or staged?</h3><div class="pl-ctl"><label>Fund<input id="btSym" value="${fund}" maxlength="12"></label>
          <label>Spread over<select id="btMonths"><option value="6">6 months</option><option value="12" selected>12 months</option><option value="24">24 months</option></select></label>
          <button class="btn solid sm" id="btGo">Test</button></div><div id="btOut" class="pl-out"></div></div>
        <div class="chart-card"><h3>Your dip alerts <span class="sub-dim">shown on the dashboard when hit</span></h3><div id="plAlerts"></div>
          <div class="pl-ctl" style="margin-top:12px"><label>Fund<input id="alSym" placeholder="${fund}" maxlength="12"></label><label>Fall (%)<input id="alPct" type="number" inputmode="numeric" value="10"></label><button class="btn ghost sm" id="alAdd">Add</button></div></div></div>
    </div>
    <div class="ctab hidden" data-t="mine">
      ${reqHTML()}
      <div class="chart-card"><h3>Market assumptions <span class="sub-dim">what the simulations use</span></h3><div class="pl-ctl">
        <label>Equity return (%)<input id="asEqR" type="number" step="0.5" value="${S.assume.equity[0]}"></label>
        <label>Equity swings (%)<input id="asEqV" type="number" step="0.5" value="${S.assume.equity[1]}"></label>
        <label>Staged buys (${tip('tranches','tranche')})<input id="asTr" type="number" value="${S.tranches}"></label>
        <button class="btn solid sm" id="asSave">Save</button></div><div class="fine">Defaults are deliberately modest. Change them to what you believe and the plan recalculates.</div></div>
    </div>
    <div class="ctab hidden" data-t="more">
      <div class="chart-card" id="plReview"></div>
    </div>`;
  bindPlan();loadHabit();
  $('plTabs').onclick=e=>{const b=e.target.closest('button');if(b)showPlanTab(b.dataset.t);};
  let t='plan';try{t=localStorage.getItem('planTab')||'plan';}catch(e){}
  showPlanTab(t,true);
}

async function loadHabit(){
  const h=await api('/api/invest/habit').catch(()=>null),host=$('plHabit');if(!h||!host||!h.planned)return;
  const cur=h.months[h.months.length-1];
  host.innerHTML=`<div class="chart-card"><div class="panel-head"><h3>Your investing habit <span class="sub-dim">money that left your bank for investing, per month</span></h3></div>
    <div class="pl-habit">${h.months.map(m=>`<div class="pl-hm ${m.hit?'on':m===cur?'now':'off'}" title="${esc(m.month)}: ${CUR} ${fmt(m.sent)} sent"><b>${m.hit?'✓':m===cur?'…':'–'}</b><span>${esc(fmtMonth(m.month))}</span></div>`).join('')}</div>
    <div class="fine" style="margin-top:10px">Plan: ${CUR} ${fmt(h.planned)} a month. ${h.streak?`${h.streak} month${h.streak>1?'s':''} in a row. `:''}A month counts when an investing transfer shows on a bank statement the app reads; money sent from another bank is not seen.</div></div>`;
}

function goalRow(g){
  return `<div class="pl-ctl rq-goal"><label>Goal<input class="g-n" maxlength="40" value="${esc(g.name||'')}" placeholder="Marriage" style="width:min(170px,100%)"></label>
    <label>Amount (${CUR})<input class="g-a" type="number" inputmode="numeric" value="${g.amount||''}"></label>
    <label>Needed by<input class="g-d" type="date" value="${esc(g.date||'')}"></label>
    <button class="btn ghost sm g-x" type="button">Remove</button></div>`;
}
function reqHTML(){
  const r=R;
  return `<div class="chart-card" id="plReq" style="margin-bottom:20px"><h3>Your savings plan <span class="sub-dim">what you put away and what for. Every number below is calculated from this</span></h3>
    <div class="pl-ctl"><label>Invest each month (USD)<input id="rqUsd" type="number" inputmode="decimal" step="50" value="${r.invest_usd}"></label>
      <label>Into<input id="rqLabel" maxlength="40" value="${esc(r.invest_label)}" style="width:min(230px,100%)"></label>
      <label>Also save each month (${CUR})<input id="rqSave" type="number" inputmode="numeric" value="${r.save_base}"></label></div>
    <div class="fine" id="rqHint" style="margin:-4px 0 12px"></div>
    ${r.other_base?`<div class="fine" style="margin:-6px 0 12px;color:var(--warn)">Your Budget has ${CUR} ${fmt(r.other_base)} a month more in other saving or investing lines. They count in every figure here; edit them on the Budget page.</div>`:''}
    <div class="attn-title" style="margin-top:6px">Goals</div><div id="rqGoals">${r.goals.map(goalRow).join('')}</div>
    <button class="btn ghost sm" id="rqAddGoal" type="button">Add a goal</button>
    <div class="attn-title" style="margin-top:16px">Rules the plan follows</div>
    <div class="pl-ctl"><label>${tip('Emergency fund','emergency')} (months of essentials)<input id="rqEm" type="number" step="0.5" value="${r.emergency_months}"></label>
      <label>Keep in current account (months)<input id="rqBuf" type="number" step="0.5" value="${r.buffer_months}"></label>
      <label>Savings account rate (%)<input id="rqRate" type="number" step="0.1" value="${r.savings_rate}"></label>
      <label>Savings account balance cap (${CUR}, 0 = none)<input id="rqCap" type="number" inputmode="numeric" value="${r.savings_cap}"></label>
      <label>${tip('Fixed-term holdings','term')} pay out on<input id="rqMat" type="date" value="${esc(r.term_maturity)}"></label></div>
    <div class="pl-acts"><button class="btn solid sm" id="rqGo" type="button">Save plan</button><span class="fine" id="rqMsg"></span></div>
    <div class="fine" style="margin-top:10px">The monthly amounts are written to your Budget lines, so Budget, Overview and this page always agree. Spending is not typed here: it is measured from your card statements.</div></div>`;
}
function bindReq(){
  const hint=()=>{const u=+$('rqUsd').value||0;$('rqHint').textContent=`About ${CUR} ${fmt(u*R.usd_base)} a month at ${R.usd_base} ${CUR} per USD.`;};
  $('rqUsd').oninput=hint;hint();
  $('rqAddGoal').onclick=()=>$('rqGoals').insertAdjacentHTML('beforeend',goalRow({}));
  $('rqGoals').onclick=e=>{const b=e.target.closest('.g-x');if(b)b.closest('.rq-goal').remove();};
  $('rqGo').onclick=async()=>{
    const v=i=>$(i).value,goals=[...document.querySelectorAll('#rqGoals .rq-goal')].map(r=>({name:r.querySelector('.g-n').value,amount:r.querySelector('.g-a').value,date:r.querySelector('.g-d').value}));
    $('rqMsg').textContent='Saving…';
    try{R=await api('/api/plan/requirements',{method:'PUT',body:{invest_usd:v('rqUsd'),invest_label:v('rqLabel'),save_base:v('rqSave'),goals,emergency_months:v('rqEm'),buffer_months:v('rqBuf'),
        savings_rate:v('rqRate'),savings_cap:v('rqCap'),term_maturity:v('rqMat')}});
      toast('Plan saved');renderPlan();}
    catch(e){let m='Could not save';try{m=JSON.parse(e.message).error||m;}catch{}$('rqMsg').textContent=m;toast(m,'err');}};
}

function bindPlan(){
  const el=$('page-plan');
  el.onclick=e=>{const b=e.target.closest('[data-ask]');if(b)ask(b.dataset.ask);};
  $('plAsk').onclick=()=>ask('Look at my whole situation and tell me the three most valuable things I could do with my money this month, with amounts.');
  bindReq();
  $('plYears').onchange=$('plTarget').onchange=drawFan;
  $('wiGo').onclick=async()=>{
    const v=i=>$(i).value;$('wiOut').textContent='Simulating…';
    try{const q=new URLSearchParams({start:v('wiStart')||0,monthly:v('wiMonthly')||0,years:v('wiYears')||5,equity:v('wiEq')||0});if(v('wiTarget'))q.set('target',v('wiTarget'));
      const r=await api('/api/plan/simulate?'+q);const n=r.p50.length-1;
      $('wiOut').innerHTML=`After ${r.years.length} years: <b>${amtc(r.p50[n])}</b> typical, between <b>${amtc(r.p10[n])}</b> and <b>${amtc(r.p90[n])}</b> in 8 of 10 outcomes.${r.prob_target_pct!=null?` Chance of reaching the goal: <b>${r.prob_target_pct}%</b>.`:''}
        <div class="fine">Assumes ${r.inputs.return_pct.toFixed(1)}% a year with ${r.inputs.volatility_pct.toFixed(1)}% swings.</div>`;}
    catch(e){$('wiOut').textContent='Could not simulate.';}};
  $('btGo').onclick=async()=>{
    $('btOut').textContent='Testing on real price history…';
    try{const r=await api(`/api/plan/backtest?symbol=${encodeURIComponent($('btSym').value||'VTI')}&months=${$('btMonths').value}`);
      $('btOut').innerHTML=r.error?esc(r.error):`Across ${r.windows_tested} historical starting points, investing it all on day one beat staging it <b>${r.lump_sum_won_pct}%</b> of the time (median edge ${r.median_lump_edge_pct}%). In the worst case staging did ${Math.abs(r.worst_lump_vs_dca_pct)}% better.<div class="fine">${esc(r.read)}</div>`;}
    catch(e){$('btOut').textContent='Could not test that symbol.';}};
  $('alAdd').onclick=async()=>{const sym=($('alSym').value||'').trim().toUpperCase();if(!sym)return;
    const al=P.settings.alerts.filter(a=>a.symbol!==sym).concat([{symbol:sym,drop_pct:+$('alPct').value||10}]);
    P.settings=await api('/api/plan/settings',{method:'PUT',body:{alerts:al}});$('alSym').value='';drawAlerts();};
  $('asSave').onclick=async()=>{const v=i=>+$(i).value;
    await api('/api/plan/settings',{method:'PUT',body:{tranches:v('asTr'),assume:{equity:[v('asEqR'),v('asEqV')]}}});
    toast('Saved');renderPlan();};
}

async function drawFan(){
  const yrs=+$('plYears').value,tg=+$('plTarget').value;
  let r;try{r=await api(`/api/plan/projection?years=${yrs}${tg?'&target='+tg:''}`);}catch(e){$('plFan').innerHTML='<div class="empty">Could not run the projection.</div>';return;}
  if(!$('plFan'))return;
  const labels=r.years.map(y=>'Year '+y);
  MiniChart.lines($('plFan'),labels,[{name:'Optimistic',data:r.p90},{name:'Typical',data:r.p50},{name:'Cautious',data:r.p10},{name:'Safe pile only',data:r.safe}],{unit:CUR,height:300});
  const i5=Math.min(4,r.p50.length-1);
  if($('plK5')){$('plK5').textContent=CUR+' '+fmt(r.p50[i5]);$('plK5m').textContent=`Between ${fmt(r.p10[i5])} and ${fmt(r.p90[i5])} in 8 of 10 outcomes`;}
  $('plProb').innerHTML=r.prob_target_pct!=null?`Chance of reaching ${CUR} ${fmt(tg)}: <b>${r.prob_target_pct}%</b>`:`Safe pile at ${r.inputs.safe_rate_pct}%, growth assets at ${r.inputs.equity_return_pct}% with ${r.inputs.equity_vol_pct}% swings, ${CUR} ${fmt(r.inputs.monthly_invested)} added monthly, dated goals paid out of the safe pile.`;
}

async function loadWeather(){
  const host=$('plWeather');let w;
  try{w=await api('/api/plan/weather');}catch(e){host.innerHTML='<div class="empty" style="padding:20px">Market data is unavailable right now.</div>';return;}
  if(!$('plWeather'))return;
  const pc=v=>v==null?'-':(v>0?'+':'')+v+'%';
  host.innerHTML=`<div class="fine" style="margin:0 0 12px"><b>${esc(w.read)}</b> ${esc(w.caveat)}</div>
    <div class="table-wrap"><table class="pl-t"><thead><tr><th>Market</th><th>Now</th><th>1 month</th><th>From 1-yr high</th><th>vs 200-day</th></tr></thead><tbody>
    ${w.rows.map(r=>`<tr><td>${esc(r.label)}</td><td>${r.error?'-':fmt2(r.last)}</td><td>${pc(r.chg_1m_pct)}</td><td>${pc(r.from_1y_high_pct)}</td><td>${pc(r.vs_200d_pct)}</td></tr>`).join('')}</tbody></table></div>`;
}

function drawAlerts(){
  const al=P.settings.alerts;
  $('plAlerts').innerHTML=al.length?al.map(a=>`<div class="pl-wf-top" style="padding:6px 0;border-bottom:1px solid var(--line)"><span>${esc(a.symbol)} <span class="sub-dim">${a.drop_pct}% below its 1-year high</span></span>
    <button class="btn ghost sm" data-rm="${esc(a.symbol)}">Remove</button></div>`).join(''):'<div class="fine">No alerts yet.</div>';
  $('plAlerts').onclick=async e=>{const b=e.target.closest('[data-rm]');if(!b)return;
    P.settings=await api('/api/plan/settings',{method:'PUT',body:{alerts:P.settings.alerts.filter(a=>a.symbol!==b.dataset.rm)}});drawAlerts();};
}

function drawReview(){
  const r=P.review||{},host=$('plReview');if(!host)return;
  const when=r.at?new Date(r.at+'Z').toLocaleDateString('en-GB',{day:'numeric',month:'short'}):'';
  host.innerHTML=`<h3>Weekly review</h3><div class="fine" style="margin:-6px 0 10px">The assistant reads your plan and writes a short brief once a week</div>${r.running?'<div class="fine">Writing your review… this takes about a minute.</div>':r.text?`<div class="ag-md">${md(r.text)}</div><div class="fine" style="margin-top:10px">Written ${when}</div>`
      :`<div class="fine">${r.error?esc(r.error):'No review yet. It needs your AI key set in the assistant, then runs weekly on its own.'}</div>`}
    <div class="pl-acts"><button class="btn ghost sm" id="rvGo" ${r.running?'disabled':''}>Write one now</button>
      <label class="check" style="font-size:13px"><input type="checkbox" id="rvAuto" ${P.settings.auto_review?'checked':''}> Write one automatically every week <span class="sub-dim">(uses your AI credit)</span></label></div>`;
  $('rvAuto').onchange=async e=>{P.settings=await api('/api/plan/settings',{method:'PUT',body:{auto_review:e.target.checked}});toast(e.target.checked?'Weekly review is on':'Weekly review is off');};
  $('rvGo').onclick=async()=>{P.review=await api('/api/plan/review',{method:'POST'});drawReview();poll();};
  function poll(){setTimeout(async()=>{if(currentPage!=='plan')return;const s=await api('/api/plan/review').catch(()=>null);if(!s)return;P.review=s;drawReview();if(s.running)poll();},4000);}
  if(r.running)poll();
}

/* ───────── cards page: foreign spend ───────── */
async function renderCardValue(){
  const host=$('cardValueArea');if(!host)return;
  host.innerHTML=`<div class="sec"><h2>Card value and foreign spend</h2><p>What each card really earns and costs, last 12 months</p></div><div class="chart-card"><div class="empty" style="padding:24px">Reading your statements…</div></div>`;
  let fx;try{fx=await api('/api/cards/fx?period='+getCardsPeriod());}catch(e){host.innerHTML='';return;}
  if(!$('cardValueArea'))return;
  host.innerHTML=`<div class="kpis">${kpi('Spent in foreign currency',amtc(fx.total.purchases_base),`${fx.transactions_in_foreign_currency} charges, ${fx.window}`)}
      ${kpi('Lost to exchange rates',amtc(fx.total.extra_cost_base),`${fx.total.effective_markup_pct??'-'}% above the mid-market rate${fx.total.refund_rate_loss_base>0?`, plus ${CUR} ${fmt(fx.total.refund_rate_loss_base)} lost on refunds`:''}`,fx.total.extra_cost_base>=300?'warn':'')}
      ${kpi('Cheapest card abroad',fx.cheapest_card?esc(fx.cheapest_card):'-',fx.cheapest_card?`Everything on it would have saved about ${CUR} ${fmt(fx.saving_if_all_on_cheapest_base)}`:'Not enough charges to compare')}
      ${kpi('Refunds at a worse rate',amtc(fx.total.refund_rate_loss_base),'Rate moved between the charge and the refund')}</div>
    <div class="two-col" style="margin-top:20px">
      <div class="chart-card"><h3>By currency <span class="sub-dim">what each cost vs the mid-market rate</span></h3>
        ${fx.by_currency.map(c=>`<div class="pl-wf-top" style="padding:9px 0;border-bottom:1px solid var(--line)"><span><b>${esc(c.currency)}</b> ${fmt(c.foreign_amount)}<div class="fine">${c.count} charge${c.count>1?'s':''}</div></span>
          <span style="text-align:right"><b>${CUR} ${fmt(c.purchases_base)}</b><div class="fine">${c.markup_pct==null?'-':c.markup_pct+'% above mid · '+CUR+' '+fmt(c.extra_base)}</div></span></div>`).join('')||'<div class="fine">No foreign-currency charges found.</div>'}</div>
      <div class="chart-card"><h3>By card <span class="sub-dim">lower is better</span></h3>
        ${fx.by_card.map(c=>`<div class="pl-wf-top" style="padding:9px 0;border-bottom:1px solid var(--line)"><span><b>${esc(c.card)}</b><div class="fine">${c.count} charges · ${CUR} ${fmt(c.purchases_base)}</div></span>
          <span style="text-align:right"><b ${c.card===fx.cheapest_card?'style="color:var(--pos)"':''}>${c.markup_pct}%</b><div class="fine">${CUR} ${fmt(c.extra_base)}${c.refund_cost_base>1?` + ${fmt(c.refund_cost_base)} on refunds`:''}</div></span></div>`).join('')}
        <div class="fine" style="margin-top:10px">No bank prints a separate fee line: the markup is inside the rate they applied.</div></div></div>
    ${fx.refunds.length?`<div class="chart-card" style="margin-top:20px"><h3>Refunds <span class="sub-dim">what came back vs what was charged</span></h3>
      ${fx.refunds.map(r=>`<div class="pl-wf-top" style="padding:8px 0;border-bottom:1px solid var(--line)"><span style="min-width:0"><span style="overflow-wrap:anywhere">${esc(r.description)}</span><div class="fine">${esc(r.date)} · ${esc(r.card)} · ${esc(r.currency)} ${fmt(r.foreign_amount)}</div></span>
        <span style="text-align:right"><b>${CUR} ${fmt(r.base_refunded)}</b>${r.lost_to_rates_base>1?`<div class="fine" style="color:var(--warn)">${CUR} ${fmt(r.lost_to_rates_base)} less than charged</div>`:''}</span></div>`).join('')}</div>`:''}
    <div class="chart-card" style="margin-top:20px"><h3>Largest foreign charges</h3>
      ${fx.transactions.slice(0,12).map(t=>`<div class="pl-wf-top" style="padding:8px 0;border-bottom:1px solid var(--line)"><span style="min-width:0"><span style="overflow-wrap:anywhere">${esc(t.description)}</span>
        <div class="fine">${esc(t.date)} · ${esc(t.card)} · ${esc(t.currency)} ${fmt2(Math.abs(t.foreign_amount))} @ ${t.rate}${t.mid_rate?` (mid ${t.mid_rate})`:''}</div></span>
        <span style="text-align:right"><b>${CUR} ${fmt(Math.abs(t.base))}${t.base<0?' back':''}</b>${t.markup_pct!=null&&t.base>0?`<div class="fine">${t.markup_pct}% above mid</div>`:''}</span></div>`).join('')}
      ${fx.billed_in_base_by_overseas_merchants.count?`<div class="fine" style="margin-top:10px">${fx.billed_in_base_by_overseas_merchants.count} more charges (${CUR} ${fmt(fx.billed_in_base_by_overseas_merchants.aed)}) were billed in your own currency by overseas-looking merchants. ${esc(fx.billed_in_base_by_overseas_merchants.note)}</div>`:''}
      <div class="fine" style="margin-top:10px">${esc(fx.how_measured)}</div>
      <div class="pl-acts"><button class="btn ghost sm" id="cvReparse">Re-read statements</button><span class="fine" id="cvReparseMsg"></span></div></div>
    <div class="pl-acts" style="margin-top:20px"><button class="btn solid sm" id="cvAbroad">Best way to pay abroad</button></div>`;
  host.onclick=async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.id==='cvReparse'){
      b.disabled=true;const m=$('cvReparseMsg');m.textContent='Reading your statements from Gmail…';
      await api('/api/cards/reparse',{method:'POST'});
      const tick=async()=>{const r=await api('/api/cards/reparse');
        if(r.running){setTimeout(tick,4000);return;}
        const x=r.report||{};m.textContent=x.ok?`Re-read ${x.updated} statements; ${x.foreign_added} foreign charges found.`:(x.error||'Could not read statements.');
        toast(x.ok?'Statements updated':'Failed');if(x.ok)renderCardValue();};
      tick();}
    else if(b.id==='cvAbroad')ask('I travel a lot. Using my foreign spend data, what is the cheapest way for me to pay abroad (cards, multi-currency accounts), and is it worth changing anything?');
  };
}

/* ───────── cards page: rewards ───────── */
const UNIT={cashback:'cashback'};
async function renderRewards(){
  const host=$('rewardsArea');if(!host)return;
  host.innerHTML=`<div class="chart-card"><div class="empty" style="padding:24px">Reading your rewards…</div></div>`;
  let d,us;try{[d,us]=await Promise.all([api('/api/cards/rewards?period='+getCardsPeriod()),api('/api/cards/usage?period='+getCardsPeriod())]);}catch(e){host.innerHTML='<div class="chart-card"><div class="empty">Could not read rewards.</div></div>';return;}
  if(!$('rewardsArea'))return;
  const r=d.report,rt=d.routing,val={};us.cards.forEach(c=>val[c.card_id]=c);
  const spend=r.cards.reduce((s,c)=>s+c.spend_12m,0),net=us.cards.reduce((s,c)=>s+c.net_value,0);
  const months=[...new Set(r.cards.flatMap(c=>c.per_statement.map(x=>x.date.slice(0,7))))].sort();
  host.innerHTML=`<div class="kpis">${kpi('Rewards earned',amtc(r.total_earned_12m),r.window+', cash value')}
      ${kpi('Effective rate',spend?(r.total_earned_12m/spend*100).toFixed(2)+'%':'-','Of the spend on cards with reward data')}
      ${kpi('Sitting unused',amtc(r.unredeemed),'Balances on the latest statements',r.unredeemed>=500?'warn':'')}
      ${kpi('Net value after costs',amtc(net),'Rewards minus fees and exchange-rate markup',net<0?'bad':'good')}</div>
    <div class="chart-card" style="margin-top:20px"><h3>Rewards per statement <span class="sub-dim">by card</span></h3><div id="rwChart" class="chart-mount"></div></div>
    <div class="sec"><h2>Card by card</h2><p>${esc(r.note)}</p></div>
    ${r.cards.map(c=>{const v=val[c.card_id]||{};return `<div class="chart-card rw-card" style="margin-bottom:16px">
      <div class="cv-top"><div><b>${esc(c.card)}</b><div class="fine">${esc(c.basis)}</div></div><button class="btn ghost sm" data-edit="${c.card_id}" data-name="${esc(c.card)}">Edit</button></div>
      <div class="cv-grid" style="margin:12px 0"><div><span>${esc(UNIT[c.unit]||c.unit)} earned</span><b>${fmt(c.earned_12m)}</b></div><div><span>Effective rate</span><b>${c.effective_rate_pct==null?'-':c.effective_rate_pct+'%'}</b><i>of ${CUR} ${fmt(c.spend_12m)}</i></div>
        ${c.balance!=null?`<div><span>Unused balance</span><b>${fmt(c.balance)}</b></div>`:''}<div><span>Fees</span><b>${fmt((v.annual_fee||0)+(v.interest_and_late_fees||0))}</b></div>
        <div><span>FX markup paid</span><b>${fmt(v.est_fx_cost||0)}</b></div><div class="cv-net"><span>Net value</span><b>${fmt(v.net_value||0)}</b></div></div>
      ${c.cap?`<div class="pl-wf-top"><span>${esc(c.cap.label)} ${c.cap.rate_pct}%${c.cap.needs?' with '+esc(c.cap.needs):''}</span><b>${c.cap.utilisation_pct}% of the cap used</b></div>
        <div class="pl-bar"><span style="width:${Math.min(100,c.cap.utilisation_pct)}%;${c.cap.utilisation_pct>=90?'background:var(--warn)':''}"></span></div>
        <div class="fine" style="margin-top:6px">You collect about ${CUR} ${fmt(c.cap.avg_reward_per_month)} a month; the most it can pay is ${CUR} ${fmt(c.cap.max_reward_per_month)}${c.cap.cap_spend?` (on ${CUR} ${fmt(c.cap.cap_spend)} of spend)`:''}.</div>`:''}
      ${(v.flags||[]).length?`<div class="fine" style="color:var(--warn);margin-top:8px">${v.flags.map(esc).join(' · ')}</div>`:''}
      <div class="rw-rules">${c.rules.length?c.rules.map(x=>`<span class="rw-chip">${esc(x.label)} ${x.rate_pct}%${x.cap?` · cap ${fmt(x.cap)}/${x.period||'month'}`:''}</span>`).join(''):'<span class="fine">No rules set, using what the statements show.</span>'}
        <button class="btn ghost sm" data-rules="${esc(c.card)}">Check these terms</button></div></div>`}).join('')}
    <div class="sec"><h2>Which card for what</h2><p>From each card's rules, net of its exchange-rate markup abroad</p></div>
    <div class="chart-card">${rt.rows.map(x=>`<div class="pl-wf-top" style="padding:10px 0;border-bottom:1px solid var(--line)"><span style="min-width:0"><b>${esc(x.use)}</b><div class="fine">${esc(x.note||'')}</div></span>
        <span style="text-align:right"><b>${esc(x.card)}</b><div class="fine">${x.rate_pct}% back</div></span></div>`).join('')}
      ${rt.abroad.length?`<h3 style="margin:18px 0 6px">Abroad, after the exchange-rate markup</h3>${rt.abroad.map(a=>`<div class="pl-wf-top" style="padding:8px 0;border-bottom:1px solid var(--line)"><span><b>${esc(a.card)}</b><div class="fine">${a.reward_pct}% back, ${a.markup_pct}% markup</div></span><b style="color:${a.net_pct>=0?'var(--pos)':'var(--neg)'}">${a.net_pct>0?'+':''}${a.net_pct}%</b></div>`).join('')}`:''}
      <div class="fine" style="margin-top:10px">${esc(rt.note)}</div></div>
    ${r.categories.length?`<div class="sec"><h2>Where your cards pay</h2><p>Cashback by category, last 12 months</p></div><div class="chart-card">
      ${r.categories.map(c=>`<div class="pl-wf-top" style="padding:8px 0;border-bottom:1px solid var(--line)"><span><b>${esc(c.label)}</b><div class="fine">${c.rate_pct}% on ${CUR} ${fmt(c.spend)}</div></span><b>${CUR} ${fmt(c.earned)}</b></div>`).join('')}</div>`:''}
    <div class="pl-acts" style="margin-top:20px"><button class="btn solid sm" id="rwFind">Find better cards</button><button class="btn ghost sm" id="rwReview">Review all my rewards</button></div>`;
  const series=r.cards.map(c=>({name:c.card,data:months.map(m=>c.per_statement.filter(p=>p.date.startsWith(m)).reduce((s,p)=>s+p.earned,0))})).filter(x=>x.data.some(v=>v));
  MiniChart.stackedColumns($('rwChart'),months.map(fmtMonth),series,{height:240});
  host.onclick=async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.id==='rwFind')ask('Review my cards using get_rewards, get_card_usage and get_fx_report, then search the current credit card market where I live for cards I should consider for my salary: no annual fee, strong rewards for how I spend, and anything my investing rules require. Tell me which cards to keep, which to cancel and which to apply for, with sources and eligibility.');
    else if(b.id==='rwReview')ask('Review my card rewards with get_rewards. Am I using each card where it pays best? Where am I leaving money on the table, and what should I change next month?');
    else if(b.dataset.rules)ask(`Check the reward rules I have for ${b.dataset.rules} (see get_rewards) against the card's current published terms. If they differ, propose a corrected card_info_set.`);
    else if(b.dataset.edit){
      const c=us.cards.find(x=>x.card_id==b.dataset.edit);
      const v=await dialog({title:b.dataset.name,text:'What this card really costs.',ok:'Save',fields:[
        {key:'annual_fee',label:'Annual fee ('+CUR+')',type:'number',value:c.annual_fee},{key:'benefits',label:'Benefits (short note)',value:c.benefits||''}]});
      if(!v)return;await api(`/api/cards/${b.dataset.edit}/info`,{method:'PUT',body:v});toast('Saved');renderRewards();}
  };
}

/* ───────── cards page: short list for the Overview tab ───────── */
async function renderCardInsights(){
  const host=$('cardInsights');if(!host)return;
  let r;try{r=await api('/api/plan/insights');}catch(e){return;}
  const keep=(r.insights||[]).filter(i=>/^(cap-|unredeemed|abroad-best|fx-cost|card-|rec-)/.test(i.id)).slice(0,4);
  if(!keep.length){host.innerHTML='';return;}
  host.innerHTML=`<div class="sec"><h2>Worth knowing</h2><p>From your statements</p></div><div class="chart-card">${keep.map(i=>`<div class="plan-item"><span class="plan-icon sev-ic-${i.severity}">${typeof sevIcon==='function'?sevIcon(i.severity):''}</span>
    <div style="flex:1;min-width:0"><p><b>${esc(i.title)}</b></p><div class="fine" style="margin-top:3px">${esc(i.body)}</div>
    <div class="pl-acts">${i.page==='cards'?'<button class="btn ghost sm" data-tab="rewards">See rewards</button>':''}${i.ask?`<button class="btn ghost sm" data-ask="${esc(i.ask)}">Ask the assistant</button>`:''}</div></div></div>`).join('')}</div>`;
  host.onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.ask)ask(b.dataset.ask);else if(b.dataset.tab&&window.showCardsTab)showCardsTab(b.dataset.tab);};
}

window.renderPlan=renderPlan;window.showPlanTab=showPlanTab;window.loadPlanInsights=loadPlanInsights;window.renderCardValue=renderCardValue;window.renderRewards=renderRewards;window.renderCardInsights=renderCardInsights;
})();
