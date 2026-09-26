importScripts("jobboard-batch.js", "github-jd-worker.js", "pilot-worker.js");
const DEFAULT_BASE_URL = "http://127.0.0.1:7654";
const PROTOCOL_VERSION = 1;
const RECONNECT_DELAY_MS = 3000;
const HEARTBEAT_MS = 20000;
const MAX_BACKOFF_MS = 30000;
const BLOCKED_BACKOFF_MS = [30000, 60000, 120000, 240000, 300000];

let socket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let connectionState = "disconnected";
const activeTasks = new Map();

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("jobfeed-jobright-reconnect", { periodInMinutes: 0.5 });
  void connect();
});

chrome.runtime.onStartup.addListener(() => void connect());
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "jobfeed-jobright-reconnect" && !isConnected()) {
    void connect();
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "status") {
    void getBaseUrl().then((baseUrl) =>
      sendResponse({ state: connectionState, baseUrl }),
    );
    return true;
  }
  if (message?.type === "configure" && typeof message.baseUrl === "string") {
    void configure(message.baseUrl).then(sendResponse);
    return true;
  }
  return false;
});

async function configure(rawBaseUrl) {
  const baseUrl = normalizeBaseUrl(rawBaseUrl);
  await chrome.storage.local.set({ jobfeedBaseUrl: baseUrl });
  closeSocket();
  await connect();
  return { state: connectionState, baseUrl };
}

async function connect() {
  if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) {
    return;
  }
  clearTimeout(reconnectTimer);
  connectionState = "connecting";
  const baseUrl = await getBaseUrl();
  // Storage yields: startup, installation and reconnect callbacks may overlap.
  if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  const wsUrl = `${baseUrl.replace(/^http/, "ws")}/api/sources/jobright/bridge`;
  const nextSocket = new WebSocket(wsUrl);
  socket = nextSocket;
  nextSocket.addEventListener("open", () => {
    if (socket !== nextSocket) return;
    connectionState = "handshaking";
    send({ type: "hello", protocol: PROTOCOL_VERSION, sources: ["jobright", "linkedin", "handshake", "linkedin-search-results", "tiktok", "github-jd", "discovery-gate-v1"] });
  });
  nextSocket.addEventListener("message", (event) => {
    if (socket === nextSocket) void handleMessage(event.data);
  });
  nextSocket.addEventListener("close", () => {
    if (socket === nextSocket) {
      socket = null;
      connectionState = "disconnected";
      for (const [taskId,task] of activeTasks) {
        cancelDiscovery(taskId);
        task.cancelled = true;
        if (task.tabId != null) void chrome.tabs.remove(task.tabId).catch(() => {});
        for (const tabId of task.tabIds || []) void chrome.tabs.remove(tabId).catch(() => {});
      }
      stopHeartbeat();
      scheduleReconnect();
    }
  });
  nextSocket.addEventListener("error", () => {
    if (socket === nextSocket) connectionState = "error";
  });
}

