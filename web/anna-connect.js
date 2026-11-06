(() => {
  const $=s=>document.querySelector(s);
  let csrf='';
  async function api(path,body){
    const r=await fetch('/api'+path,{method:body?'POST':'GET',credentials:'same-origin',headers:{...(body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{})},...(body?{body:JSON.stringify(body)}:{})});
    const d=await r.json();if(!r.ok)throw Error(d.message||'The request failed.');return d;
  }
  const message=s=>$('#message').textContent=s;
  async function check(){
    try {
      const s=await api('/status');csrf=s.csrfToken||'';
      $('#signin').hidden=!!s.authenticated;$('#consent').hidden=!s.authenticated;
      $('#title').textContent=s.authenticated?'Your Anna connection.':'Sign into HomeBase.';
      if(!s.authenticated){message('Use your existing HomeBase account. Credentials are sent only to this local HTTPS server.');return;}
      const c=await api('/anna/status');$('#commands').checked=!!c.commands;$('#cameras').checked=!!c.cameraImages;
      message(c.connected?'Connected. Return to HomeBase OS in Anna and check the connection.':'Signed in locally. Review the options, then connect.');
    }catch(e){message(e.message);}
  }
  $('#signin').addEventListener('submit',async e=>{e.preventDefault();const f=e.target;const b=f.querySelector('button');b.disabled=true;try{await api('/auth/login',Object.fromEntries(new FormData(f)));f.password.value='';f.totp.value='';await check();}catch(err){message(err.message);}finally{b.disabled=false;}});
  $('#enable').addEventListener('click',async e=>{e.target.disabled=true;try{await api('/anna/connect',{commands:$('#commands').checked,cameraImages:$('#cameras').checked});await check();}catch(err){message(err.message);}finally{e.target.disabled=false;}});
  $('#disconnect').addEventListener('click',async e=>{e.target.disabled=true;try{await api('/anna/disconnect',{});await check();}catch(err){message(err.message);}finally{e.target.disabled=false;}});
  check();
})();
