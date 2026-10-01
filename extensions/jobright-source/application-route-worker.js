/* One shared queue across every application-resolution call. Never borrow scan tabs. */
const APPLICATION_ROUTE_CAPACITY = 10;
const applicationRouteQueue = [];
let applicationRouteWorkers = 0;
let applicationRouteLateCreates = 0;
let applicationRouteRuleId = 1000000;
const applicationRouteNextHostStart = new Map();

function applicationPublicURL(value) {
  const u = new URL(value), host = u.hostname.toLowerCase();
  // URL normalizes alternative IPv4 spellings before this guard.
  if (!['http:','https:'].includes(u.protocol) || u.username || u.password ||
      !host.includes('.') || host.endsWith('.localhost') || host.endsWith('.local') ||
      host.startsWith('[') || /^(?:0|10|127)\.|^169\.254\.|^172\.(?:1[6-9]|2\d|3[01])\.|^192\.168\.|^100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|^198\.(?:18|19)\.|^(?:22[4-9]|23\d|24\d|25[0-5])\./.test(host))
    throw new Error('Application target must be a public HTTP(S) URL');
  return u;
}

function cancelApplicationRouteTask(task) {
  task.applicationCancel?.();
  pumpApplicationRoutes();
}

async function runApplicationRouteScan(command, task) {
  if (!Array.isArray(command.targets) || !command.targets.length || command.targets.length > 100)
    throw new Error('Invalid application resolution targets');
  for (const target of command.targets) {
    if (!target.id || typeof target.url !== 'string') throw new Error('Invalid application target');
    applicationPublicURL(target.url);
  }
  task.tabIds = new Set();
  let cancel;
  task.applicationCancellation = new Promise(resolve => {cancel = resolve;});
  task.applicationCancel = cancel;
  try {
    await Promise.all(command.targets.map(target => new Promise((resolve, reject) => {
      applicationRouteQueue.push({target, command, task, resolve, reject});
      pumpApplicationRoutes();
    })));
    if (!task.cancelled) send({type:'complete', task_id:command.task_id});
  } finally {
    delete task.applicationCancel;
    delete task.applicationCancellation;
  }
}

function pumpApplicationRoutes() {
  for (let index = applicationRouteQueue.length - 1; index >= 0; index--) {
    if (applicationRouteQueue[index].task.cancelled) applicationRouteQueue.splice(index, 1)[0].resolve();
  }
  while (applicationRouteWorkers + applicationRouteLateCreates < APPLICATION_ROUTE_CAPACITY && applicationRouteQueue.length) {
    const item = applicationRouteQueue.shift();
    applicationRouteWorkers++;
    void processApplicationRoute(item).then(item.resolve, item.reject).finally(() => {
      applicationRouteWorkers--;
      pumpApplicationRoutes();
    });
  }
}