async function handleMessage(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    return;
  }
  if (message.type === "ready" && message.protocol === PROTOCOL_VERSION) {
    connectionState = "connected";
    startHeartbeat();
    return;
  }
  if (message.type === "pong") {
    return;
  }
  if (message.type === "discovery_result") {
    resolveDiscovery(message);
    return;
  }
  if (message.type === "cancel" && typeof message.task_id === "string") {
    const task = activeTasks.get(message.task_id);
    if (task) {
      cancelDiscovery(message.task_id);
      task.cancelled = true;
      if (task.tabId != null) await chrome.tabs.remove(task.tabId).catch(() => {});
      await Promise.all([...task.tabIds || []].map(tabId => chrome.tabs.remove(tabId).catch(() => {})));
    }
    return;
  }
  if (["start_scan", "start_board_scan"].includes(message.type) && typeof message.task_id === "string") {
    const source = message.type === "start_scan" ? "jobright" : message.source;
    const lane = ["github-jd", "tiktok"].includes(source) ? "enrichment" : source;
    if (activeTasks.has(message.task_id) || [...activeTasks.values()].some(task => task.lane === lane) || pilotRunning) {
      send({
        type: "error",
        task_id: message.task_id,
        error: `${lane} browser lane is busy`,
      });
      return;
    }
    const task = { cancelled: false, lane };
    activeTasks.set(message.task_id, task);
    try {
      if (message.type === "start_board_scan") await runBoardScan(message, task);
      else await runScan(message, task);
    } catch (error) {
      if (task.cancelled) return;
      send({
        type: "error",
        task_id: message.task_id,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      cancelDiscovery(message.task_id);
      activeTasks.delete(message.task_id);
    }
  }
}

async function runScan(command, task) {
  const maxJobs = positiveInteger(command.max_jobs, 1000);
  const batchSize = positiveInteger(command.batch_size, 20);
  const pacingMs = positiveInteger(command.pacing_ms, 1000);
  const tab = await chrome.tabs.create({
    url: "https://jobright.ai/jobs/recommend",
    active: false,
  });
  if (typeof tab.id !== "number") throw new Error("Chrome did not create a Jobright tab");
  const tabId = tab.id;
  task.tabId = tabId;
  const seen = new Set();
  let position = 0;
  let duplicatePages = 0, warning = null;
  let lastRequestStartedAt = 0;
  try {
    await waitForTabComplete(tabId, "https://jobright.ai");
    while (!task.cancelled && seen.size < maxJobs) {
      const remainingDelay = Math.max(0, pacingMs - (Date.now() - lastRequestStartedAt));
      if (remainingDelay > 0) await wait(remainingDelay);
      lastRequestStartedAt = Date.now();
      const page = await fetchPageWithBackoff(tabId, position, batchSize, task);
      const jobs = Array.isArray(page.jobs) ? page.jobs : [];
      const fresh = [];
      for (const job of jobs) {
        const jobId = job?.jobResult?.jobId;
        if (jobId == null) continue;
        const key = String(jobId);
        if (seen.has(key)) continue;
        seen.add(key);
        fresh.push(job);
        if (seen.size >= maxJobs) break;
      }
      if (fresh.length > 0) {
        await waitForSocketCapacity();
        send({ type: "batch", task_id: command.task_id, jobs: fresh });
      }
      if (jobs.length === 0 || jobs.length < batchSize) break;
      duplicatePages=fresh.length?0:duplicatePages+1;
      if(duplicatePages>=3){warning=`Jobright pagination repeated 3 pages at position ${position}; more results may exist`;break;}
      position += jobs.length;
    }
  } finally {
    await chrome.tabs.remove(tabId).catch(() => undefined);
  }
  if (!task.cancelled) send({ type: "complete", task_id: command.task_id, warning });
}

async function fetchPageWithBackoff(tabId, position, count, task) {
  let backoffMs = 2000;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (task.cancelled) throw new Error("Jobright scan cancelled");
    let page;
    try {page = await fetchPage(tabId, position, count);}
    catch(error){
      if(!/frame.*removed|frame.*detached|document.*unloaded/i.test(String(error)) || attempt>=2)throw error;
      await waitForTabComplete(tabId, "https://jobright.ai");await wait(1000);continue;
    }
    if (page.status === 200 && page.success === true) return page;
    if (page.status === 401) {
      throw new Error("Jobright login expired; sign in in Chrome and retry");
    }
    if (page.status !== 403 && page.status !== 429 && page.status < 500) {
      throw new Error(page.error || `Jobright request failed (${page.status})`);
    }
    if (attempt === 5) {
      if (page.status === 403) {
        throw new Error(
          "Jobright access remained blocked (403) after long backoff; wait before retrying",
        );
      }
      if (page.status === 429) {
        throw new Error("Jobright remained rate limited (429) after backoff");
      }
      throw new Error(`Jobright remained unavailable (${page.status})`);
    }
    // A 403 with a still-valid signed-in page is Jobright's temporary access
    // block. Keep the current position and retry it after a long cooldown;
    // only incrementing `position` after success makes the scan resumable.
    if (page.status === 403) {
      await wait(BLOCKED_BACKOFF_MS[attempt]);
    } else {
      await wait(backoffMs);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    }
  }
  throw new Error("Jobright request failed");
}

