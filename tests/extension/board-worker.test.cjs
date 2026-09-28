const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function harness(pages){
 const sent=[],removed=[],calls=[],injections=[],waits=[],logs=[],listeners=[];let observe;
 const chrome={webRequest:{onBeforeSendHeaders:{addListener(fn){observe=fn}}},
 runtime:{onMessage:{addListener(fn){listeners.push(fn)}},getURL:p=>'chrome-extension://test/'+p},
 tabs:{onUpdated:{addListener(){},removeListener(){}},onRemoved:{addListener(){},removeListener(){}},async get(){return {url:'https://www.linkedin.com/jobs/search/',status:'complete'}},async create(){setTimeout(()=>observe({tabId:7,requestHeaders:[{name:'x-csrf-token',value:'ephemeral-test'},{name:'csrf-token',value:'ephemeral-test'}]}),0);return {id:7}},async remove(id){removed.push(id)}},
 scripting:{async executeScript(args){if(args.files){injections.push(args);return [];}calls.push(args.args[0]);const page=pages.shift();if(page instanceof Error)throw page;return [{result:page}]}}};
 const context=vm.createContext({chrome,Map,Set,Date,URL,setTimeout,clearTimeout,console:{warn:(...args)=>logs.push(args)},positiveInteger:(n,d)=>n||d,wait:ms=>{waits.push(ms);return new Promise(r=>setTimeout(r,0));},waitForTabComplete:async()=>{},waitForSocketCapacity:async()=>{},send:m=>sent.push(m)});
 vm.runInContext(fs.readFileSync('extensions/jobright-source/pilot-worker.js','utf8'),context);
 return {context,sent,removed,calls,injections,waits,logs,listeners};
}
test('detail progress is forwarded only for its active task and owned tab',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:1,total:1}]);
 const execute=h.context.chrome.scripting.executeScript;
 h.context.chrome.scripting.executeScript=async args=>{
  if(!args.files){
   const m={type:'jobboard_progress',task_id:'t',phase:'details',processed:1,total:1,currentJobId:'1'};
   for(const fn of h.listeners){fn({...m,task_id:'other'},{tab:{id:7}},()=>{});fn(m,{tab:{id:8}},()=>{});fn(m,{tab:{id:7}},()=>{});}
  }
  return execute(args);
 };
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:1,progress_events:true},{cancelled:false});
 const progress=h.sent.filter(m=>m.type==='progress');
 assert.equal(progress.length,1);assert.equal(progress[0].current_job_id,'1');assert.equal(progress[0].processed,1);
 for(const fn of h.listeners)fn({type:'jobboard_progress',task_id:'t',phase:'details',processed:2},{tab:{id:7}},()=>{});
 assert.equal(h.sent.filter(m=>m.type==='progress').length,1);
});
test('old backend commands never receive unsolicited progress',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:1,total:1}]);
 const execute=h.context.chrome.scripting.executeScript;
 h.context.chrome.scripting.executeScript=async args=>{
  if(!args.files){
   assert.equal(args.args[0].progressEvents,false);
   for(const fn of h.listeners)fn({type:'jobboard_progress',task_id:'t',phase:'details',processed:1,total:1},{tab:{id:7}},()=>{});
  }
  return execute(args);
 };
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:1},{cancelled:false});
 assert.equal(h.sent.filter(m=>m.type==='progress').length,0);
 assert.equal(h.sent.at(-1).type,'complete');
});
test('long cross-batch backoff notices cancellation within one short wait',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:1,total:2,retryAfterMs:300000}]);
 const task={cancelled:false};const wait=h.context.wait;
 let backoffWait;
 h.context.wait=async ms=>{
  if(h.calls.length===1&&ms>1){backoffWait=ms;task.cancelled=true;}
  await wait(ms);
 };
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:2,pacing_ms:1},task);
 assert.ok(backoffWait<=250);
 assert.equal(h.calls.length,1);
 assert.ok(!h.sent.some(m=>m.type==='complete'));
});
test('LinkedIn Retry-After survives page and task boundaries without delaying Handshake',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:1,total:2,retryAfterMs:100},
  {jobs:[{id:'2'}],nextOffset:2,total:2,retryAfterMs:100},
  {jobs:[{id:'h'}],nextOffset:1,total:1},
  {jobs:[{id:'3'}],nextOffset:1,total:1}]);
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'one',max_jobs:2},{cancelled:false});
 assert.ok(h.waits.some(ms=>ms>50&&ms<=100));
 const before=h.waits.length;
 await h.context.runBoardScan({source:'handshake',query:'SWE',task_id:'h',max_jobs:1},{cancelled:false});
 assert.ok(!h.waits.slice(before).some(ms=>ms>50&&ms<=100));
 const next=h.waits.length;
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'two',max_jobs:1},{cancelled:false});
 assert.ok(h.waits.slice(next).some(ms=>ms>50&&ms<=100));
});
test('cancellation during cross-batch Retry-After does not inject a new request',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:1,total:2,retryAfterMs:100},{jobs:[{id:'2'}],nextOffset:2,total:2}]);
 const task={cancelled:false};const wait=h.context.wait;
 h.context.wait=async ms=>{if(ms>50&&ms<=100)task.cancelled=true;await wait(ms);};
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:2},task);
 assert.equal(h.calls.length,1);assert.ok(!h.sent.some(m=>m.type==='complete'));
});
test('uncertain discovery stops with warning and never requests details',async()=>{
 const h=harness([{discoveredRows:[],jobs:[],warning:'Unconfirmed empty page',nextOffset:25,batches:[{returned:0}]}]);
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',discovery_gate:true},{cancelled:false});
 assert.equal(h.calls.length,1);
 assert.equal(h.sent.at(-1).type,'complete');
 assert.equal(h.sent.at(-1).warning,'Unconfirmed empty page');
});
test('ID gate reuses stored and Redis results, and continues across duplicate-only pages',async()=>{
 const h=harness([
  {discoveredRows:[{id:'old'},{id:'saved'},{id:'new'}],nextOffset:3,total:5,batches:[{returned:3}]},
  {jobs:[{id:'new',description:'new JD'}]},
  {discoveredRows:[{id:'old'},{id:'other-search'}],nextOffset:5,total:5,batches:[{returned:2}]},
 ]);
 const probes=[];
 h.context.lookupDiscovered=async(_task,rows)=>{
  probes.push(Array.from(rows,r=>r.id));
  return probes.length===1?{skip_ids:['old'],reused_jobs:[{id:'old',description:'stored JD'}]}:
    {skip_ids:['other-search'],reused_jobs:[]};
 };
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:10,
  discovery_gate:true,cached_jobs:[{id:'saved',description:'Redis JD'}]}, {cancelled:false});
 assert.deepEqual(probes,[['old','new'],['other-search']]);
 assert.deepEqual(h.calls.filter(c=>c.discoveredRows).flatMap(c=>c.discoveredRows.map(r=>r.id)),['new']);
 assert.deepEqual(h.sent.flatMap(m=>m.jobs||[]).map(j=>j.id).sort(),['new','old','saved']);
 assert.equal(h.sent.at(-1).type,'complete');
});
test('database lookup error fails closed without any detail phase',async()=>{
 const h=harness([{discoveredRows:[{id:'1'}],nextOffset:1,batches:[{returned:1}]}]);
 h.context.lookupDiscovered=async()=>{throw new Error('DB unavailable');};
 await assert.rejects(h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',discovery_gate:true},{cancelled:false}),/DB unavailable/);
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].discoverOnly,true);
});
test('discovery response is correlated to its task and cancellation rejects the wait',async()=>{
 const h=harness([]);
 const pending=h.context.lookupDiscovered('one',[{id:'old',employer:{name:'Noom'}}]);
 const request=h.sent[0];
 assert.equal(request.rows[0].employer.name,'Noom');
 h.context.resolveDiscovery({task_id:'wrong',request_id:request.request_id,skip_ids:[],reused_jobs:[]});
 h.context.resolveDiscovery({task_id:'one',request_id:request.request_id,skip_ids:['old'],reused_jobs:[]});
 assert.deepEqual((await pending).skip_ids,['old']);
 const cancelled=h.context.lookupDiscovered('two',[{id:'new'}]);
 h.context.cancelDiscovery('two');
 await assert.rejects(cancelled,/cancelled/);
});
test('Redis replay refreshes only missing company without fetching the JD again',async()=>{
 const h=harness([{discoveredRows:[{id:'saved',employer:{name:'Noom'}}],nextOffset:1,total:1,batches:[{returned:1}]}]);
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:1,discovery_gate:true,
  cached_jobs:[{id:'saved',_stored_posting:{company:'Unknown',jd_text:'preserved JD'}}]}, {cancelled:false});
 assert.equal(h.calls.length,1);
 const row=h.sent.find(m=>m.type==='batch').jobs[0];
 assert.equal(row.employer.name,'Noom');
 assert.equal(row._stored_posting.jd_text,'preserved JD');
});
test('production worker streams pages and resumes cursor before completion',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:25,total:50},{jobs:[{id:'2'}],nextOffset:50,total:50}]);
 await h.context.runBoardScan({source:'handshake',query:'SWE',task_id:'t',max_jobs:50},{cancelled:false});
 assert.deepEqual(h.sent.map(m=>m.type),['batch','batch','complete']);
 assert.deepEqual(h.calls.map(c=>c.startOffset),[0,25]);assert.deepEqual(h.removed,[7]);
});
test('LinkedIn frame loss retries the failed cursor and preserves earlier batches',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:25,total:50},new Error('Frame with ID 0 was removed.'),{jobs:[{id:'1'},{id:'2'}],nextOffset:50,total:50}]);
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t',max_jobs:50},{cancelled:false});
 assert.deepEqual(h.calls.map(c=>c.startOffset),[0,25,25]);
 assert.deepEqual(h.sent.flatMap(m=>m.jobs||[]).map(j=>j.id),['1','2']);
 assert.equal(h.sent.at(-1).type,'complete');assert.equal(h.removed.length,1);
 assert.equal(h.injections.length,3);assert.equal(h.logs.filter(l=>l[0]==='jobboard_frame_retry').length,1);
});
test('frame recovery stops after two retries',async()=>{
 const h=harness(Array.from({length:3},()=>new Error('Frame with ID 0 was removed.')));
 await assert.rejects(h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},{cancelled:false}),/Frame with ID 0/);
 assert.equal(h.calls.length,3);assert.deepEqual(h.removed,[7]);
});
test('a closed tab is not recreated during recovery',async()=>{
 const h=harness([new Error('Frame with ID 0 was removed.')]);
 h.context.chrome.tabs.get=async()=>{throw new Error('No tab with id: 7');};
 await assert.rejects(h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},{cancelled:false}),/No tab/);
 assert.equal(h.calls.length,1);
});
test('cancellation during recovery does not retry or complete',async()=>{
 const h=harness([new Error('Frame with ID 0 was removed.')]),task={cancelled:false};
 const wait=h.context.wait;h.context.wait=async ms=>{await wait(ms);if(ms===1000)task.cancelled=true;};
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},task);
 assert.equal(h.calls.length,1);assert.equal(h.sent.length,0);assert.deepEqual(h.removed,[7]);
});
test('HTTP limit stops without retry and retains the preceding batch',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:25,total:50},{jobs:[],error:'HTTP 429',retryAfter:'60'}]);
 await assert.rejects(h.context.runBoardScan({source:'handshake',query:'SWE',task_id:'t',max_jobs:50},{cancelled:false}),/429.*60/);
 assert.deepEqual(h.sent.map(m=>m.type),['batch']);assert.equal(h.calls.length,2);assert.deepEqual(h.removed,[7]);
});
test('recovery stops at a login redirect',async()=>{
 const h=harness([new Error('Frame with ID 0 was removed.')]);
 h.context.chrome.tabs.get=async()=>({url:'https://www.linkedin.com/checkpoint/challenge/'});
 await assert.rejects(h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},{cancelled:false}),/left the job search page/);
 assert.equal(h.calls.length,1);assert.deepEqual(h.removed,[7]);
});
test('unrelated script errors are not retried',async()=>{
 const h=harness([new Error('Unexpected parser failure')]);
 await assert.rejects(h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},{cancelled:false}),/Unexpected parser/);
 assert.equal(h.calls.length,1);assert.equal(h.logs.length,0);
});
test('frame loss during script injection is recoverable too',async()=>{
 const h=harness([{jobs:[{id:'1'}],nextOffset:25,total:25}]);
 const execute=h.context.chrome.scripting.executeScript;let injects=0;
 h.context.chrome.scripting.executeScript=async args=>{
  if(args.files&&++injects===1)throw new Error('Frame with ID 0 was removed.');
  return execute(args);
 };
 await h.context.runBoardScan({source:'linkedin',query:'SWE',task_id:'t'},{cancelled:false});
 assert.equal(injects,2);assert.equal(h.calls.length,1);assert.equal(h.sent.at(-1).type,'complete');
});
