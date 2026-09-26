const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function harness(get,execute){
 const listeners=new Set(),event={addListener(){}};
 const context=vm.createContext({importScripts(){},URL,console,setInterval,clearInterval,
  setTimeout:(fn)=>setTimeout(fn,25),clearTimeout,WebSocket:{OPEN:1,CONNECTING:0},
  chrome:{runtime:{onInstalled:event,onStartup:event,onMessage:event},alarms:{onAlarm:event},
   storage:{local:{get:()=>new Promise(()=>{})}},
   tabs:{get:()=>get(listeners),onUpdated:{addListener:fn=>listeners.add(fn),removeListener:fn=>listeners.delete(fn)}},
   scripting:{executeScript:execute}}});
 vm.runInContext(fs.readFileSync('extensions/jobright-source/service-worker.js','utf8'),context);
 return {context,listeners};
}
test('tab completion between status read and listener subscription is not lost',async()=>{
 let reads=0;
 const h=harness(async listeners=>{
  reads++;if(reads===1)for(const fn of listeners)fn(7,{status:'complete'});
  return {status:reads===1?'loading':'complete'};
 });
 await h.context.waitForTabComplete(7);
 assert.equal(h.listeners.size,0);
});
test('source API can start on interactive same-origin document while tracking resources load',async()=>{
 const h=harness(async()=>({status:'loading'}),async()=>[{result:{readyState:'interactive',origin:'https://jobright.ai'}}]);
 await h.context.waitForTabComplete(7,'https://jobright.ai');
 assert.equal(h.listeners.size,0);
});
test('JD readiness accepts interactive redirected document without waiting for video resources',async()=>{
 const h=harness(async()=>({status:'loading'}),async()=>[{result:{readyState:'interactive',origin:'https://jobs.example'}}]);
 await h.context.waitForTabComplete(7,null,true);
 assert.equal(h.listeners.size,0);
});
