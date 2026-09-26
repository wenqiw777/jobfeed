// CSRF headers are kept only in worker memory, never in storage or exports.
const pilotHeaders = new Map();
const pilotTabs = new Set();
let pilotRunning = false;
const discoveryRequests = new Map();
let discoverySequence = 0;
function resolveDiscovery(message) {
  const pending = discoveryRequests.get(message.request_id);
  if (!pending || pending.taskId !== message.task_id) return;
  if (message.error) pending.reject(new Error(message.error));
  else pending.resolve(message);
}
function cancelDiscovery(taskId) {
  for (const pending of discoveryRequests.values()) {
    if (pending.taskId === taskId) pending.reject(new Error('Discovery lookup cancelled'));
  }
}
async function lookupDiscovered(taskId, rows) {
  const requestId = `${taskId}:${++discoverySequence}`;
  let timer;
  try {
    return await new Promise((resolve,reject)=>{
      discoveryRequests.set(requestId,{taskId,resolve,reject});
      timer=setTimeout(()=>reject(new Error('Discovery lookup timed out; details not requested')),30000);
      send({type:'discovery',task_id:taskId,request_id:requestId,rows:rows.map(row=>({...row,id:String(row.id)}))});
    });
  } finally {
    clearTimeout(timer);
    discoveryRequests.delete(requestId);
  }
}
chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  if (!pilotTabs.has(details.tabId)) return;
  const values = {};
  for (const header of details.requestHeaders || []) {
    if (['csrf-token', 'x-csrf-token', 'x-xsrf-token', 'x-restli-protocol-version'].includes(header.name.toLowerCase())) {
      values[header.name.toLowerCase()] = header.value;
    }
  }
  if (Object.keys(values).length) pilotHeaders.set(details.tabId, {...pilotHeaders.get(details.tabId), ...values});
}, {urls:['https://www.linkedin.com/voyager/api/*','https://app.joinhandshake.com/hs/graphql']}, ['requestHeaders']);
chrome.tabs.onRemoved.addListener(id => { pilotHeaders.delete(id); pilotTabs.delete(id); });
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type !== 'pilot_scan') return false;
  if (!sender.url?.startsWith(chrome.runtime.getURL('pilot.html'))) return false;
  if (pilotRunning || (typeof activeTasks !== 'undefined' && activeTasks.size)) {
    respond({error:'A scan is already running'}); return false;
  }
  pilotRunning = true;
  void runBoardPilot(message).then(respond, e=>respond({error:e.message})).finally(()=>{pilotRunning=false;});
  return true;
});
async function runBoardPilot(message) {
  if (!['linkedin','handshake'].includes(message.source)) throw new Error('Unsupported source');
  const query = String(message.query || '').trim();
  if (!query || query.length > 200) throw new Error('Enter a search query (max 200 characters)');
  const started = Date.now();
  const source = message.source;
  const url = source === 'linkedin'
    ? `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(query)}&location=United%20States&f_TPR=r86400&sortBy=DD`
    : `https://app.joinhandshake.com/job-search?query=${encodeURIComponent(query)}&per_page=25&sort=relevance&page=1`;
  await chrome.storage.local.set({jobboardPilotState:{status:'running',source,startedAt:new Date().toISOString()}});
  const tab = await chrome.tabs.create({url,active:true});
  pilotTabs.add(tab.id);
  try {
    const header = source === 'linkedin' ? 'csrf-token' : 'x-csrf-token';
    for (let attempt=0;attempt<120;attempt++) {
      if (pilotHeaders.get(tab.id)?.[header]) break;
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    if (!pilotHeaders.get(tab.id)?.[header]) throw new Error('No authenticated job request observed. Sign in and retry.');
    await chrome.scripting.executeScript({target:{tabId:tab.id},world:'ISOLATED',files:['job-page-snapshot.js','jobboard-batch.js']});
    const options={source,query,maxJobs:message.maxJobs,batchSize:25,pacingMs:1000,headers:pilotHeaders.get(tab.id)};
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'ISOLATED',args:[options],
      func:async options=>await JobboardBatch.scan(options)});
    const result=results[0]?.result;
    if (!result) throw new Error('No scan result returned');
    result.totalElapsedMs=Date.now()-started;
    await chrome.storage.local.set({jobboardPilotResult:result,jobboardPilotState:{status:result.status,source}});
    return {result};
  } catch(e) {
    await chrome.storage.local.set({jobboardPilotState:{status:'failed',source,error:e.message}});
    throw e;
  } finally {
    pilotHeaders.delete(tab.id);
    pilotTabs.delete(tab.id);
    await chrome.tabs.remove(tab.id).catch(()=>{});
  }
}

