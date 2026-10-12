/* Bank page: what moves through your bank accounts, read from statement e-mails or CSV files (backend/bank_tx.py). */
(function(){
const $=id=>document.getElementById(id);
const money=n=>CUR+' '+fmt(n);
const KIND={own:'Between my accounts / card bills',salary:'Salary',family:'Family support',education:'Education',cash:'Cash withdrawal',debit_spend:'Debit card purchase',to_person:'Sent to a person',from_person:'Received from a person',
  fee:'Bank fee',investing:'Investing transfer',investing_fee:'Investing transfer fee',interest:'Interest paid',reward:'Reward',other_in:'Other money in',other_out:'Other money out'};
const kpi=(l,v,m,t)=>`<div class="kpi ${t||''}"><div class="kpi-l">${l}</div><div class="kpi-v">${v}</div><div class="kpi-m">${m||''}</div></div>`;

async function importBankCsv(){
  const text=await pickCsv();if(!text)return;
  const r=await dialog({title:'Import a CSV file',text:'Any bank export with date, description and amount (or debit and credit) columns. Money out is recognised by the sign of the amounts.',ok:'Import',fields:[{key:'bank',label:'Account name',value:'My account'}]});
  if(!r)return;
  try{const d=await api('/api/bank/import-csv',{method:'POST',body:{text,bank:r.bank}});toast(`Read ${d.read} lines, ${d.added} new`);renderBank();}
  catch(e){let m='Could not read that file';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}
}

async function renderBank(){
  const el=$('page-bank');
  const head=`<div class="page-head"><div><div class="page-title">Bank accounts</div><div class="page-sub">Read from statement e-mails or CSV files</div></div>
    <div style="display:flex;gap:10px"><button class="btn ghost sm" id="bkCsv">Import CSV</button><button class="btn solid sm" id="bkSync">Read statements now</button></div></div>`;
  el.innerHTML=head+'<div class="chart-card"><div class="empty" style="padding:40px 20px">Reading your accounts…</div></div>';
  let d;try{d=await api('/api/bank/summary?months=12');}catch(e){el.innerHTML=head+`<div class="chart-card"><div class="empty">Could not read the bank data: ${esc(e.message)}</div></div>`;return;}
  if(currentPage!=='bank')return;
  $('page-bank').innerHTML=head+(d.count?body(d):`<div class="chart-card"><div class="empty" style="padding:40px 20px">No bank transactions yet.<br>Import a CSV file from your bank, or add the sender of your statement e-mails under <b>Settings &rarr; Bank accounts</b> and press "Read statements now".</div></div>`);
  $('bkCsv').onclick=importBankCsv;
  $('bkSync').onclick=async()=>{const b=$('bkSync');b.disabled=true;b.textContent='Reading statements… (about a minute)';
    try{const r=await api('/api/bank/sync?months=14',{method:'POST'});toast(r.ok?`Read ${r.read} lines, ${r.added} new`:(r.error||'Could not read'),r.ok?'ok':'err');}catch(e){toast('Could not read the statements','err');}
    renderBank();};
  if(d.count)draw(d);
}

function body(d){
  const fam=d.family,o=d.other,inv=d.investing,sal=d.series.map(s=>-(s.salary||0)).filter(v=>v>0),avgSal=sal.length?sal.reduce((a,b)=>a+b,0)/sal.length:0;
  const fees=inv.reduce((a,x)=>a+x.fees,0);
  return `<div class="kpis">
      ${kpi('Salary in',money(avgSal),'Average a month')}
      ${kpi('To family',money(fam.per_month)+' a month',`${fmt(fam.regular)} regular, ${fmt(fam.extras)} extra in ${fam.months} months`)}
      ${kpi('Cash and small',money(o.monthly)+' a month',`Cash ${fmt(o.by_kind.cash||0)}, people ${fmt(o.by_kind.to_person||0)}, debit ${fmt(o.by_kind.debit_spend||0)}, fees ${fmt(o.by_kind.fee||0)}`)}
    </div>
    <div class="chart-card" style="margin-top:20px"><h3>Money out by month</h3><div id="bkChart" class="chart-mount"></div></div>
    <div class="two-col" style="margin-top:20px">
      <div class="chart-card"><h3>To family</h3><div id="bkFam" class="chart-mount" style="min-height:170px"></div></div>
      <div class="chart-card"><h3>Investing transfers</h3>
        ${inv.length?inv.slice(-8).map(x=>`<div class="pl-wf-top" style="padding:7px 0;border-bottom:1px solid var(--line)"><span>${esc(x.date)} · ${esc(x.what)}</span><b>${money(x.amount)}${x.fees?` <span class="sub-dim">+${fmt(x.fees)} fees</span>`:''}</b></div>`).join('')+(fees>0?`<div class="fine" style="margin-top:8px">${money(fees)} in transfer fees so far.</div>`:''):'<div class="fine">No transfers found yet.</div>'}</div>
    </div>
    <details class="more" style="margin-top:20px" id="bkAll"><summary>All transactions</summary><div id="bkList"><div class="empty" style="padding:20px">Loading…</div></div></details>`;
}

function draw(d){
  const ms=d.series.map(s=>s.month),f=k=>d.series.map(s=>Math.max(0,s[k]||0));
  MiniChart.stackedColumns($('bkChart'),ms.map(fmtMonth),[{name:'Family support',data:f('family')},{name:'Education',data:f('education')},{name:'Investing',data:d.series.map(s=>Math.max(0,(s.investing||0)+(s.investing_fee||0)))},
    {name:'Cash and small',data:d.series.map(s=>Math.max(0,(s.cash||0)+(s.debit_spend||0)+(s.to_person||0)+(s.fee||0)+(s.other_out||0)))}],{height:240,colors:['--accent','--info','--pos','--warn'].map(v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim()||'#888')});
  MiniChart.bar($('bkFam'),d.family.by_month.map(m=>({label:fmtMonth(m.month),value:m.amount})),{highlight:'max',height:170});
  $('bkAll').addEventListener('toggle',async()=>{
    if(!$('bkAll').open||$('bkAll').dataset.done)return;$('bkAll').dataset.done=1;
    const t=await api('/api/bank/transactions?months=12&limit=120');
    $('bkList').innerHTML=`<div class="table-wrap" style="max-height:420px;overflow-y:auto"><table class="stmt-table"><tbody>${t.rows.map(r=>`<tr data-id="${r.id}"><td>${esc(r.txn_date)}</td><td>${esc(r.bank)}</td><td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.description)}">${esc(r.description)}</td>
      <td><select class="bk-kind">${Object.entries(KIND).map(([k,l])=>`<option value="${k}" ${k===r.kind?'selected':''}>${esc(l)}</option>`).join('')}</select></td><td class="num" style="color:${r.credit?'var(--pos)':'inherit'}">${r.credit?'+'+fmt2(r.credit):'−'+fmt2(r.debit)}</td></tr>`).join('')}</tbody></table></div>`;
  });
  $('page-bank').onchange=async e=>{
    const s=e.target.closest('.bk-kind');if(!s||!s.value)return;const id=s.closest('[data-id]').dataset.id;
    await api('/api/bank/tx/'+id,{method:'PUT',body:{kind:s.value}});toast('Saved');renderBank();};
}
window.renderBank=renderBank;
})();
