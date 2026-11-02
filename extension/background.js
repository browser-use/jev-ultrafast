/* WebSocket bridge client: relays host commands to the tab and task requests to the host. */

const DEFAULT_PORT = 8767;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let ws = null;
let connected = false;
let nextId = 1;
let lastState = null;
let activeRun = null;

function settings() {
  return browser.storage.local.get({ port: DEFAULT_PORT, token: "" });
}

function setStatus(value) {
  connected = value;
  browser.runtime.sendMessage({ type: "status", connected }).catch(() => {});
}

function send(message) {
  if (!ws || ws.readyState !== 1) throw new Error("Host is not connected");
  ws.send(JSON.stringify(message));
}

async function connect() {
  const config = await settings();
  const socket = new WebSocket(`ws://127.0.0.1:${config.port}`);
  ws = socket;
  socket.onopen = () => {
    setStatus(true);
    send({ type: "hello", token: config.token || undefined });
  };
  socket.onclose = () => {
    if (ws === socket) {
      setStatus(false);
      setTimeout(connect, 2000);
    }
  };
  socket.onmessage = (event) => {
    handleHostMessage(JSON.parse(event.data)).catch((error) => {
      console.error("bridge message failed", error);
    });
  };
}

async function handleHostMessage(message) {
  if (message.type === "welcome") {
    if (message.state) {
      lastState = message.state;
      browser.runtime.sendMessage({ type: "state", state: lastState }).catch(() => {});
    }
    return;
  }
  if (message.type === "state") {
    lastState = message.state;
    if (message.state && message.state.status) activeRun = message.state.status;
    browser.runtime.sendMessage({ type: "state", state: lastState }).catch(() => {});
    return;
  }
  if (message.type === "error") {
    browser.runtime.sendMessage(message).catch(() => {});
    return;
  }
  if (message.type === "models" || message.type === "approval_request" || message.type === "delta") {
    browser.runtime.sendMessage(message).catch(() => {});
    return;
  }
  if (Number.isInteger(message.id)) {
    try {
      const result = await runCommand(message);
      send({ id: message.id, ok: true, result });
    } catch (error) {
      send({ id: message.id, ok: false, error: String((error && error.message) || error) });
    }
  }
}

async function ensureContent(tabId) {
  try {
    await browser.tabs.sendMessage(tabId, { cmd: "ping" });
  } catch {
    await browser.tabs.executeScript(tabId, { file: "content.js" });
  }
}

async function waitForLoad(tabId, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const tab = await browser.tabs.get(tabId);
      if (tab.status === "complete") return true;
    } catch {
      return false; // the tab is gone
    }
    await sleep(150);
  }
  return false;
}

async function readSnapshot(tabId) {
  const results = await browser.tabs.executeScript(tabId, { file: "snapshot.js" });
  return results && results[0];
}

async function runCommand(message) {
  if (message.type === "open") {
    const tab = await browser.tabs.create({ url: message.url });
    return { tabId: tab.id };
  }
  if (!Number.isInteger(message.tabId)) throw new Error("No tab attached");
  if (message.type === "observe") {
    await waitForLoad(message.tabId);
    await ensureContent(message.tabId);
    await browser.tabs.sendMessage(message.tabId, { cmd: "settle" });
    let state = null;
    for (let attempt = 0; attempt < 10 && !state; attempt++) {
      state = await readSnapshot(message.tabId);
      if (!state) await sleep(20);
    }
    if (!state) throw new Error("Document is navigating");
    if (message.screenshot) {
      const data = await browser.tabs.captureTab(message.tabId, { format: "jpeg", quality: 72 });
      state.screenshot = data.split(",")[1];
    }
    return state;
  }
  if (message.type === "fresh") {
    if (message.node !== undefined) {
      return browser.tabs.sendMessage(message.tabId, { cmd: "freshNode", node: message.node });
    }
    const state = await readSnapshot(message.tabId);
    return state ? state.marker : null;
  }
  if (message.type === "act") {
    return browser.tabs.sendMessage(message.tabId, { cmd: "act", action: message.action, text: message.text });
  }
  throw new Error(`Unknown command ${message.type}`);
}

browser.runtime.onMessage.addListener((message) => {
  if (!message || !message.cmd) return undefined;
  if (message.cmd === "status") {
    return Promise.resolve({ connected, state: lastState, run: activeRun });
  }
  if (message.cmd === "run") {
    return (async () => {
      let [tab] = await browser.tabs.query({ active: true, currentWindow: true });
      if (!tab || !/^https?:/i.test(tab.url || "")) {
        // The current tab (new tab page, about:*, ...) cannot be scripted:
        // open a normal page and run the mission there instead of failing.
        tab = await browser.tabs.create({ url: "https://duckduckgo.com/" });
      }
      send({ type: "run", goal: message.goal, url: tab.url, tabId: tab.id });
      return { ok: true };
    })().catch((error) => ({ error: String(error.message || error) }));
  }
  if (message.cmd === "check") {
    try {
      send({ type: "check" });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "stop") {
    try {
      send({ type: "stop" });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "models") {
    try {
      send({ type: "models", refresh: !!message.refresh });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "select-model") {
    try {
      send({ type: "models.select", role: message.role, provider: message.provider, model: message.model });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "params") {
    try {
      const payload = message.preset
        ? { type: "params.set", preset: message.preset }
        : { type: "params.set", role: message.role, params: message.params || {} };
      send(payload);
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "profile") {
    try {
      send({ type: `profile.${message.action}`, name: message.name });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  if (message.cmd === "approval") {
    try {
      send({ type: "approval_response", id: message.id, approved: !!message.approved });
      return Promise.resolve({ ok: true });
    } catch (error) {
      return Promise.resolve({ error: String(error.message || error) });
    }
  }
  return undefined;
});

browser.browserAction.onClicked.addListener(() => {
  browser.sidebarAction.open();
});

connect();