async function fetchPage(tabId, position, count) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [position, count],
    func: async (pagePosition, pageCount) => {
      const url = new URL("/swan/recommend/list/jobs", window.location.origin);
      url.searchParams.set("refresh", "false");
      url.searchParams.set("sortCondition", "0");
      url.searchParams.set("position", String(pagePosition));
      url.searchParams.set("count", String(pageCount));
      url.searchParams.set("syncRerank", "false");
      const response = await fetch(url, { credentials: "include" });
      const text = await response.text();
      let payload = null;
      try {
        payload = JSON.parse(text);
      } catch {
        // The caller reports the HTTP status and a bounded response excerpt.
      }
      return {
        status: response.status,
        success: payload?.success === true,
        errorCode: payload?.errorCode ?? null,
        error: payload?.errorMsg || text.slice(0, 200),
        jobs: payload?.result?.jobList ?? [],
      };
    },
  });
  const result = results[0]?.result;
  if (!result || typeof result !== "object") {
    throw new Error("Jobright page did not return a readable response");
  }
  return result;
}

async function waitForTabComplete(tabId, sourceOrigin = null, allowInteractive = false) {
  // Register before checking status: onUpdated can fire while tabs.get resolves.
  await new Promise((resolve, reject) => {
    let finished = false, timer, poll;
    const finish = error => {
      if (finished) return;
      finished = true;
      clearTimeout(timer); clearTimeout(poll);
      chrome.tabs.onUpdated.removeListener(listener);
      error ? reject(error) : resolve();
    };
    const listener = (id, change) => {
      if (id === tabId && change.status === "complete") void check();
    };
    const check = async () => {
      try {
        const tab = await chrome.tabs.get(tabId);
        if (finished) return;
        if (!sourceOrigin && tab.status === "complete") return finish();
        if (sourceOrigin || allowInteractive) {
          // API work needs a usable source document, not all advertising resources.
          const rows = await chrome.scripting.executeScript({target:{tabId},world:"MAIN",
            func:() => ({readyState:document.readyState,origin:location.origin})});
          const page = rows[0]?.result;
          if ((!sourceOrigin || page?.origin === sourceOrigin) && ["interactive","complete"].includes(page.readyState)) return finish();
        }
      } catch (error) {
        if (/No tab|Invalid tab|closed/i.test(error.message || "")) return finish(error);
      }
      if (!finished) { clearTimeout(poll); poll = setTimeout(check, 250); }
    };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(() => finish(new Error("Source page did not finish loading within 30000 ms")), 30000);
    void check();
  });
}

async function waitForSocketCapacity() {
  while (socket && socket.bufferedAmount > 1_000_000) await wait(50);
  if (!isConnected()) throw new Error("Jobfeed bridge disconnected");
}

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error("Jobfeed bridge is not connected");
  }
  socket.send(JSON.stringify(message));
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (isConnected()) send({ type: "ping" });
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

function closeSocket() {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  stopHeartbeat();
  socket?.close();
  socket = null;
  connectionState = "disconnected";
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => void connect(), RECONNECT_DELAY_MS);
}

function isConnected() {
  return socket?.readyState === WebSocket.OPEN && connectionState === "connected";
}

async function getBaseUrl() {
  const stored = await chrome.storage.local.get("jobfeedBaseUrl");
  return normalizeBaseUrl(stored.jobfeedBaseUrl || DEFAULT_BASE_URL);
}

function normalizeBaseUrl(value) {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Use an http(s) Jobfeed URL");
  return url.origin;
}

function positiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

void connect();
