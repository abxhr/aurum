/* Locking, privacy blur and the Security settings. The server decides when a phone session is locked (two idle minutes); this file shows the lock screen and hides the
   page in the app switcher. */
(function(){
const $=id=>document.getElementById(id);
let remote=false,hiddenAt=0;
const stateP=fetch('/api/auth/state',{credentials:'same-origin'}).then(r=>r.json()).catch(()=>({}));
let demo=false;stateP.then(s=>{remote=!!s.remote&&!s.demo;demo=!!s.demo;});
const startDemo=async()=>{const r=await fetch('/api/demo/start',{method:'POST',credentials:'same-origin',headers:{'X-Wealth':'1'}});if(r.ok)location.href='/';};

function lockScreen(){
  if($('lockOv'))return;
  const d=document.createElement('div');d.id='lockOv';d.className='lock-ov';
  d.innerHTML=remote?`<div class="lock-box"><div class="lock-mark" style="background:none"><img src="/static/icons/logo.svg" alt="" width="48" height="48" style="border-radius:12px"></div><h2>Locked</h2><p>Use Face ID to continue.</p><button class="btn solid" id="lockBtn">Unlock with Face ID</button><div class="fine" id="lockErr"></div><button class="alt" id="lockDemo" type="button">Open the demo instead</button></div>`
    :`<div class="lock-box"><div class="lock-mark" style="background:none"><img src="/static/icons/logo.svg" alt="" width="48" height="48" style="border-radius:12px"></div><h2>Signed out</h2><button class="btn solid" id="lockBtn">Sign in</button></div>`;
  document.body.appendChild(d);
  if($('lockDemo'))$('lockDemo').onclick=startDemo;
  $('lockBtn').onclick=async()=>{
    if(!remote){location.href='/login';return;}
    try{await Passkey.login();location.reload();}catch(e){$('lockErr').textContent='Face ID did not unlock. Try again.';}
  };
}
window.onSessionEnded=err=>{if(err==='locked')lockScreen();else location.href='/login';};

document.addEventListener('visibilitychange',()=>{
  if(document.hidden){hiddenAt=Date.now();document.body.classList.add('privacy');return;}
  document.body.classList.remove('privacy');
  if(remote&&hiddenAt&&Date.now()-hiddenAt>60000){
    fetch('/api/session/lock',{method:'POST',credentials:'same-origin',headers:{'X-Wealth':'1'}}).finally(lockScreen);
    document.body.classList.add('privacy');
  }
});

/* ───────── Settings → Security ───────── */
const ago=t=>{if(!t)return 'never';const m=Math.round((Date.now()/1000-t)/60);return m<1?'just now':m<60?m+' min ago':m<1440?Math.round(m/60)+' h ago':Math.round(m/1440)+' days ago';};
const EV={password:'Password sign-in',passkey_login:'Face ID sign-in',passkey_register:'Face ID device added',password_changed:'Password changed',password_set:'Password set',enroll_link_made:'Link made to add a phone',
  remote_identity_refused:'Refused: wrong Tailscale identity',csrf_refused:'Refused: request from another site',default_password_removed:'Default password removed',signed_out_others:'Other devices signed out',
  passkey_removed:'Face ID device removed',passkey_register_options:'Face ID setup attempt'};
async function loadSecurity(){
  const box=$('secBox');if(!box)return;
  const st=await stateP;
  if(st.demo){box.innerHTML=`<div class="set-sub" style="margin:0 0 6px">Demo mode</div><div class="fine">You are looking at invented data. Nothing real is loaded.</div><div class="pw-row" style="margin-top:8px"><button class="btn solid sm" id="spExitDemo">Exit demo</button></div>`;
    $('spExitDemo').onclick=async()=>{await api('/api/logout',{method:'POST'});location.href='/login';};return;}
  let s;try{s=await api('/api/security/status');}catch(e){box.textContent='Unavailable.';return;}
  const local=!s.remote;
  box.innerHTML=`
    <div class="set-sub" style="margin:0 0 6px">Demo mode</div><div class="pw-row"><button class="btn ghost sm" id="spDemo">Show demo data</button></div><div class="fine">Swaps everything for invented data, for showing the app to others. Exit from Settings to return; you sign in again.</div>
    ${local?`<div class="form-grid"><label>Current password<input id="sp_cur" type="password" autocomplete="current-password"></label><label>New password (12+ characters)<input id="sp_new" type="password" autocomplete="new-password"></label></div>
      <div class="pw-row"><button class="btn ghost sm" id="spChange">Change password</button><span class="pw-msg" id="spMsg"></span></div>`:''}
    <div class="set-sub" style="margin:14px 0 6px">Face ID devices</div>
    ${s.passkeys.length?s.passkeys.map(p=>`<div class="budget-row"><span class="br-label">${esc(p.name)} <span class="sub-dim">added ${ago(p.created)}, used ${ago(p.last_used)}</span></span>${local?`<button class="btn ghost sm" data-rm="${esc(p.id)}">Remove</button>`:''}</div>`).join(''):'<div class="fine">None yet. Add your iPhone below.</div>'}
    ${local?`<div class="pw-row" style="margin-top:8px"><button class="btn ghost sm" id="spEnroll">Add an iPhone</button><span class="pw-msg" id="spEnrollMsg"></span></div><div id="spQr"></div>`:''}
    ${s.remote?`<div class="set-sub" style="margin:14px 0 6px">Phone alerts</div><div class="pw-row"><button class="btn ghost sm" id="spPush">Turn on alerts</button><button class="btn ghost sm" id="spPushOff" style="display:none">Turn off</button><span class="pw-msg" id="spPushMsg"></span></div>
      <div class="fine">Only new statements and rare market moments. On iPhone, first add this page to your Home Screen (Share, Add to Home Screen) and open it from there.</div>`:''}
    <div class="set-sub" style="margin:14px 0 6px">Signed-in devices</div>
    ${s.sessions.map(x=>`<div class="budget-row"><span class="br-label">${x.remote?'Phone (Face ID)':'This PC (password)'}${x.current?' <b>· this one</b>':''} <span class="sub-dim">active ${ago(x.last_seen)}</span></span></div>`).join('')}
    <div class="pw-row" style="margin-top:8px"><button class="btn ghost sm" id="spOthers">Sign out all other devices</button><button class="btn ghost sm" id="spLogout">Log out here</button></div>
    <div class="set-sub" style="margin:14px 0 6px">Recent activity</div>
    ${s.events.map(e=>`<div class="fine" style="color:${e.ok?'inherit':'var(--neg)'}">${ago(e.at)} · ${esc(EV[e.kind]||e.kind)}${e.ok?'':' · failed'}</div>`).join('')||'<div class="fine">Nothing yet.</div>'}
    <div class="fine" style="margin-top:10px">${s.remote?'You are on the phone: passwords are never accepted here.':s.public_host?`Phone address: ${esc(s.public_host)} (private to your Tailscale devices).`:'No private phone address is set.'}</div>`;
  if(local){
    $('spChange').onclick=async()=>{const m=$('spMsg');try{await api('/api/security/change-password',{method:'POST',body:{current:$('sp_cur').value,new:$('sp_new').value}});m.textContent='Changed. Other devices were signed out.';m.style.color='var(--pos)';$('sp_cur').value=$('sp_new').value='';}
      catch(e){let t='Could not change';try{t=JSON.parse(e.message).error||t;}catch{}m.textContent=t;m.style.color='var(--neg)';}};
    $('spEnroll').onclick=async()=>{const m=$('spEnrollMsg');m.textContent='';
      try{const r=await api('/api/security/enroll-link',{method:'POST'});
        $('spQr').innerHTML=`<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:8px"><div style="width:170px;background:#fff;padding:8px;border-radius:8px">${r.svg}</div>
          <div class="fine" style="flex:1;min-width:200px">Scan with your iPhone camera and open the link in Safari. It works once, for 10 minutes. Then confirm with Face ID.<br><span style="word-break:break-all">${esc(r.url.split('#')[0])}#…</span></div></div>`;
        const before=s.passkeys.length;const t=setInterval(async()=>{try{const n=await api('/api/security/status');if(n.passkeys.length>before){clearInterval(t);loadSecurity();}}catch(e){clearInterval(t);}},3000);setTimeout(()=>clearInterval(t),600000);
      }catch(e){let t='Could not make the link';try{t=JSON.parse(e.message).error||t;}catch{}m.textContent=t;}};
    box.querySelectorAll('[data-rm]').forEach(b=>b.onclick=async()=>{if(await askConfirm('This iPhone will no longer be able to open the app.',{title:'Remove Face ID device?',ok:'Remove'})){await api('/api/security/passkey/'+b.dataset.rm,{method:'DELETE'});loadSecurity();}});
  }
  $('spDemo').onclick=startDemo;
  if(s.remote)setupPush();
  $('spLogout').onclick=async()=>{await api('/api/logout',{method:'POST'});location.href='/login';};
  $('spOthers').onclick=async()=>{await api('/api/security/sign-out-others',{method:'POST'});toast('Other devices signed out');loadSecurity();};
}
function setupPush(){
  const m=$('spPushMsg'),on=$('spPush'),off=$('spPushOff'),say=t=>{m.textContent=t;};
  const standalone=window.navigator.standalone||matchMedia('(display-mode: standalone)').matches;
  const getReg=()=>Promise.race([navigator.serviceWorker.ready,new Promise((_,j)=>setTimeout(()=>j(new Error('the app is not ready, close it fully and reopen')),8000))]);
  const show=has=>{on.style.display=has?'none':'';off.style.display=has?'':'none';say(has?'Alerts are on for this phone.':'');};
  if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){say(standalone?'This iOS version cannot do alerts (needs 16.4 or newer).':'Open the app from the Home Screen icon, not Safari, to turn alerts on.');}
  else getReg().then(r=>r.pushManager.getSubscription()).then(x=>show(!!x)).catch(()=>{});
  on.onclick=async()=>{
    if(!('Notification' in window)){say(standalone?'This iOS version cannot do alerts.':'Add to Home Screen first, then open it from the icon.');return;}
    say('Asking for permission…');
    try{
      const perm=await Notification.requestPermission();            // first, so it counts as the tap
      if(perm!=='granted'){say('Blocked. Allow notifications for Aurum in iOS Settings → Notifications.');return;}
      say('Registering this phone…');
      const reg=await getReg(),k=(await api('/api/notify/key')).key,pad='='.repeat((4-k.length%4)%4),raw=atob((k+pad).replace(/-/g,'+').replace(/_/g,'/'));
      const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:Uint8Array.from(raw,c=>c.charCodeAt(0))});
      await api('/api/notify/subscribe',{method:'POST',body:{subscription:sub.toJSON(),name:'iPhone'}});
      const t=await api('/api/notify/test',{method:'POST'});show(true);if(!t.sent)say('Alerts are on, but the test did not go out. Try again in a minute.');
    }catch(e){say('Could not turn on alerts: '+String(e.message||e).slice(0,120));}
  };
  off.onclick=async()=>{try{const sub=await (await getReg()).pushManager.getSubscription();if(sub){await api('/api/notify/unsubscribe',{method:'POST',body:{endpoint:sub.endpoint}});await sub.unsubscribe();}show(false);}catch(e){say('Could not turn off: '+String(e.message||e).slice(0,100));}};
}
window.loadSecurity=loadSecurity;
})();
