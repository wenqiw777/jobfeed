const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function harness({cancel=false,fail=false,missing=false,holdCooldown=false,scriptLost=false,iframeDelay=false,finalUrl=null,vanishedTab=false,snapshotOnly=false,emptySnapshot=false,lateRedirect=false,slowHydration=false,bootstrapOnly=false}={}) {
  const events=[],sent=[],live=new Set(),task={cancelled:false},cooldowns=[];
  let next=0,peak=0;
  const frameTabs=new Map(),reads=new Map();
  const chrome={
    permissions:{contains:async()=>!missing},
    tabs:{
      async get(){return finalUrl?{url:finalUrl}:{};},
      async create(){const id=++next;live.add(id);peak=Math.max(peak,live.size);events.push(['create',id]);return {id};},
      async update(id,{url}){if(vanishedTab){vanishedTab=false;live.delete(id);}if(!live.has(id))throw new Error(`No tab with id: ${id}.`);events.push(['navigate',id,url]);if(url.includes('greenhouse.io'))frameTabs.set(id,0);else frameTabs.delete(id);},
      async remove(id){live.delete(id);events.push(['remove',id]);},
    },
    scripting:{async executeScript({target,args,files}){
      if(vanishedTab){vanishedTab=false;live.delete(target.tabId);throw new Error(`No tab with id: ${target.tabId}.`);}
      if(files){events.push(['inject',target.tabId]);return [];}
      assert.ok(!Array.isArray(args[0]),'extract one loaded page, not an HTTP batch');
      events.push(['extract',target.tabId,args[0].id]);
      if(slowHydration){const n=reads.get(args[0].id)||0;reads.set(args[0].id,n+1);if(n<45)return [{result:{page_snapshot:{blocks:[]},frames:[]}}];}
      if(lateRedirect){const n=reads.get(args[0].id)||0;reads.set(args[0].id,n+1);if(n<3)return [{result:{page_snapshot:{blocks:[]},frames:[]}}];if(n===3)return [{result:{error:'GitHubJD is not defined'}}];}
      if(iframeDelay){if(!frameTabs.has(target.tabId))return [{result:{error:'Job identity not found in page',frames:['https://job-boards.eu.greenhouse.io/embed/job_app?for=example&token=1234567']}}]; const attempts=frameTabs.get(target.tabId);frameTabs.set(target.tabId,attempts+1);if(attempts===0)return [{result:{error:'Job identity not found in page'}}];}
      if(snapshotOnly || emptySnapshot)return [{result:{page_snapshot:{url:args[0].url,blocks:emptySnapshot?[]:[{id:0,text:'Engineer job description'}],truncated:false},frames:snapshotOnly?['https://job-boards.greenhouse.io/embed/job_app?token=123']:[]}}];
      if(bootstrapOnly)return [{result:{page_snapshot:{blocks:[]},frames:[],ats_urls:['https://boards.greenhouse.io/embed/job_board/js?for=example']}}];
      if(scriptLost){scriptLost=false;return [{result:{error:'GitHubJD is not defined'}}];}
      if(cancel)task.cancelled=true;
      if(fail)throw new Error('extraction failed');
      return [{result:{...args[0],description:'Complete JD',source:'github-jd'}}];
    }},
  };
  const context=vm.createContext({chrome,URL,Set,Map,
    wait:async ms=>{events.push(['wait',ms]);if(holdCooldown&&ms===1000)await new Promise(resolve=>cooldowns.push(resolve));},
    waitForTabComplete:async id=>{events.push(['loaded',id]);},
    waitForSocketCapacity:async()=>{},send:m=>sent.push(m)});
  vm.runInContext(fs.readFileSync('extensions/jobright-source/github-jd-worker.js','utf8'),context);
  const targets=Array.from({length:9},(_,i)=>({id:String(i),title:'Engineer',url:`https://host${i%2}.example/jobs/${i}`}));
  return {run:()=>context.runGitHubJDScan({task_id:'test',targets},task),events,sent,live,task,cooldowns,get peak(){return peak;}};
}
test('lost extraction script is reinjected with bounded recovery',async()=>{
 const h=harness({scriptLost:true});await h.run();
 assert.equal(h.sent.flatMap(m=>m.jobs||[]).length,9);
 assert.equal(h.events.filter(e=>e[0]==='inject').length,10);
 assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.description));
});
test('four real tabs process targets once and wait one second after extraction',async()=>{
  const h=harness();await h.run();
  assert.equal(h.peak,4);
  assert.equal(h.events.filter(e=>e[0]==='create').length,9);
  const rows=h.sent.flatMap(m=>m.jobs||[]);
  assert.equal(rows.length,9);assert.equal(new Set(rows.map(r=>r.id)).size,9);
  assert.ok(rows.every(r=>r.description));
  assert.equal(h.events.filter(e=>e[0]==='wait'&&e[1]===1000).length,9);
  assert.equal(h.live.size,0);assert.equal(h.task.tabIds.size,0);
  assert.equal(h.sent.at(-1).type,'complete');
});
test('cancellation cleans every owned tab and emits no completion',async()=>{
  const h=harness({cancel:true});await h.run();
  assert.equal(h.live.size,0);assert.ok(!h.sent.some(m=>m.type==='complete'));
});
test('extraction failures retain every target and release the tabs',async()=>{
  const h=harness({fail:true});await h.run();
  const rows=h.sent.flatMap(m=>m.jobs||[]);
  assert.equal(rows.length,9);assert.ok(rows.every(r=>r.error));assert.equal(h.live.size,0);
});
test('missing host permission does not open pages',async()=>{
  const h=harness({missing:true});await h.run();
  assert.equal(h.peak,0);assert.equal(h.sent.flatMap(m=>m.jobs||[]).length,9);
  assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.error_code==='missing_permission'));
});
test('cooldown blocks reuse, but releasing one worker does not wait for others',async()=>{
  const h=harness({holdCooldown:true});const running=h.run();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.sent.flatMap(m=>m.jobs||[]).length,4);
  assert.equal(h.cooldowns.length,4);
  assert.equal(h.events.filter(e=>e[0]==='navigate').length,0);
  h.cooldowns.shift()();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.sent.flatMap(m=>m.jobs||[]).length,5);
  assert.equal(h.events.filter(e=>e[0]==='create').length,5);
  h.task.cancelled=true;
  h.cooldowns.splice(0).forEach(resolve=>resolve());
  await running;
  assert.equal(h.live.size,0);
});

