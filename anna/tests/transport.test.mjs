import test from 'node:test';
import assert from 'node:assert/strict';
import {createTransport,containsSecret} from '../bundle/transport.js';

test('real Anna RPC arguments contain no browser credentials or auth headers',async()=>{
  const calls=[];
  const t=createTransport({tools:{invoke:async args=>{calls.push(args);return {status:200,data:{authenticated:true,bridge:{connected:true,commands:true,cameraImages:false}}};}}});
  const r=await t.fetch('/api/status',{headers:{Cookie:'must not travel','X-CSRF-Token':'private'}});
  assert.equal((await r.json()).authenticated,true);
  assert.deepEqual(calls[0].args,{path:'/api/status',method:'GET'});
  await t.fetch('/api/tvs/test/command',{method:'POST',body:JSON.stringify({command:'volume_up'})});
  assert.equal(calls[1].args.body.command,'volume_up');
});

test('sensitive forms and pairing never enter an Anna RPC',async()=>{
  let calls=0;
  const t=createTransport({tools:{invoke:async()=>{calls++;throw Error('must not send');}}});
  for(const [path,body] of [['/api/auth/login',{username:'owner',password:'secret'}],['/api/tvs/test/pair/finish',{code:'1234'}],['/api/settings',{ai:{apiKey:'secret'}}],['/api/integrations',{password:'secret'}]]){
    await assert.rejects(t.fetch(path,{method:'POST',body:JSON.stringify(body)}));
  }
  assert.equal(calls,0);
  assert.equal(containsSecret({nested:[{password:'secret'}]}),true);
});

test('commands and snapshots require the explicit local grants',async()=>{
  let calls=0;
  const t=createTransport({tools:{invoke:async()=>{calls++;return {status:200,data:{}};}}});
  await assert.rejects(t.fetch('/api/tvs/test/command',{method:'POST',body:'{}'}),/read-only/);
  await assert.rejects(t.fetch('/api/cameras/test/snapshot'),/image sharing is off/);
  assert.equal(calls,0);
});

test('overlapping reads coalesce while errors remain explicit',async()=>{
  let calls=0,resolve;
  const t=createTransport({tools:{invoke:()=>{calls++;return new Promise(r=>resolve=r);}}});
  const a=t.fetch('/api/overview'),b=t.fetch('/api/overview');
  resolve({result:{status:200,data:{devices:[]}}});
  assert.equal(calls,1);
  assert.deepEqual(await (await a).json(),await (await b).json());
  const failing=createTransport({tools:{invoke:async()=>({success:false,error:'agent_unavailable'})}});
  await assert.rejects(failing.fetch('/api/status'),/agent_unavailable/);
});
