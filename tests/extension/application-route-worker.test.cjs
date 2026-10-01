const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function harness({manualTimers=false,firstMedia=false,holdCreates=false,holdSocket=false,targetCount=5}={}){
  const sent=[],events=[],live=new Map(),held=[],rules=new Map(),timers=[],creating=[];let next=0,peak=0;
  const chrome={
    permissions:{contains:async()=>true},
    declarativeNetRequest:{async updateSessionRules({addRules=[],removeRuleIds=[]}){for(const id of removeRuleIds)rules.delete(id);for(const rule of addRules)rules.set(rule.id,rule);events.push(['rules',addRules]);}},
    tabs:{async create(options){if(holdCreates)await new Promise(resolve=>creating.push(resolve));const id=++next;live.set(id,options.url);peak=Math.max(peak,live.size);events.push(['create',id,options]);return {id};},
      async update(id,{url}){assert.ok([...rules.values()].some(r=>r.condition.tabIds.includes(id)),'guard installed before navigation');live.set(id,url);events.push(['navigate',id,url]);},
      async get(id){if(!live.has(id))throw Error('No tab');return {id,url:live.get(id)};},
      async remove(id){live.delete(id);events.push(['remove',id]);}},
    scripting:{async executeScript({target,files}){if(files)return [];await new Promise(resolve=>held.push({id:target.tabId,resolve}));const read=(reads.get(target.tabId)||0)+1;reads.set(target.tabId,read);return [{result:{url:live.get(target.tabId),has_application_link:true,html:firstMedia&&read<3?'<h1>Engineer</h1><iframe src="https://video.example/embed/1"></iframe>':'<h1>Engineer</h1><a href="https://jobs.lever.co/acme/abc">Apply</a>'}}];}},
  };
  const reads=new Map();const context=vm.createContext({chrome,URL,Set,Map,console,
    setTimeout:manualTimers?callback=>{timers.push(callback);return callback;}:setTimeout,
    clearTimeout:manualTimers?callback=>{const index=timers.indexOf(callback);if(index>=0)timers.splice(index,1);}:clearTimeout,
    wait:async()=>{},waitForTabComplete:async()=>{},waitForSocketCapacity:async()=>{events.push(['socket']);if(holdSocket)await new Promise(()=>{});},send:m=>sent.push(m)});
  vm.runInContext(fs.readFileSync('extensions/jobright-source/application-route-worker.js','utf8'),context);
  const targets=Array.from({length:targetCount},(_,i)=>({id:String(i),url:`https://careers.example/jobs/${i}`,title:'Engineer',company:'Acme'}));
  return {context,targets,held,sent,events,live,rules,timers,reads,creating,get peak(){return peak;}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('overlapping calls share ten inactive tabs, the eleventh waits, and task ownership is preserved',async()=>{
  const h=harness({targetCount:11}),a={cancelled:false},b={cancelled:false};
  const runs=[h.context.runApplicationRouteScan({task_id:'a',targets:h.targets.slice(0,6)},a),h.context.runApplicationRouteScan({task_id:'b',targets:h.targets.slice(6)},b)];
  await tick();assert.equal(h.peak,10);assert.equal(h.events.filter(e=>e[0]==='create').length,10);
  await tick();assert.equal(h.events.filter(e=>e[0]==='create').length,10);
  for(let n=0;n<60;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  await Promise.all(runs);
  assert.equal(h.peak,10);assert.equal(h.live.size,0);assert.equal(h.rules.size,0);
  assert.ok(h.events.filter(e=>e[0]==='create').every(e=>e[2].active===false&&e[2].url==='about:blank'));
  const allow=h.events.find(e=>e[0]==='rules'&&e[1].length)[1].find(r=>r.action.type==='allow');
  const filter=new RegExp(allow.condition.regexFilter);
  assert.ok(filter.test('https://careers.example/jobs/1'));
  assert.ok(!filter.test('https://careersXexample/jobs/1'));
  assert.ok(!filter.test('https://unvalidated.careers.example/jobs/1'));
  for(const [id,count] of [['a',6],['b',5]]){assert.equal(h.sent.filter(m=>m.task_id===id).flatMap(m=>m.jobs||[]).length,count);assert.equal(h.sent.filter(m=>m.task_id===id&&m.type==='complete').length,1);}
});

test('cancelling one queued call releases only its own tabs and leaves another task running',async()=>{
  const h=harness(),a={cancelled:false},b={cancelled:false};
  const runs=[h.context.runApplicationRouteScan({task_id:'a',targets:h.targets},a),h.context.runApplicationRouteScan({task_id:'b',targets:h.targets},b)];
  await tick();a.cancelled=true;h.context.cancelApplicationRouteTask(a);await tick();
  for(let n=0;n<60;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  await Promise.all(runs);
  assert.ok(!h.sent.some(m=>m.task_id==='a'));
  assert.equal(h.sent.filter(m=>m.task_id==='b').flatMap(m=>m.jobs||[]).length,5);
  assert.equal(h.live.size,0);assert.equal(h.rules.size,0);
});

test('private or credentialed targets are rejected before opening a tab',async()=>{
  const h=harness();
  for(const url of ['http://localhost/job','http://127.0.0.1/job','http://10.2.3.4/job','http://169.254.169.254/job','http://[::1]/job','https://user:pass@example.com/job'])
    await assert.rejects(h.context.runApplicationRouteScan({task_id:url,targets:[{id:'1',url}]},{cancelled:false}),/public/);
  assert.equal(h.events.length,0);
});

test('one page timeout releases its own tab without interrupting peers in the same call',async()=>{
  const h=harness({manualTimers:true}),task={cancelled:false};
  const running=h.context.runApplicationRouteScan({task_id:'a',targets:h.targets},task);
  await tick();assert.equal(h.live.size,5);
  const peers=[...h.live.keys()].slice(1);
  h.timers[0]();await tick();
  assert.ok(peers.every(id=>h.live.has(id)));
  assert.equal(h.sent.flatMap(m=>m.jobs||[])[0].error_code,'page_timeout');
  for(let n=0;n<60;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  await running;assert.equal(h.live.size,0);assert.equal(h.peak,5);
});

test('video or recommended Apply readiness does not end snapshots before target hydration',async()=>{
  const h=harness({firstMedia:true});
  const running=h.context.runApplicationRouteScan({task_id:'hydration',targets:h.targets.slice(0,1)},{cancelled:false});
  for(let n=0;n<16;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  await running;
  assert.match(h.sent.flatMap(m=>m.jobs||[])[0].html,/jobs.lever.co/);
  assert.ok(h.reads.get(1)>=3);
});

test('permission timeout cannot retain a worker or create a tab',async()=>{
  const h=harness({manualTimers:true});h.context.chrome.permissions.contains=()=>new Promise(()=>{});
  const running=h.context.runApplicationRouteScan({task_id:'permission',targets:h.targets.slice(0,1)},{cancelled:false});
  await tick();h.timers[0]();await running;
  assert.equal(h.live.size,0);assert.equal(h.sent.flatMap(m=>m.jobs||[])[0].error_code,'page_timeout');
});

test('late-created cancelled tabs are closed before capacity is reused',async()=>{
  const h=harness({manualTimers:true,holdCreates:true,targetCount:11}),a={cancelled:false},b={cancelled:false};
  const first=h.context.runApplicationRouteScan({task_id:'first',targets:h.targets.slice(0,10)},a);
  await tick();a.cancelled=true;h.context.cancelApplicationRouteTask(a);await first;
  const second=h.context.runApplicationRouteScan({task_id:'second',targets:h.targets.slice(0,1)},b);
  await tick();assert.equal(h.creating.length,10);
  h.creating.splice(0).forEach(resolve=>resolve());await tick();
  assert.equal(h.live.size,0);assert.equal(h.creating.length,1);
  h.creating.splice(0).forEach(resolve=>resolve());
  for(let n=0;n<16;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  await second;assert.ok(h.peak<=10);assert.equal(h.live.size,0);assert.equal(h.rules.size,0);
});

test('socket backpressure notices cancellation without retaining the task',async()=>{
  const h=harness({holdSocket:true}),task={cancelled:false};
  const running=h.context.runApplicationRouteScan({task_id:'socket',targets:h.targets.slice(0,1)},task);
  for(let n=0;n<16;n++){h.held.splice(0).forEach(x=>x.resolve());await tick();}
  assert.ok(h.events.some(e=>e[0]==='socket'));
  task.cancelled=true;h.context.cancelApplicationRouteTask(task);await running;
  assert.equal(h.live.size,0);assert.equal(h.rules.size,0);assert.equal(h.sent.length,0);
});