// Retry only transient LinkedIn main-frame loss, without advancing the cursor.
async function runBoardBatch(tabId, options, task, taskId) {
  for(let attempt=0;attempt<3&&!task.cancelled;attempt++) {
    if(attempt) {
      await wait(1000);
      if(task.cancelled)return;
      await waitForTabComplete(tabId);
      if(task.cancelled)return;
      const tab=await chrome.tabs.get(tabId);
      const url=new URL(tab.url);
      if(url.origin!=='https://www.linkedin.com'||!url.pathname.startsWith('/jobs/')) {
        throw new Error('LinkedIn recovery stopped: scan tab left the job search page');
      }
    }
    try {
      await chrome.scripting.executeScript({target:{tabId},world:'ISOLATED',files:['job-page-snapshot.js','jobboard-batch.js']});
      if(task.cancelled)return;
      return await chrome.scripting.executeScript({target:{tabId},world:'ISOLATED',args:[options],
        func:async options=>await JobboardBatch.scan(options)});
    } catch(error) {
      if(task.cancelled)return;
      if(options.source!=='linkedin'||attempt===2||!/^Frame with ID 0 was removed\.?$/.test(error.message||''))throw error;
      console.warn('jobboard_frame_retry',{taskId,tabId,offset:options.startOffset,retry:attempt+1,at:new Date().toISOString()});
    }
  }
}

// Production Scan shares the same page APIs as the pilot, emitting each batch.
async function runBoardScan(command, task) {
  if (command.source === 'github-jd') return await runGitHubJDScan(command, task);
  if (command.source === 'tiktok') return await runTikTokScan(command, task);
  const source = command.source;
  if (!['linkedin', 'handshake'].includes(source)) throw new Error('Unsupported source');
  const query = String(command.query || '').trim();
  if (!command.search_url && ((!query && !command.filters) || query.length > 200)) throw new Error('Invalid search query');
  let searchUrl;
  if (command.search_url) {
    if (source !== 'linkedin') throw new Error('Search URLs require LinkedIn');
    searchUrl = JobboardBatch.linkedInSearchUrl(command.search_url).href;
  }
  if (command.filters) {
    if (source !== 'handshake') throw new Error('Category filters require Handshake');
    const names={jobRoleGroupIds:'jobRoleGroups',employmentTypeIds:'employmentTypes',jobTypeIds:'jobType'};
    const params=new URLSearchParams({sort:'posted_date_desc',per_page:'25',page:'1'});
    for(const [key,values] of Object.entries(command.filters)){
      if(!names[key] || !Array.isArray(values) || !values.length || values.some(v=>!/^\d+$/.test(String(v)))) throw new Error('Invalid category filters');
      params.set(names[key],values.join(','));
    }
    searchUrl=`https://app.joinhandshake.com/job-search?${params}`;
  }
  const tab = await chrome.tabs.create({url: searchUrl || (source === 'linkedin'
    ? `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(query)}&location=United%20States&f_TPR=r86400&sortBy=DD`
    : `https://app.joinhandshake.com/job-search?query=${encodeURIComponent(query)}&per_page=25&sort=relevance&page=1`), active:true});
  task.tabId = tab.id;
  pilotTabs.add(tab.id);
  const seen = new Set();
  const cached = new Map((command.cached_jobs || []).map(job=>[String(job.id),job]));
  const maxJobs = positiveInteger(command.max_jobs, 500);
  let offset = 0, warning = null, duplicatePages = 0;
  const onUpdated=(id,change)=>{
    if(id===tab.id&&(change.status||change.url||change.discarded!==undefined))
      console.warn('jobboard_tab_updated',{taskId:command.task_id,tabId:id,offset,status:change.status,navigated:!!change.url,discarded:change.discarded,at:new Date().toISOString()});
  };
  const onRemoved=id=>{
    if(id===tab.id)console.warn('jobboard_tab_removed',{taskId:command.task_id,tabId:id,offset,at:new Date().toISOString()});
  };
  chrome.tabs.onUpdated.addListener(onUpdated);
  chrome.tabs.onRemoved.addListener(onRemoved);
  try {
    const header = source === 'linkedin' ? 'csrf-token' : 'x-csrf-token';
    for (let attempt=0;attempt<120 && !task.cancelled;attempt++) {
      if (pilotHeaders.get(tab.id)?.[header]) break;
      await wait(500);
    }
    if (task.cancelled) return;
    if (!pilotHeaders.get(tab.id)?.[header]) throw new Error('No authenticated job request observed. Sign in and retry.');
    await waitForTabComplete(tab.id, source === 'linkedin' ? 'https://www.linkedin.com' : 'https://app.joinhandshake.com');
    if (task.cancelled) return;
    while (!task.cancelled && seen.size < maxJobs) {
      const options={source,query,searchUrl:command.search_url,filters:command.filters,sort:command.sort || 'relevance',maxJobs:Math.min(25,maxJobs-seen.size),batchSize:Math.min(25,maxJobs-seen.size),
          startOffset:offset,maxPages:1,pacingMs:0,headers:pilotHeaders.get(tab.id)};
      let results;
      let discoveredIds=[];
      if (command.discovery_gate) {
        const discovery=(await runBoardBatch(tab.id,{...options,discoverOnly:true},task,command.task_id))?.[0]?.result;
        if(task.cancelled)return;
        if(!discovery || discovery.error)throw new Error(discovery?.error || 'Discovery returned no result');
        const candidates=(discovery.discoveredRows || []).filter(row=>row.id && !seen.has(String(row.id)));
        const unique=[...new Map(candidates.map(row=>[String(row.id),row])).values()];
        discoveredIds=unique.map(row=>String(row.id));
        const restored=unique.filter(row=>cached.has(String(row.id))).map(row=>{
          const cachedJob=cached.get(String(row.id));
          const saved=row.isRepost===true ? {...cachedJob,isRepost:true,repostEvidence:row.repostEvidence,repostObservedAt:row.repostObservedAt} : cachedJob;
          const old=saved._stored_posting?.company || saved.employer?.name;
          return (!old?.trim()||old.trim().toLowerCase()==='unknown')&&row.employer?.name
            ? {...saved,employer:row.employer} : saved;
        });
        const probe=unique.filter(row=>!cached.has(String(row.id)));
        const decision=probe.length?await lookupDiscovered(command.task_id,probe):{skip_ids:[],reused_jobs:[]};
        if(task.cancelled)return;
        if(!Array.isArray(decision.skip_ids)||!Array.isArray(decision.reused_jobs))throw new Error('Invalid discovery decision');
        const remaining=probe.filter(row=>!decision.skip_ids.includes(String(row.id)));
        const details=remaining.length?(await runBoardBatch(tab.id,{...options,discoveredRows:remaining},task,command.task_id))?.[0]?.result:{jobs:[]};
        if(task.cancelled)return;
        if(!details)throw new Error('Details returned no result');
        results=[{result:{...discovery,...details,jobs:[...restored,...decision.reused_jobs,...details.jobs],
          nextOffset:discovery.nextOffset,total:discovery.total,batches:discovery.batches}}];
      } else {
        results=await runBoardBatch(tab.id,options,task,command.task_id);
      }
      if (task.cancelled) return;
      const result = results[0]?.result;
      if (!result) throw new Error('No scan result returned');
      const fresh = result.jobs.filter(job=>!seen.has(job.id));
      for(const job of fresh) seen.add(job.id);
      for(const id of discoveredIds) seen.add(id);
      if (fresh.length) {
        await waitForSocketCapacity();
        send({type:'batch',task_id:command.task_id,jobs:fresh});
      }
      if (result.error) throw new Error(`${source}: ${result.error}${result.retryAfter ? `; Retry-After: ${result.retryAfter}` : ''}`);
      if(result.warning){warning=result.warning;break;}
      if(result.stopReason==='exhausted' || (result.total != null && result.nextOffset >= result.total))break;
      duplicatePages=(!fresh.length&&!discoveredIds.length)?duplicatePages+1:0;
      if(result.nextOffset<=offset || duplicatePages>=3){warning=`Pagination stalled at offset ${offset}; more results may exist`;break;}
      offset = result.nextOffset;
      await wait(positiveInteger(command.pacing_ms,1000));
    }
  } finally {
    chrome.tabs.onUpdated.removeListener(onUpdated);
    chrome.tabs.onRemoved.removeListener(onRemoved);
    pilotHeaders.delete(tab.id); pilotTabs.delete(tab.id);
    await chrome.tabs.remove(tab.id).catch(()=>{});
  }
  if (!task.cancelled) send({type:'complete',task_id:command.task_id,warning});
}

