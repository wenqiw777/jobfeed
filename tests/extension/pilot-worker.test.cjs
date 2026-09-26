const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function harness(fail=false){
 const stored=[],calls=[],removed=[];let observe;
 const chrome={
  webRequest:{onBeforeSendHeaders:{addListener(fn){observe=fn}}},
  runtime:{onMessage:{addListener(){}},getURL:p=>'chrome-extension://test/'+p},
  tabs:{onRemoved:{addListener(){}},async create(){setTimeout(()=>observe({tabId:7,requestHeaders:[{name:'x-csrf-token',value:'ephemeral-test'},{name:'Cookie',value:'must-not-copy'}]}),0);return {id:7}},async remove(id){removed.push(id)}},
  storage:{local:{async set(d){stored.push(d)}}},
  scripting:{async executeScript(args){calls.push(args);if(args.files)return [];if(fail)throw new Error('test failure');return [{result:{source:'handshake',status:'succeeded',jobs:[],elapsedMs:2}}]}},
 };
 const context=vm.createContext({chrome,Map,Date,setTimeout,clearTimeout,console});vm.runInContext(fs.readFileSync('extensions/jobright-source/pilot-worker.js','utf8'),context);
 return {context,stored,calls,removed};
}
test('pilot uses isolated world, ephemeral CSRF headers, and exports no credentials',async()=>{
 const h=harness();await h.context.runBoardPilot({source:'handshake',query:'AI Engineer New Grad',maxJobs:50});
 assert.equal(h.calls[0].world,'ISOLATED');assert.equal(h.calls[1].args[0].headers['x-csrf-token'],'ephemeral-test');assert.equal(h.calls[1].args[0].headers.Cookie,undefined);
 assert.ok(!JSON.stringify(h.stored).includes('ephemeral-test'));assert.deepEqual(h.removed,[7]);
});
test('pilot cleans up owned tab and records injection failure',async()=>{
 const h=harness(true);await assert.rejects(h.context.runBoardPilot({source:'handshake',query:'SWE'}),/test failure/);
 assert.deepEqual(h.removed,[7]);assert.equal(h.stored.at(-1).jobboardPilotState.status,'failed');
});

test('discovery transport preserves explicit repost evidence',async()=>{
 const h=harness();let message;h.context.send=m=>{message=m;queueMicrotask(()=>h.context.resolveDiscovery({request_id:m.request_id,task_id:m.task_id,skip_ids:[],reused_jobs:[]}));};
 const row={id:'123',isRepost:true,repostEvidence:'Reposted 1 day ago',repostObservedAt:'2026-09-21T02:00:00Z'};
 await h.context.lookupDiscovered('task',[row]);
 assert.equal(message.rows[0].repostEvidence,row.repostEvidence);
 assert.equal(message.rows[0].isRepost,true);
});