test('embedded Greenhouse job waits for hydrated identity and body',async()=>{
 const h=harness({iframeDelay:true});await h.run();
 assert.equal(h.sent.flatMap(m=>m.jobs||[]).length,9);
 assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.description));
 assert.equal(h.live.size,0);
});

test('Workday maintenance redirect is transient, not a permission request',async()=>{
 const h=harness({finalUrl:'https://community.workday.com/maintenance-page'});await h.run();
 const rows=h.sent.flatMap(m=>m.jobs||[]);
 assert.equal(rows.length,9);
 assert.ok(rows.every(r=>r.error_code==='transient'&&r.error.includes('maintenance')));
});

test('a vanished worker tab is recreated and the same job is retried',async()=>{
 const h=harness({vanishedTab:true});await h.run();
 const rows=h.sent.flatMap(m=>m.jobs||[]);
 assert.equal(rows.length,9);
 assert.equal(new Set(rows.map(r=>r.id)).size,9);
 assert.ok(rows.every(r=>r.description));
 assert.ok(h.peak<=4);
 assert.equal(h.live.size,0);
 assert.equal(h.task.tabIds.size,0);
});

test('readable parent snapshot is retained instead of replaced by application iframe',async()=>{
 const h=harness({snapshotOnly:true});await h.run();
 const rows=h.sent.flatMap(m=>m.jobs||[]);
 assert.ok(rows.every(r=>r.page_snapshot.blocks.length===1));
 assert.ok(!h.events.some(e=>e[0]==='navigate'&&e[2].includes('greenhouse.io')));
});
test('empty page exhausts bounded readiness then reports retryable timeout',async()=>{
 const h=harness({emptySnapshot:true});await h.run();
 const rows=h.sent.flatMap(m=>m.jobs||[]);
 assert.equal(rows.length,9);
 assert.ok(rows.every(r=>r.error_code==='page_timeout'));
 assert.equal(h.events.filter(e=>e[0]==='extract').length,9*120);
});

test('redirect after several readiness polls reinjects into replacement document',async()=>{
 const h=harness({lateRedirect:true});await h.run();
 assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.description));
});
test('each target gets a fresh document so navigation cannot read previous job',async()=>{
 const h=harness();await h.run();
 assert.equal(h.events.filter(e=>e[0]==='create').length,9);
 assert.ok(h.peak<=4);
});

test('slow hydration stays in the same job document and becomes readable',async()=>{
 const h=harness({slowHydration:true});await h.run();
 assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.description));
 assert.equal(h.events.filter(e=>e[0]==='create').length,9);
});

test('bootstrap metadata survives timeout without navigating to JavaScript',async()=>{
 const h=harness({bootstrapOnly:true});await h.run();
 assert.ok(h.sent.flatMap(m=>m.jobs||[]).every(r=>r.ats_urls?.length===1));
 assert.ok(!h.events.some(e=>e[0]==='navigate'));
});
