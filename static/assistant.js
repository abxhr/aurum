/* assistant.js — the Assistant page: streaming chat with a tool-using Claude agent.
   Rich content arrives as ```ui:TYPE {json}``` blocks inside the reply and is rendered here with the app's chart toolkit. */
(function(){
const A={conv:null,convs:[],busy:false,ctrl:null,settings:{},profile:{},memories:[],key:{anthropic:'none',openai:'none'},models:{},omodels:{},sdk:{anthropic:true,openai:true},actions:{},uiId:0,rec:null,tab:'about'};
const $=id=>document.getElementById(id);
const esc2=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=u=>/^https:\/\/[^\s"'<>]+$/.test(String(u||''))?String(u):null;
const SUGGEST=[
  ['Plan a savings goal','Look at my goals and budget. Build me a month-by-month plan to reach my nearest goal on time, with the numbers.'],
  ['Where should my spare cash go?','Look at my balances, budget and goals. Where should my idle cash go right now, within my investing rules? Compare 3 options with numbers.'],
  ['Review my month','Review how I am doing this month: spending, income, savings. Anything I should fix? Show a chart.'],
  ['Compare my funds','Compare the funds I hold over 1 year. Show a chart and tell me which ones overlap or cost too much.'],
  ['Check my positions','Check my brokerage positions. Anything concentrated, risky or against my investing rules that I should look at?'],
  ['Cut my costs','Find the three recurring costs or fees in my cards and accounts that I could cut, and what each would save me a year.'],
];

/* ───────── markdown (small, safe) ───────── */
function inline(s){
  return s.replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/(^|[^*])\*([^*\s][^*]*)\*/g,'$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function md(src){
  const lines=esc2(src).split('\n'),out=[];let i=0;
  const isTable=l=>/^\s*\|.*\|\s*$/.test(l);
  while(i<lines.length){
    const l=lines[i];
    if(/^```/.test(l)){const buf=[];i++;while(i<lines.length&&!/^```/.test(lines[i]))buf.push(lines[i++]);i++;out.push('<pre><code>'+buf.join('\n')+'</code></pre>');continue;}
    if(/^\s*$/.test(l)){i++;continue;}
    let m;
    if((m=l.match(/^(#{1,4})\s+(.*)$/))){out.push(`<h${Math.min(m[1].length+2,6)}>${inline(m[2])}</h${Math.min(m[1].length+2,6)}>`);i++;continue;}
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(l)){out.push('<hr>');i++;continue;}
    if(/^&gt;\s?/.test(l)){const buf=[];while(i<lines.length&&/^&gt;\s?/.test(lines[i]))buf.push(lines[i++].replace(/^&gt;\s?/,''));out.push('<blockquote>'+inline(buf.join('<br>'))+'</blockquote>');continue;}
    if(isTable(l)&&i+1<lines.length&&/^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1])){
      const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
      const head=cells(l);i+=2;const rows=[];while(i<lines.length&&isTable(lines[i]))rows.push(cells(lines[i++]));
      out.push('<div class="ag-tablewrap"><table><thead><tr>'+head.map(h=>`<th>${inline(h)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>');continue;}
    if(/^\s*[-*]\s+/.test(l)){const buf=[];while(i<lines.length&&/^\s*[-*]\s+/.test(lines[i]))buf.push(inline(lines[i++].replace(/^\s*[-*]\s+/,'')));out.push('<ul>'+buf.map(b=>`<li>${b}</li>`).join('')+'</ul>');continue;}
    if(/^\s*\d+[.)]\s+/.test(l)){const buf=[];while(i<lines.length&&/^\s*\d+[.)]\s+/.test(lines[i]))buf.push(inline(lines[i++].replace(/^\s*\d+[.)]\s+/,'')));out.push('<ol>'+buf.map(b=>`<li>${b}</li>`).join('')+'</ol>');continue;}
    const buf=[];while(i<lines.length&&!/^\s*$/.test(lines[i])&&!/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+[.)]\s|&gt;)/.test(lines[i])&&!(isTable(lines[i])&&/^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1]||'')))buf.push(lines[i++]);
    if(!buf.length){buf.push(lines[i++]);}
    out.push('<p>'+inline(buf.join('<br>'))+'</p>');
  }
  return out.join('');
}

/* ───────── rich ui blocks ───────── */
const fmtV=(v,unit)=>{const n=Number(v);if(!isFinite(n))return esc2(v);return (unit?unit+' ':'')+(Math.abs(n)>=1000?MiniChart.fmt(n):(Math.round(n*100)/100).toString());};
function uiBlock(type,spec){
  const el=document.createElement('div');el.className='ag-ui ag-ui-'+type;
  try{
    if(type==='metrics'){
      el.innerHTML='<div class="ag-metrics">'+(spec.items||[]).slice(0,8).map(m=>`<div class="ag-metric ${['good','warn','bad'].includes(m.tone)?m.tone:''}"><div class="l">${esc2(m.label)}</div><div class="v">${esc2(m.value)}</div>${m.note?`<div class="n">${esc2(m.note)}</div>`:''}</div>`).join('')+'</div>';
    }else if(type==='chart'){
      const id='agc'+(++A.uiId);
      el.innerHTML=`<div class="ag-card">${spec.title?`<div class="ag-ct">${esc2(spec.title)}${spec.subtitle?` <span>${esc2(spec.subtitle)}</span>`:''}</div>`:''}<div id="${id}" class="chart-mount ag-chart"></div>${spec.note?`<div class="fine">${esc2(spec.note)}</div>`:''}</div>`;
      setTimeout(()=>{const c=$(id);if(!c)return;const u=spec.unit||'';const items=(spec.items||[]).map(i=>({label:String(i.label),value:Number(i.value)||0}));
        const t=spec.type||'bar';
        if(t==='line'||t==='area')MiniChart.lines(c,spec.labels||[],(spec.series||[]).map(s=>({name:s.name,data:(s.data||[]).map(Number)})),{unit:u});
        else if(t==='stacked')MiniChart.stackedColumns(c,spec.labels||[],(spec.series||[]).map(s=>({name:s.name,data:(s.data||[]).map(Number)})),{colors:MiniChart.pal(),height:260});
        else if(t==='rank')MiniChart.rankBars(c,items,{limit:12,format:v=>fmtV(v,u)});
        else if(t==='compose')MiniChart.stackBar(c,items,{format:v=>fmtV(v,u)});
        else MiniChart.bar(c,items,{highlight:'none',height:240});},30);
    }else if(type==='table'){
      el.innerHTML=`<div class="ag-card">${spec.title?`<div class="ag-ct">${esc2(spec.title)}</div>`:''}<div class="ag-tablewrap"><table><thead><tr>${(spec.columns||[]).map(c=>`<th>${esc2(c)}</th>`).join('')}</tr></thead><tbody>${(spec.rows||[]).slice(0,40).map(r=>`<tr>${(r||[]).map(c=>`<td>${inline(esc2(c))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
    }else if(type==='progress'){
      el.innerHTML='<div class="ag-card">'+(spec.items||[]).slice(0,8).map(p=>{const pct=Math.max(0,Math.min(100,(Number(p.current)/Math.max(Number(p.target),1))*100));
        return `<div class="ag-prog"><div class="top"><b>${esc2(p.label)}</b><span>${fmtV(p.current,p.unit)} of ${fmtV(p.target,p.unit)} · ${Math.round(pct)}%</span></div><div class="goal-bar-track"><div class="goal-bar-fill" style="width:${pct}%"></div></div>${p.note?`<div class="fine">${esc2(p.note)}</div>`:''}</div>`;}).join('')+'</div>';
    }else if(type==='links'){
      el.innerHTML='<div class="ag-links">'+(spec.items||[]).slice(0,8).map(l=>{const u=safeUrl(l.url);return u?`<a class="ag-link" href="${esc2(u)}" target="_blank" rel="noopener noreferrer"><svg class="i"><use href="#i-up"/></svg><span><b>${esc2(l.label)}</b>${l.note?`<small>${esc2(l.note)}</small>`:''}</span></a>`:'';}).join('')+'</div>';
    }else if(type==='actions'){
      el.innerHTML='<div class="ag-chips">'+(spec.items||[]).slice(0,6).map(a=>`<button class="btn ghost sm" data-page="${esc2(a.page)}">${esc2(a.label)}</button>`).join('')+'</div>';
      el.querySelectorAll('button').forEach(b=>b.onclick=()=>go(b.dataset.page));
    }else if(type==='callout'){
      el.innerHTML=`<div class="ag-callout ${['good','warn','bad'].includes(spec.tone)?spec.tone:''}">${spec.title?`<b>${esc2(spec.title)}</b> `:''}${esc2(spec.text)}</div>`;
    }else return null;
  }catch(e){return null;}
  return el;
}
/* Turn raw reply text into DOM: markdown segments + rendered ui blocks (cached so charts don't redraw while streaming) */
function renderRich(host,text,cache){
  const re=/```ui:(\w+)\n([\s\S]*?)```/g;let last=0,m;const frag=document.createDocumentFragment();
  const addMd=s=>{if(s.trim()){const d=document.createElement('div');d.className='ag-md';d.innerHTML=md(s);frag.appendChild(d);}};
  const open=text.match(/```ui:(\w+)\n(?![\s\S]*```)[\s\S]*$/);
  const body=open?text.slice(0,open.index):text;
  while((m=re.exec(body))){
    addMd(body.slice(last,m.index));
    const key=m[1]+m[2];let node=cache.get(key);
    if(!node){let spec=null;try{spec=JSON.parse(m[2]);}catch{}node=spec?uiBlock(m[1],spec):null;if(!node){node=document.createElement('div');node.className='ag-md';node.innerHTML=md('```\n'+m[2]+'\n```');}cache.set(key,node);}
    frag.appendChild(node);last=re.lastIndex;
  }
  addMd(body.slice(last));
  if(open){const sk=document.createElement('div');sk.className='ag-skel';sk.textContent=({chart:'Drawing chart…',table:'Building table…',metrics:'Calculating…'}[open[1]]||'Preparing…');frag.appendChild(sk);}
  host.replaceChildren(frag);
}
function plainText(t){return t.replace(/```ui:[\s\S]*?```/g,' ').replace(/[`*_#>|]/g,'').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/\s+/g,' ').trim();}

/* ───────── message DOM ───────── */
function userBubble(text){const d=document.createElement('div');d.className='ag-msg user';d.innerHTML=`<div class="ag-bubble">${esc2(text).replace(/\n/g,'<br>')}</div>`;return d;}
function newAssistant(){
  const root=document.createElement('div');root.className='ag-msg bot';
  root.innerHTML='<div class="ag-avatar"><svg class="i"><use href="#i-spark"/></svg></div><div class="ag-body"></div>';
  const body=root.querySelector('.ag-body');
  const S={root,body,cur:null,thinkEl:null,toolsEl:null,textBuf:'',textEl:null,cache:new Map(),tools:{},raf:0,thinkStart:0,plain:''};
  return S;
}
function ensureTools(S){if(!S.toolsEl){S.toolsEl=document.createElement('div');S.toolsEl.className='ag-tools';S.body.appendChild(S.toolsEl);}return S.toolsEl;}
function addThinking(S,d){
  if(!S.thinkEl){const det=document.createElement('details');det.className='ag-think';det.open=true;det.innerHTML='<summary><span class="dot"></span><span class="lbl">Thinking…</span></summary><div class="tx"></div>';S.body.appendChild(det);S.thinkEl=det;S.thinkStart=Date.now();S.toolsEl=null;}
  S.thinkEl.querySelector('.tx').textContent+=d;
}
function closeThinking(S){if(S.thinkEl){S.thinkEl.open=false;S.thinkEl.querySelector('.lbl').textContent='Reasoning · '+Math.max(1,Math.round((Date.now()-S.thinkStart)/1000))+'s';S.thinkEl.classList.add('done');S.thinkEl=null;}}
function addText(S,d){
  closeThinking(S);
  if(!S.textEl){S.textEl=document.createElement('div');S.textEl.className='ag-text';S.body.appendChild(S.textEl);S.textBuf='';S.cache=new Map();S.toolsEl=null;}
  S.textBuf+=d;S.plain+=d;
  if(!S.raf)S.raf=requestAnimationFrame(()=>{S.raf=0;if(!S.textEl)return;renderRich(S.textEl,S.textBuf,S.cache);scrollDown();});
}
function endText(S){if(S.raf){cancelAnimationFrame(S.raf);S.raf=0;}if(S.textEl){renderRich(S.textEl,S.textBuf,S.cache);S.textEl=null;}}
function toolChip(S,id,label,state,summary){
  const box=ensureTools(S);let c=S.tools[id];
  if(!c){c=document.createElement('span');c.className='ag-tool';c.innerHTML='<i></i><span></span>';box.appendChild(c);S.tools[id]=c;}
  c.className='ag-tool '+state;c.querySelector('span').textContent=label;if(summary)c.title=summary;
}
function actionCard(a){
  const d=document.createElement('div');d.className='ag-action '+a.status;d.dataset.id=a.id;
  const draw=()=>{
    d.className='ag-action '+a.status;
    d.innerHTML=`<div class="ah"><b>${esc2(a.title)}</b><span class="st">${{pending:'Waiting for you',applied:'Applied',dismissed:'Dismissed',failed:'Failed'}[a.status]||a.status}</span></div>
      ${(a.details||[]).length?`<ul>${a.details.map(x=>`<li>${esc2(x)}</li>`).join('')}</ul>`:''}
      ${a.status==='failed'&&a.result?`<div class="fine" style="color:var(--neg)">${esc2(a.result)}</div>`:''}
      ${a.status==='pending'?`<div class="ab"><button class="btn solid sm" data-v="apply">Apply</button><button class="btn ghost sm" data-v="dismiss">Dismiss</button></div>`:''}`;
    d.querySelectorAll('button').forEach(b=>b.onclick=async()=>{
      b.disabled=true;
      try{const r=await api(`/api/agent/actions/${a.id}/${b.dataset.v}`,{method:'POST'});
        a.status=b.dataset.v==='apply'?(r.ok?'applied':'failed'):'dismissed';a.result=r.message||'';
        if(b.dataset.v==='apply'){toast(r.ok?'Done':(r.message||'Could not apply'),r.ok?'ok':'err');invalidateAppData();}
      }catch{toast('That did not work','err');}
      draw();});
  };
  draw();return d;
}
function invalidateAppData(){try{summary=null;_cardsTxnCache=null;SPEND_CATEGORIES=null;}catch{}}
function chip(kind,o){
  const d=document.createElement('div');d.className='ag-chips';
  if(kind==='nav'){const b=document.createElement('button');b.className='btn ghost sm';b.innerHTML=`<svg class="i"><use href="#i-up"/></svg>Open ${esc2(o.label||o.page)}`;b.onclick=()=>go(o.page);d.appendChild(b);}
  else if(kind==='link'){const u=safeUrl(o.url);if(!u)return null;const a=document.createElement('a');a.className='btn ghost sm';a.href=u;a.target='_blank';a.rel='noopener noreferrer';a.innerHTML=`<svg class="i"><use href="#i-up"/></svg>${esc2(o.label)}`;if(o.note)a.title=o.note;d.appendChild(a);}
  else if(kind==='memory'){d.innerHTML=`<span class="ag-mem"><svg class="i"><use href="#i-spark"/></svg>Remembered: ${esc2(o.text)}</span>`;}
  return d;
}
function scrollDown(force){const s=$('agScroll');if(!s)return;if(force||s.scrollHeight-s.scrollTop-s.clientHeight<160)s.scrollTop=s.scrollHeight;}

/* replay a saved turn */
function renderTurn(turn,actions){
  const frag=document.createDocumentFragment();frag.appendChild(userBubble(turn.user));
  const S=newAssistant();frag.appendChild(S.root);
  for(const p of turn.parts||[]){
    if(p.k==='thinking'){addThinking(S,p.text);closeThinking(S);S.thinkStart=Date.now();}
    else if(p.k==='text'){S.textEl=null;addText(S,p.text);endText(S);}
    else if(p.k==='tool'){toolChip(S,p.id,p.label,p.ok?'ok':'err',p.summary);}
    else if(p.k==='action'){const a=actions[p.id];if(a)S.body.appendChild(actionCard(a));}
    else if(p.k==='nav'||p.k==='link'||p.k==='memory'){const c=chip(p.k,p);if(c)S.body.appendChild(c);}
  }
  if(S.thinkEl)closeThinking(S);
  if(turn.complete===false){const n=document.createElement('div');n.className='fine';n.textContent='This reply was interrupted.';S.body.appendChild(n);}
  return frag;
}

/* ───────── streaming ───────── */
function setBusy(b){A.busy=b;const btn=$('agSend');if(!btn)return;btn.innerHTML=b?'<svg class="i"><use href="#i-close"/></svg>':'<svg class="i"><use href="#i-up"/></svg>';btn.title=b?'Stop':'Send';btn.classList.toggle('stop',b);}
async function send(text){
  text=(text||'').trim();if(!text||A.busy)return;
  const msgs=$('agMsgs');if(!msgs)return;
  $('agEmpty')?.remove();
  msgs.appendChild(userBubble(text));
  const S=newAssistant();msgs.appendChild(S.root);scrollDown(true);
  $('agInput').value='';autoGrow();setBusy(true);
  A.ctrl=new AbortController();let autoNav=null;
  try{
    const r=await fetch('/api/agent/chat',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Wealth':'1'},body:JSON.stringify({conv_id:A.conv,text}),signal:A.ctrl.signal});
    if(!r.ok||!r.body)throw new Error('chat failed');
    const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){
      const {value,done}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n\n'))>=0){
        const raw=buf.slice(0,i);buf=buf.slice(i+2);
        if(!raw.startsWith('data: '))continue;
        let ev;try{ev=JSON.parse(raw.slice(6));}catch{continue;}
        switch(ev.t){
          case 'start':A.conv=ev.conv_id;if(ev.title){A.convs.unshift({id:ev.conv_id,title:ev.title});drawList();$('agTitle').textContent=ev.title;}break;
          case 'thinking':addThinking(S,ev.d);scrollDown();break;
          case 'text':addText(S,ev.d);break;
          case 'tool':endText(S);closeThinking(S);toolChip(S,ev.id,ev.label+'…','run');break;
          case 'tool_done':toolChip(S,ev.id,S.tools[ev.id]?.querySelector('span').textContent.replace('…',''),ev.ok?'ok':'err',ev.summary);break;
          case 'action':{endText(S);A.actions[ev.action.id]=ev.action;S.body.appendChild(actionCard(ev.action));S.toolsEl=null;scrollDown();break;}
          case 'nav':{endText(S);const c=chip('nav',ev);if(c)S.body.appendChild(c);if(ev.auto)autoNav=ev.page;break;}
          case 'link':{endText(S);const c=chip('link',ev);if(c)S.body.appendChild(c);break;}
          case 'memory':{const c=chip('memory',ev);if(c)S.body.appendChild(c);loadState(false);break;}
          case 'refresh':invalidateAppData();break;
          case 'error':{endText(S);closeThinking(S);const e=document.createElement('div');e.className='ag-error';e.textContent=ev.message;S.body.appendChild(e);if(ev.code==='no_key'||ev.code==='bad_key')openSettings('model');break;}
          case 'done':break;
        }
      }
    }
  }catch(e){
    if(e.name!=='AbortError'){const er=document.createElement('div');er.className='ag-error';er.textContent='Connection lost. Try again.';S.body.appendChild(er);}
  }finally{
    endText(S);closeThinking(S);setBusy(false);A.ctrl=null;
    if(A.settings.speak_replies&&S.plain&&'speechSynthesis' in window){try{speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(plainText(S.plain).slice(0,1500)));}catch{}}
    loadState(false);scrollDown();
    if(autoNav&&document.getElementById('page-assistant')&&!document.getElementById('page-assistant').classList.contains('hidden'))go(autoNav);
  }
}

/* ───────── page ───────── */
function autoGrow(){const t=$('agInput');if(!t)return;t.style.height='auto';t.style.height=Math.min(t.scrollHeight,180)+'px';}
function drawList(){
  const l=$('agList');if(!l)return;
  l.innerHTML=A.convs.length?A.convs.map(c=>`<div class="ag-conv ${c.id===A.conv?'active':''}" data-id="${c.id}"><span>${esc2(c.title||'New chat')}</span><button title="Delete chat" aria-label="Delete chat"><svg class="i"><use href="#i-close"/></svg></button></div>`).join(''):'<div class="sub-dim" style="padding:8px 12px">No chats yet.</div>';
  l.querySelectorAll('.ag-conv').forEach(el=>{
    el.onclick=()=>openConv(+el.dataset.id);
    el.querySelector('button').onclick=async e=>{e.stopPropagation();if(!await askConfirm('This chat will be deleted. Saved memories stay.',{title:'Delete chat?',ok:'Delete'}))return;
      await api('/api/agent/conversations/'+el.dataset.id,{method:'DELETE'});if(A.conv===+el.dataset.id)newChat();await loadState(true);};
  });
}
function emptyState(){
  const d=document.createElement('div');d.id='agEmpty';d.className='ag-empty';
  const nm=(A.profile.name||'').trim();
  d.innerHTML=`<div class="ag-hello"><div class="mark"><svg class="i"><use href="#i-spark"/></svg></div><h2>${nm?`Hi ${esc2(nm)}.`:'Hi.'} What do you want to work out?</h2>
    <p>I can read all your accounts, cards, budget, payslips, IBKR and eToro, check live markets, build plans with charts, and make changes in the app when you approve them.</p></div>
    <div class="ag-suggest">${SUGGEST.map((s,i)=>`<button data-i="${i}"><b>${esc2(s[0])}</b><span>${esc2(s[1])}</span></button>`).join('')}</div>
    ${A.key[A.settings.provider||'anthropic']==='none'?`<div class="ag-error" style="margin-top:18px">I need an API key to think. <a href="#" id="agKeyLink">Add it in AI settings</a>.</div>`:''}`;
  d.querySelectorAll('.ag-suggest button').forEach(b=>b.onclick=()=>send(SUGGEST[+b.dataset.i][1]));
  d.querySelector('#agKeyLink')?.addEventListener('click',e=>{e.preventDefault();openSettings('model');});
  return d;
}
function newChat(){A.conv=null;const m=$('agMsgs');if(m){m.replaceChildren(emptyState());}$('agTitle')&&($('agTitle').textContent='New chat');drawList();$('agInput')?.focus();}
async function openConv(id){
  if(A.busy)return;
  const c=await api('/api/agent/conversations/'+id).catch(()=>null);if(!c)return;
  A.conv=id;Object.assign(A.actions,c.actions);
  const m=$('agMsgs');m.replaceChildren();c.turns.forEach(t=>m.appendChild(renderTurn(t,c.actions)));
  $('agTitle').textContent=c.title;drawList();scrollDown(true);
}
async function loadState(redraw){
  const s=await api('/api/agent/state').catch(()=>null);if(!s)return;
  Object.assign(A,{settings:s.settings,profile:s.profile,memories:s.memories,convs:s.conversations,key:s.key,models:s.models,omodels:s.openai_models||{},sdk:s.sdk});
  if(redraw!==false||true)drawList();
  const sp=$('agSpeak');if(sp)sp.classList.toggle('on',!!A.settings.speak_replies);
}
async function renderAssistantPage(){
  const el=$('page-assistant');
  el.innerHTML=`<div class="ag">
    <aside class="ag-side"><button class="btn solid" id="agNew">New chat</button><div class="ag-list" id="agList"></div>
      <button class="btn ghost sm" id="agSet" style="margin-top:auto">AI settings</button></aside>
    <section class="ag-main">
      <header class="ag-head"><button class="btn ghost sm ag-chats-btn" id="agChats">Chats</button><div id="agTitle">New chat</div><div class="ag-tools2"><button class="btn ghost sm" id="agSpeak" title="Read replies aloud">Read aloud</button><button class="btn ghost sm" id="agSet2">AI settings</button></div></header>
      <div class="ag-scroll" id="agScroll"><div id="agMsgs"></div></div>
      <div class="ag-composer"><button class="btn ghost" id="agMic" title="Speak">Speak</button>
        <textarea id="agInput" rows="1" placeholder="Ask anything about your money, or tell me what to change…" aria-label="Message"></textarea>
        <button class="btn solid" id="agSend" title="Send"><svg class="i"><use href="#i-up"/></svg></button></div>
    </section></div>`;
  $('agNew').onclick=newChat;$('agChats').onclick=()=>el.firstElementChild.classList.toggle('side-open');$('agList').addEventListener('click',()=>el.firstElementChild.classList.remove('side-open'));$('agNew').addEventListener('click',()=>el.firstElementChild.classList.remove('side-open'));el.querySelector('.ag-main').addEventListener('click',()=>el.firstElementChild.classList.remove('side-open'));$('agSet').onclick=$('agSet2').onclick=()=>openSettings('about');
  $('agSend').onclick=()=>A.busy?A.ctrl?.abort():send($('agInput').value);
  $('agInput').addEventListener('input',autoGrow);
  $('agInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send($('agInput').value);}});
  $('agSpeak').onclick=async()=>{const on=!A.settings.speak_replies;await api('/api/agent/settings',{method:'PUT',body:{speak_replies:on}});A.settings.speak_replies=on;$('agSpeak').classList.toggle('on',on);if(!on&&'speechSynthesis' in window)speechSynthesis.cancel();};
  setupMic();
  await loadState();
  setBusy(false);
  if(A.pending){const t=A.pending;A.pending=null;send(t);}
  if(A.conv&&A.convs.some(c=>c.id===A.conv))await openConv(A.conv);else newChat();
}

/* ───────── voice input ───────── */
function setupMic(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition,b=$('agMic');
  if(!SR){b.disabled=true;b.title='Voice input is not supported in this browser (try Chrome or Edge)';return;}
  b.onclick=()=>{
    if(A.rec){A.rec.stop();return;}
    const r=new SR();r.lang='en-US';r.interimResults=true;r.continuous=false;A.rec=r;let finalText='';
    b.classList.add('on');b.textContent='Listening…';
    r.onresult=e=>{let t='';for(const x of e.results){t+=x[0].transcript;if(x.isFinal)finalText=t;}$('agInput').value=t;autoGrow();};
    r.onerror=()=>{};
    r.onend=()=>{A.rec=null;b.classList.remove('on');b.textContent='Speak';if(finalText.trim())send(finalText);};
    try{r.start();}catch{A.rec=null;b.classList.remove('on');b.textContent='Speak';}
  };
}

/* ───────── AI settings ───────── */
function openSettings(tab){
  A.tab=tab||A.tab;A.draftProvider=null;let ov=$('agSettings');
  if(!ov){ov=document.createElement('div');ov.id='agSettings';ov.className='modal-overlay';document.body.appendChild(ov);}
  drawSettings();ov.classList.add('show');
  ov.onclick=e=>{if(e.target===ov)ov.classList.remove('show');};
}
const field=(id,label,val,type='text',ph='')=>`<label>${label}<input id="${id}" type="${type}" value="${esc2(val)}" placeholder="${esc2(ph)}"></label>`;
const area=(id,label,val,rows=3)=>`<label class="full">${label}<textarea id="${id}" rows="${rows}">${esc2(val)}</textarea></label>`;
function drawSettings(){
  const ov=$('agSettings'),p=A.profile||{},s=A.settings||{};
  const tabs=[['about','About you'],['memory','Memory'],['model','Model and behaviour'],['data','Chats and data']];
  let body='';
  if(A.tab==='about'){
    body=`<div class="fine" style="margin:0 0 10px">The assistant reads this every time. Keep it true; it is what makes the advice personal.</div>
      <div class="form-grid">${field('p_name','Name',p.name)}${field('p_age','Age',p.age,'number')}${field('p_nationality','Nationality',p.nationality)}${field('p_residency','Residency',p.residency)}${field('p_employer','Employer / work',p.employer)}
      <label>Comfort with risk<select id="p_risk">${['','conservative','moderate','aggressive'].map(r=>`<option value="${r}" ${p.risk===r?'selected':''}>${r||'not set'}</option>`).join('')}</select></label>
      <div class="full"><div class="fine" style="margin:6px 0">Goals the assistant should plan around</div><div id="p_goals">${(p.goals||[]).map(goalRow).join('')}</div><button class="btn ghost sm" id="p_addgoal" style="margin-top:6px">Add goal</button></div>
      ${area('p_rules','Investing rules (hard constraints)',p.investing_rules)}${area('p_other','Other countries (accounts, tax ties)',p.other_countries)}${area('p_style','How you like answers',p.style,2)}${area('p_notes','Anything else it should know',p.notes,3)}</div>
      <div class="modal-actions"><div style="flex:1"></div><button class="btn solid" id="p_save">Save</button></div>`;
  }else if(A.tab==='memory'){
    body=`<div class="fine" style="margin:0 0 10px">Things the assistant remembered across chats. Delete anything that is wrong or no longer true.</div>
      <div id="memList">${A.memories.length?A.memories.map(m=>`<div class="budget-row"><span class="br-label">${esc2(m.text)}</span><button class="btn ghost sm" data-mid="${m.id}">Delete</button></div>`).join(''):'<div class="sub-dim">Nothing saved yet.</div>'}</div>
      <div class="form-grid" style="margin-top:14px"><label class="full">Add a memory<input id="m_new" type="text" maxlength="240" placeholder="e.g. I prefer monthly automated transfers"></label></div>
      <div class="modal-actions"><button class="btn danger" id="m_clear">Delete all memories</button><div style="flex:1"></div><button class="btn solid" id="m_add">Add</button></div>`;
  }else if(A.tab==='model'){
    const prov=A.draftProvider||s.provider||'anthropic',isO=prov==='openai',name=isO?'OpenAI':'Anthropic (Claude)';
    const ks={saved:'A key is saved in this app.',environment:isO?'Using the key from your .env / OPENAI_API_KEY.':'Using the ANTHROPIC_API_KEY environment variable.',none:'No key yet. The assistant cannot think without one.'}[A.key[prov]];
    const models=isO?A.omodels:A.models,cur=isO?(s.openai_model||'gpt-4.1'):(s.model||'claude-opus-5-5'),known=Object.keys(models).includes(cur);
    body=`<div class="form-grid"><label class="full">Provider<select id="k_prov"><option value="anthropic" ${!isO?'selected':''}>Anthropic: Claude</option><option value="openai" ${isO?'selected':''}>OpenAI: GPT</option></select></label></div>
      <div class="email-status-box" style="margin-top:12px"><div class="es-row"><span class="es-label">${name} API key</span><span class="es-val" style="color:${A.key[prov]==='none'?'var(--neg)':'var(--pos)'}">${ks}</span></div></div>
      <div class="form-grid">${field('k_key',name+' API key','','password',A.key[prov]==='saved'?'Saved. Paste a new one to replace it':(isO?'sk-…':'sk-ant-…'))}
      ${field('k_jev','Jev (TypeSafe) key, optional','','password',A.key.typesafe==='saved'?'Saved. Paste a new one to replace it':'Cheapest way to sort spending')}
      <label>Model<select id="k_model">${Object.entries(models).map(([k,v])=>`<option value="${k}" ${cur===k?'selected':''}>${esc2(v)}</option>`).join('')}${isO?`<option value="__custom__" ${known?'':'selected'}>Other model id…</option>`:''}</select></label>
      ${isO?`<label id="k_custom_wrap" style="${known?'display:none':''}">Model id<input id="k_custom" type="text" value="${known?'':esc2(cur)}" placeholder="e.g. gpt-5.1"></label>`:''}
      <label>${isO?'Reasoning effort (reasoning models)':'Thinking effort'}<select id="k_effort">${['low','medium','high'].map(e=>`<option ${s.effort===e?'selected':''}>${e}</option>`).join('')}</select></label>
      <label>Changes to your data<select id="k_auto"><option value="confirm" ${s.autonomy!=='auto'?'selected':''}>Ask me first (recommended)</option><option value="auto" ${s.autonomy==='auto'?'selected':''}>Apply automatically</option></select></label>
      <label class="check full"><input id="k_think" type="checkbox" ${s.show_thinking?'checked':''}> Show the assistant's reasoning while it works ${isO?'(OpenAI does not share reasoning text)':''}</label>
      <label class="check full"><input id="k_speak" type="checkbox" ${s.speak_replies?'checked':''}> Read replies aloud</label></div>
      <div class="fine">${isO?'Keys at platform.openai.com. ':'Keys at console.anthropic.com. '}The key stays on this computer and is never sent back to the browser, and usage is billed to your own account. Spending categories are sorted with OpenAI's Decisions API on your OpenAI key (fractions of a cent per merchant), or with Jev if you add a key. You can switch provider or model at any time, even in the middle of a chat. ${A.sdk[prov]?'':`<b style="color:var(--neg)">The ${isO?'openai':'anthropic'} Python package is missing: run pip install ${isO?'openai':'anthropic'}.</b>`}</div>
      <div class="modal-actions"><button class="btn ghost" id="k_test">Test connection</button><div style="flex:1"></div><button class="btn solid" id="k_save">Save</button></div><div class="fine" id="k_msg"></div>`;
  }else{
    body=`<div class="fine" style="margin:0 0 12px">Chats are stored only in this app's database on your computer.</div>
      <div class="modal-actions" style="margin-top:0"><button class="btn danger" id="d_chats">Delete all chats</button></div>`;
  }
  ov.innerHTML=`<div class="modal" style="max-width:680px"><h3>AI settings</h3><div class="tabbar" id="agTabs" style="margin:12px 0 4px">${tabs.map(([k,l])=>`<button class="${A.tab===k?'active':''}" data-k="${k}">${l}</button>`).join('')}</div>${body}
    <div class="modal-actions" style="margin-top:12px"><div style="flex:1"></div><button class="btn ghost" id="agClose">Close</button></div></div>`;
  ov.querySelectorAll('#agTabs button').forEach(b=>b.onclick=()=>{A.tab=b.dataset.k;drawSettings();});
  $('agClose').onclick=()=>ov.classList.remove('show');
  bindSettings();
}
const goalRow=g=>`<div class="ag-goalrow"><input data-f="name" value="${esc2(g.name)}" placeholder="Goal"><input data-f="amount" type="number" value="${esc2(g.amount)}" placeholder="Amount"><input data-f="target_date" type="date" value="${esc2(g.target_date)}"><input data-f="notes" value="${esc2(g.notes||'')}" placeholder="Notes"><button class="btn ghost sm" data-rm aria-label="Remove goal">✕</button></div>`;
function bindSettings(){
  const q=id=>$(id);
  if(A.tab==='about'){
    q('p_addgoal').onclick=()=>{q('p_goals').insertAdjacentHTML('beforeend',goalRow({name:'',amount:'',target_date:''}));bindGoalRm();};bindGoalRm();
    q('p_save').onclick=async()=>{
      const goals=[...document.querySelectorAll('#p_goals .ag-goalrow')].map(r=>({name:r.querySelector('[data-f=name]').value.trim(),amount:+r.querySelector('[data-f=amount]').value||0,currency:CUR,target_date:r.querySelector('[data-f=target_date]').value,notes:r.querySelector('[data-f=notes]').value.trim()})).filter(g=>g.name);
      A.profile=await api('/api/agent/profile',{method:'PUT',body:{name:q('p_name').value,age:+q('p_age').value||null,nationality:q('p_nationality').value,residency:q('p_residency').value,employer:q('p_employer').value,risk:q('p_risk').value,goals,investing_rules:q('p_rules').value,other_countries:q('p_other').value,style:q('p_style').value,notes:q('p_notes').value}});
      toast('Saved. The assistant will use this from the next message.');};
  }else if(A.tab==='memory'){
    document.querySelectorAll('#memList [data-mid]').forEach(b=>b.onclick=async()=>{await api('/api/agent/memories/'+b.dataset.mid,{method:'DELETE'});await loadState();drawSettings();});
    q('m_add').onclick=async()=>{const t=q('m_new').value.trim();if(!t)return;try{await api('/api/agent/memories',{method:'POST',body:{text:t}});await loadState();drawSettings();}catch(e){let m='Could not save';try{m=JSON.parse(e.message).error||m;}catch{}toast(m,'err');}};
    q('m_clear').onclick=async()=>{if(!await askConfirm('Everything the assistant remembered will be deleted.',{title:'Delete all memories?',ok:'Delete'}))return;await api('/api/agent/memories',{method:'DELETE'});await loadState();drawSettings();};
  }else if(A.tab==='model'){
    const prov=A.draftProvider||A.settings.provider||'anthropic',isO=prov==='openai';
    q('k_prov').onchange=()=>{A.draftProvider=q('k_prov').value;drawSettings();};
    if(isO)q('k_model').onchange=()=>{q('k_custom_wrap').style.display=q('k_model').value==='__custom__'?'':'none';};
    const save=async()=>{
      const body={provider:prov,effort:q('k_effort').value,autonomy:q('k_auto').value,show_thinking:q('k_think').checked,speak_replies:q('k_speak').checked};
      if(isO)body.openai_model=q('k_model').value==='__custom__'?q('k_custom').value.trim():q('k_model').value;else body.model=q('k_model').value;
      if(q('k_key').value.trim())body[prov+'_key']=q('k_key').value.trim();
      if(q('k_jev')&&q('k_jev').value.trim())body.typesafe_key=q('k_jev').value.trim();
      const r=await api('/api/agent/settings',{method:'PUT',body});A.settings=r.settings;A.key=r.key;A.draftProvider=null;$('agSpeak')?.classList.toggle('on',!!A.settings.speak_replies);return r;};
    q('k_save').onclick=async()=>{await save();toast('Saved');drawSettings();};
    q('k_test').onclick=async()=>{const m=q('k_msg');m.style.color='';m.textContent='Testing…';try{await save();const r=await api('/api/agent/test',{method:'POST'});m.style.color='var(--pos)';m.textContent=`Connected to ${r.provider==='openai'?'OpenAI':'Claude'}. ${r.model} replied "${r.reply}".`;A.key=r.key;}catch(e){let msg='Connection failed';try{msg=JSON.parse(e.message).error||msg;}catch{}m.style.color='var(--neg)';m.textContent=msg;}};
  }else{
    q('d_chats').onclick=async()=>{if(!await askConfirm('Every chat will be deleted. Memories and profile stay.',{title:'Delete all chats?',ok:'Delete all'}))return;await api('/api/agent/conversations',{method:'DELETE'});newChat();await loadState();toast('Chats deleted');};
  }
}
function bindGoalRm(){document.querySelectorAll('#p_goals [data-rm]').forEach(b=>b.onclick=()=>b.closest('.ag-goalrow').remove());}

window.askAssistant=t=>{A.pending=t;go('assistant');};
window.renderAssistant=renderAssistantPage;
window.__ag={md,renderRich,uiBlock};
const fab=document.getElementById('assistantFab');if(fab)fab.onclick=()=>go('assistant');
window.openAssistant=()=>go('assistant');
})();
