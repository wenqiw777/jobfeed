// Enrich only explicit GitHub targets. Never discover extra jobs or submit forms.
async function runGitHubJDScan(command,task) {
  if(!Array.isArray(command.targets)||!command.targets.length)throw new Error('Missing GitHub job targets');
  const groups=new Map();
  for(const target of command.targets){
    const u=new URL(target.url);
    if(!['http:','https:'].includes(u.protocol)||!target.id||!target.title)throw new Error('Invalid GitHub job target');
    if(!groups.has(u.origin))groups.set(u.origin,[]);
    groups.get(u.origin).push(target);
  }
  async function emit(rows) {
    if(task.cancelled)return;
    await waitForSocketCapacity();
    if(task.cancelled)return;
    send({type:'batch',task_id:command.task_id,jobs:rows});
  }
  function errorCode(error){
    if(/Source temporarily unavailable: Workday maintenance page/i.test(error))return 'transient';
    if(/permission|cannot access contents/i.test(error))return 'missing_permission';
    if(/\bHTTP 401\b|login expired/i.test(error))return 'auth_required';
    if(/\bHTTP 429\b/i.test(error))return 'rate_limited';
    if(/GitHubJD is not defined/i.test(error))return 'script_unavailable';
    if(/No tab with id|Invalid tab ID/i.test(error))return 'transient';
    if(/timed? ?out|did not finish loading|did not become readable|frame.*removed/i.test(error))return 'page_timeout';
    return 'parse_failed';
  }
  function failed(target,error){return {...target,source:'github-jd',description:null,error,error_code:errorCode(error)};}
  async function inject(tabId){
    const tab=chrome.tabs.get?await chrome.tabs.get(tabId):null;
    if(tab?.url){const destination=new URL(tab.url);
      if(destination.origin==='https://community.workday.com'&&destination.pathname==='/maintenance-page')throw new Error('Source temporarily unavailable: Workday maintenance page');
      const origin=destination.origin;if(!await chrome.permissions.contains({origins:[`${origin}/*`]}))throw new Error(`Missing extension permission after navigation: ${origin}`);}
    await chrome.scripting.executeScript({target:{tabId},world:'ISOLATED',files:['job-page-snapshot.js','tiktok-batch.js','github-jd-entities.js','github-jd.js']});
  }
  async function rendered(tabId,target,navigate=true,followFrame=true){
    if(navigate){await chrome.tabs.update(tabId,{url:target.url});await waitForTabComplete(tabId,null,true);}
    if(task.cancelled)return;
    await inject(tabId);
    let result,previousBlocks;
    for(let attempt=0;attempt<120&&!task.cancelled;attempt++){
      const values=await chrome.scripting.executeScript({target:{tabId},world:'ISOLATED',args:[target],func:target=>{
        const frames=[...document.querySelectorAll('iframe[src]')].map(n=>n.src).filter(u=>/^https:\/\/(?:boards|job-boards)(?:\.[a-z]{2})?\.greenhouse\.io\//.test(u));
        const ats_urls=[...document.querySelectorAll('script[src]')].map(n=>n.src).filter(u=>/^https:\/\/(?:boards|job-boards)(?:\.[a-z]{2})?\.greenhouse\.io\//.test(u));
        try{return {...GitHubJD.rendered(target),frames,ats_urls};}catch(e){return {error:e.message,frames,ats_urls};}
      }});
      result=values[0]?.result;
      if(result?.description)return result;
      if(result?.page_snapshot){
        const current=JSON.stringify(result.page_snapshot.blocks);
        if(attempt>=4 && result.page_snapshot.blocks?.length && current===previousBlocks)return {...target,...result,source:'github-jd'};
        previousBlocks=current;
      }
      if(result?.error==='GitHubJD is not defined'){
        await waitForTabComplete(tabId,null,true);await inject(tabId);
      }
      if(attempt<119)await wait(500);
    }
    // Custom company sites commonly embed the actual Greenhouse job document.
    for(const frameUrl of (followFrame?result?.frames||[]:[]).slice(0,1)){
      if(task.cancelled)return;
      const u=new URL(frameUrl);
      if(!await chrome.permissions.contains({origins:[`${u.origin}/*`]}))break;
      await chrome.tabs.update(tabId,{url:frameUrl});await waitForTabComplete(tabId,null,true);await inject(tabId);
      // Frame navigation completion does not guarantee React has rendered its JD.
      // Reuse bounded readiness polling; never recurse into further frames.
      return await rendered(tabId,target,false,false);
    }
    return {...failed(target,result?.page_snapshot && !result.page_snapshot.blocks?.length?'Page content did not become readable':result?.error||'No readable job description'),...(result?.page_snapshot?{page_snapshot:result.page_snapshot}:{}),frames:result?.frames||[],ats_urls:result?.ats_urls||[]};
  }
  const queue=[];
  for(const [origin,targets] of groups){
    if(task.cancelled)return;
    if(!await chrome.permissions.contains({origins:[`${origin}/*`]})){
      await emit(targets.map(t=>failed(t,`Missing extension permission: ${origin}`)));continue;
    }
    queue.push(...targets);
  }
  task.tabIds=new Set();
  let next=0;
  async function worker(){
    let tab;
    try{
      while(!task.cancelled&&next<queue.length){
        const target=queue[next++];
        let row;
        try{
          if(!tab){
            tab=await chrome.tabs.create({url:target.url,active:false});
            task.tabIds.add(tab.id);
            if(task.cancelled)return;
            await waitForTabComplete(tab.id,null,true);
            row=await rendered(tab.id,target,false);
          }else row=await rendered(tab.id,target);
        }catch(e){
          if(tab&&!task.cancelled&&/No tab with id|Invalid tab ID/i.test(e.message)){
            // Chrome may discard/remove a worker tab. Never reuse a dead ID
            // for the remaining jobs; retry this target once in a fresh tab.
            task.tabIds.delete(tab.id);tab=null;
            try {
              tab=await chrome.tabs.create({url:target.url,active:false});
              task.tabIds.add(tab.id);
              await waitForTabComplete(tab.id,null,true);
              row=await rendered(tab.id,target,false);
            }catch(retryError){
              row=failed(target,retryError.message);
              if(tab&&/No tab with id|Invalid tab ID/i.test(retryError.message)){
                task.tabIds.delete(tab.id);tab=null;
              }
            }
          }else if(tab&&!task.cancelled&&errorCode(e.message)==='page_timeout'){
            try {await waitForTabComplete(tab.id,null,true);row=await rendered(tab.id,target,false);}
            catch(retryError){row=failed(target,retryError.message);}
          }else row=failed(target,e.message);
        }
        if(task.cancelled)return;
        await emit([row]);
        // Never navigate an old job document into a new target: Chrome can
        // resolve tabs.update before replacing the previous document.
        if(tab){await chrome.tabs.remove(tab.id).catch(()=>{});task.tabIds.delete(tab.id);tab=null;}
        // Cool down this tab only; other workers keep processing their pages.
        // Pace failures too, so bad URLs cannot cause rapid navigation loops.
        await wait(1000);
      }
    }finally{
      if(tab){await chrome.tabs.remove(tab.id).catch(()=>{});task.tabIds.delete(tab.id);}
    }
  }
  const workers=await Promise.allSettled(Array.from({length:Math.min(4,queue.length)},()=>worker()));
  const failure=workers.find(result=>result.status==='rejected');
  if(failure)throw failure.reason;
  if(!task.cancelled)send({type:'complete',task_id:command.task_id});
}