async function readApplicationRoute(target, task, timeout) {
  const stopped = Promise.race([timeout, task.applicationCancellation]).then(() => {
    throw new Error(task.cancelled ? 'Application cancelled' : 'Application page timed out after 30000 ms');
  });
  const bounded = operation => Promise.race([operation, stopped]);
  const destination = applicationPublicURL(target.url);
  const now = Date.now();
  const starts = Math.max(now, applicationRouteNextHostStart.get(destination.hostname) || 0);
  applicationRouteNextHostStart.set(destination.hostname, starts + 1000);
  if (starts > now) await bounded(wait(starts - now));
  if (task.cancelled) return;
  if (!await bounded(chrome.permissions.contains({origins:[`${destination.origin}/*`]}))) throw new Error('Missing extension permission');
  let tab, ruleIds = [];
  try {
    // Start blank so guards are installed before the very first network request.
    const creating = chrome.tabs.create({url:'about:blank', active:false});
    try {tab = await bounded(creating);} catch(error) {
      // Chrome cannot abort tabs.create. Keep its capacity reservation until a
      // late-created blank tab is closed, so the global tab limit still holds.
      applicationRouteLateCreates++;
      void creating.then(late => chrome.tabs.remove(late.id).catch(()=>{})).catch(()=>{}).finally(()=>{
        applicationRouteLateCreates--;pumpApplicationRoutes();
      });
      throw error;
    }
    task.tabIds.add(tab.id);
    if (task.cancelled) return;
    const blockId = ++applicationRouteRuleId, allowId = ++applicationRouteRuleId, privateId = ++applicationRouteRuleId;
    ruleIds = [blockId, allowId, privateId];
    const installing = chrome.declarativeNetRequest.updateSessionRules({addRules:[
      {id:blockId, priority:1, action:{type:'block'}, condition:{tabIds:[tab.id],resourceTypes:['main_frame']}},
      // Unknown cross-origin redirects cannot be DNS-checked before Chrome requests
      // them. Block them; the backend may validate and read an observed link later.
      {id:allowId, priority:2, action:{type:'allow'}, condition:{tabIds:[tab.id],regexFilter:'^https?://' + destination.hostname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(:[0-9]+)?/',resourceTypes:['main_frame']}},
      {id:privateId, priority:3, action:{type:'block'}, condition:{tabIds:[tab.id],regexFilter:'^https?://([^/@]*@)?(localhost|[^/:]+\\.localhost|[^/:]+\\.local|\\[|0\\.|10\\.|127\\.|169\\.254\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.|100\\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\\.)'}},
    ]});
    try {await bounded(installing);} catch(error) {
      void installing.then(()=>chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:ruleIds})).catch(()=>{});
      throw error;
    }
    if (task.cancelled) return;
    await bounded(chrome.tabs.update(tab.id, {url:target.url}));
    await bounded(waitForTabComplete(tab.id, null, true));
    // Hydrated anchors commonly appear after document readiness. Poll boundedly;
    // return the final document even when no Apply link becomes observable.
    for (let attempt=0; attempt<12 && !task.cancelled; attempt++) {
      const current = await bounded(chrome.tabs.get(tab.id));
      const observed = applicationPublicURL(current.url);
      if (observed.hostname !== destination.hostname) throw new Error('Unvalidated browser redirect');
      await bounded(chrome.scripting.executeScript({target:{tabId:tab.id},world:'ISOLATED',files:['job-page-snapshot.js','application-route.js']}));
      const result = (await bounded(chrome.scripting.executeScript({target:{tabId:tab.id},world:'ISOLATED',
        func:() => ApplicationRoutePage.capture(document, location.href)})))[0]?.result;
      if (!result?.html || !result.url) throw new Error('No application page snapshot');
      if (applicationPublicURL(result.url).hostname !== destination.hostname) throw new Error('Unvalidated browser snapshot redirect');
      // An unrelated Apply link/video iframe is not target readiness. Preserve
      // the complete bounded hydration window; the backend decides ownership.
      if (attempt===11) return result;
      await bounded(wait(500));
    }
  } finally {
    if (tab) {
      await chrome.tabs.remove(tab.id).catch(()=>{});
      task.tabIds.delete(tab.id);
    }
    if (ruleIds.length) await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds:ruleIds}).catch(()=>{});
  }
}

async function processApplicationRoute({target,command,task}) {
  let timer, row;
  const timeout = new Promise(resolve => {timer=setTimeout(()=>resolve({timeout:true}), 30000);});
  try {
    let result;
    try {
      result = await readApplicationRoute(target, task, timeout);
      row = {...target,...result,source:'application-resolution'};
    } catch (error) {
      if (task.cancelled) return;
      row = {...target,source:'application-resolution',error:error.message,
        error_code:/permission/i.test(error.message)?'missing_permission':/timed out|loading/i.test(error.message)?'page_timeout':'parse_failed'};
    }
    if (task.cancelled) return;
    if (!row.error) {
      try {
        await Promise.race([waitForSocketCapacity(), timeout.then(()=>{throw new Error('Application socket timed out');}),
          task.applicationCancellation.then(()=>{throw new Error('Application cancelled');})]);
      } catch(error) {if(task.cancelled)return;throw error;}
    }
    if (!task.cancelled) send({type:'batch',task_id:command.task_id,jobs:[row]});
  } finally {clearTimeout(timer);}
}