async function runTikTokScan(command, task) {
  const targets = command.targets;
  if (!Array.isArray(targets) || !targets.length || targets.some(t => {
    try { const u = new URL(t.url); return !t.id || u.origin !== 'https://lifeattiktok.com' || !/^\/search\/\d+$/.test(u.pathname); }
    catch { return true; }
  })) throw new Error('Invalid TikTok targets');
  const tab = await chrome.tabs.create({url:targets[0].url,active:true});
  task.tabId = tab.id;
  try {
    await waitForTabComplete(tab.id);
    if (task.cancelled) return;
    await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['tiktok-batch.js']});
    const errors = [];
    for (let offset=0;offset<targets.length && !task.cancelled;offset+=24) {
      const results = await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',
        args:[targets.slice(offset,offset+24)],func:async targets=>await TikTokBatch.scan(targets,()=>{},8)});
      if (task.cancelled) return;
      const result = results[0]?.result;
      if (!result) throw new Error('TikTok returned no batch result');
      if (result.jobs.length) {
        await waitForSocketCapacity();
        send({type:'batch',task_id:command.task_id,jobs:result.jobs.map(j=>({...j,source:'tiktok'}))});
      }
      errors.push(...result.errors);
      if (result.stopped) break;
    }
    if (errors.length) throw new Error(`TikTok: ${errors.length} fetch errors; ${errors[0].error}`);
  } finally {
    await chrome.tabs.remove(tab.id).catch(()=>{});
  }
  if (!task.cancelled) send({type:'complete',task_id:command.task_id});
}
