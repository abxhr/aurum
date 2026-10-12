(function(){
const $=id=>document.getElementById(id),err=$('err');
async function save(){
  err.textContent='';
  if($('p1').value!==$('p2').value){err.textContent='The two passwords differ.';return;}
  $('btn').disabled=true;
  const r=await fetch('/api/setup',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Wealth':'1'},body:JSON.stringify({password:$('p1').value,currency:$('ccy').value})});
  const d=await r.json().catch(()=>({}));
  if(r.ok){location.href='/';return;}
  err.textContent=d.error||'Could not save.';$('btn').disabled=false;
}
$('btn').onclick=save;$('p2').addEventListener('keydown',e=>{if(e.key==='Enter')save();});
})();
