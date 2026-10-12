/* Passkey helpers shared by the sign-in, enrol and lock screens. Face ID runs inside the phone; this file only carries the challenge and the signed answer. */
(function(){
const b64u2buf=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');const p=atob(s+'='.repeat((4-s.length%4)%4));const a=new Uint8Array(p.length);for(let i=0;i<p.length;i++)a[i]=p.charCodeAt(i);return a.buffer;};
const buf2b64u=b=>{const a=new Uint8Array(b);let s='';for(const c of a)s+=String.fromCharCode(c);return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');};
const post=async(path,body)=>{const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Wealth':'1'},body:JSON.stringify(body||{})});const d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false)throw new Error(d.error||'Failed');return d;};

async function login(){
  const o=await post('/api/passkey/login/options');
  const pk=o.options;pk.challenge=b64u2buf(pk.challenge);(pk.allowCredentials||[]).forEach(c=>c.id=b64u2buf(c.id));
  const cred=await navigator.credentials.get({publicKey:pk});
  const r=cred.response;
  await post('/api/passkey/login/verify',{cid:o.cid,credential:{id:cred.id,rawId:buf2b64u(cred.rawId),type:cred.type,response:{clientDataJSON:buf2b64u(r.clientDataJSON),authenticatorData:buf2b64u(r.authenticatorData),signature:buf2b64u(r.signature),userHandle:r.userHandle?buf2b64u(r.userHandle):null}}});
}
async function register(token,name){
  const o=await post('/api/passkey/register/options',{token});
  const pk=o.options;pk.challenge=b64u2buf(pk.challenge);pk.user.id=b64u2buf(pk.user.id);(pk.excludeCredentials||[]).forEach(c=>c.id=b64u2buf(c.id));
  const cred=await navigator.credentials.create({publicKey:pk});
  const r=cred.response;
  await post('/api/passkey/register/verify',{cid:o.cid,name,credential:{id:cred.id,rawId:buf2b64u(cred.rawId),type:cred.type,response:{clientDataJSON:buf2b64u(r.clientDataJSON),attestationObject:buf2b64u(r.attestationObject),transports:r.getTransports?r.getTransports():[]}}});
}
window.Passkey={login,register,post};
})();
