(function(){
const $=id=>document.getElementById(id),err=$('err');
const token=(location.hash||'').slice(1);
history.replaceState(null,'',location.pathname);        // the one-time token never stays in the address bar or history
if(!token){err.textContent='This link is incomplete. Make a new one on your PC.';$('btn').disabled=true;return;}
if(!window.PublicKeyCredential){err.textContent='This browser cannot use Face ID. Open the link in Safari.';$('btn').disabled=true;return;}
$('btn').onclick=async()=>{
  err.textContent='';$('btn').disabled=true;
  try{await Passkey.register(token,$('name').value.trim()||'iPhone');location.href='/';}
  catch(e){err.textContent=(e&&e.name==='NotAllowedError')?'Face ID was cancelled.':(e.message||'Could not set up Face ID.');$('btn').disabled=false;}
};
})();
