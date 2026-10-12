(async function(){
const $=id=>document.getElementById(id),err=$('err');
const send=async(path,body)=>{const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Wealth':'1'},body:JSON.stringify(body||{})});return [r,await r.json().catch(()=>({}))];};
let s;try{s=await (await fetch('/api/auth/state',{credentials:'same-origin'})).json();}catch(e){err.textContent='Could not reach the app.';return;}
$('demoBtn').onclick=async()=>{const [r]=await send('/api/demo/start').catch(()=>[{ok:false}]);if(r.ok)location.href='/';else err.textContent='Could not open the demo.';};
if(s.signed_in){location.href='/';return;}
if(s.setup_needed){location.href='/setup';return;}
if(s.remote){
  $('sub').textContent=s.passkeys?'Use Face ID to open your ledger.':'No Face ID device is set up yet. Add this phone from Settings → Security on your PC.';
  if(s.passkeys){$('pkBox').hidden=false;
    $('pkBtn').onclick=async()=>{err.textContent='';$('pkBtn').disabled=true;
      try{await Passkey.login();location.href='/';}catch(e){err.textContent='Face ID did not unlock. Try again.';$('pkBtn').disabled=false;}};}
}else{
  $('sub').textContent='Enter your password to open your ledger.';$('pwBox').hidden=false;$('pw').focus();
  const go=async()=>{err.textContent='';$('btn').disabled=true;
    const [r,d]=await send('/api/login',{password:$('pw').value}).catch(()=>[{ok:false},{}]);
    if(r.ok){location.href='/';return;}
    err.textContent=d.error||'Could not sign in.';$('pw').value='';$('btn').disabled=false;$('pw').focus();};
  $('btn').onclick=go;$('pw').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
}
})();
