/** One direct Chrome DevTools Protocol WebSocket. No daemon, no retries, no resends. */

import { join } from "@std/path";
import { CdpError, CdpTimeout } from "./errors.ts";
import type { CdpParams, CdpResult, CdpSendOptions, CdpTransport, Env } from "./types.ts";

type Handler = (params: CdpResult, sessionId?: string) => void;

interface Pending {
  resolve: (value: CdpResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  method: string;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** A CDP connection over a native WebSocket. */
export class CdpConnection implements CdpTransport {
  /** Called after each command settles, with its method and duration in milliseconds. */
  onCall?: (method: string, ms: number) => void;
  /** Resolves once the socket has closed, for any reason. */
  readonly whenClosed: Promise<void>;

  #ws: WebSocket;
  #next = 0;
  #closed = false;
  #pending = new Map<number, Pending>();
  #handlers = new Map<string, Set<Handler>>();
  #markClosed!: () => void;

  private constructor(ws: WebSocket) {
    this.#ws = ws;
    this.whenClosed = new Promise((resolve) => this.#markClosed = resolve);
    ws.addEventListener("message", (event) => this.#message(event));
    ws.addEventListener("close", () => this.#shutdown());
    ws.addEventListener("error", () => {
      if (ws.readyState === WebSocket.CLOSED) this.#shutdown();
    });
  }

  /** Open a connection. There is no handshake timeout: Chrome may be waiting on its Allow prompt. */
  static connect(wsUrl: string): Promise<CdpConnection> {
    return new Promise((resolve, reject) => {
      const failure = () => new CdpError(`Could not connect to Chrome DevTools at ${wsUrl}`);
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl);
      } catch {
        reject(failure());
        return;
      }
      const connection = new CdpConnection(ws);
      let opened = false;
      ws.addEventListener("open", () => {
        opened = true;
        resolve(connection);
      }, { once: true });
      const fail = () => {
        if (!opened) reject(failure());
      };
      ws.addEventListener("error", fail, { once: true });
      ws.addEventListener("close", fail, { once: true });
    });
  }

  /** Whether the connection is closed or closing. */
  get closed(): boolean {
    return this.#closed;
  }

  /** Send one command. It is sent exactly once; a timeout rejects and ignores any late reply. */
  send<T = CdpResult>(method: string, params?: CdpParams, opts?: CdpSendOptions): Promise<T> {
    if (this.#closed) return Promise.reject(new CdpError("CDP connection closed", undefined, method));
    const id = ++this.#next;
    const message: Record<string, unknown> = { id, method, params: params ?? {} };
    if (opts?.sessionId !== undefined && !method.startsWith("Target.")) message.sessionId = opts.sessionId;
    const timeoutMs = opts?.timeoutMs ?? 5000;
    const started = performance.now();
    return new Promise<T>((resolve, reject) => {
      const settle = () => {
        this.#pending.delete(id);
        clearTimeout(entry.timer);
        this.onCall?.(method, performance.now() - started);
      };
      const entry: Pending = {
        resolve: (value) => {
          settle();
          resolve(value as T);
        },
        reject: (error) => {
          settle();
          reject(error);
        },
        timer: setTimeout(() => {
          entry.reject(new CdpTimeout(`${method} timed out after ${timeoutMs / 1000}s`));
        }, timeoutMs),
        method,
      };
      this.#pending.set(id, entry);
      try {
        this.#ws.send(JSON.stringify(message));
      } catch {
        entry.reject(new CdpError("CDP connection closed", undefined, method));
      }
    });
  }

  /** Subscribe to a CDP event. Returns an unsubscribe function. */
  on(method: string, handler: (params: CdpResult, sessionId?: string) => void): () => void {
    let handlers = this.#handlers.get(method);
    if (!handlers) this.#handlers.set(method, handlers = new Set());
    handlers.add(handler);
    return () => {
      handlers.delete(handler);
    };
  }

  /** Close the socket and reject pending commands. Idempotent. */
  close(): Promise<void> {
    if (this.#ws.readyState === WebSocket.CLOSED) {
      this.#shutdown();
      return Promise.resolve();
    }
    this.#closed = true;
    try {
      this.#ws.close();
    } catch {
      this.#shutdown();
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fallback = new Promise<void>((resolve) => {
      timer = setTimeout(() => {
        this.#shutdown();
        resolve();
      }, 2000);
    });
    return Promise.race([this.whenClosed, fallback]).finally(() => clearTimeout(timer));
  }

  #message(event: MessageEvent) {
    if (typeof event.data !== "string") return;
    let message: CdpResult;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (typeof message.id === "number") {
      const entry = this.#pending.get(message.id);
      if (!entry) return;
      if (message.error) {
        const { message: text, data, code } = message.error;
        entry.reject(new CdpError(`${text}${data ? `: ${data}` : ""}`, code, entry.method));
      } else {
        entry.resolve(message.result ?? {});
      }
      return;
    }
    if (typeof message.method === "string") {
      for (const handler of [...(this.#handlers.get(message.method) ?? [])]) {
        try {
          handler(message.params ?? {}, message.sessionId);
        } catch (error) {
          console.error(error);
        }
      }
    }
  }

  #shutdown() {
    this.#closed = true;
    for (const entry of [...this.#pending.values()]) {
      entry.reject(new CdpError("CDP connection closed", undefined, entry.method));
    }
    this.#pending.clear();
    this.#markClosed();
  }
}

/** A shared, reference-counted CDP connection. */
export interface CdpLease {
  readonly cdp: CdpConnection;
  /** Release this reference. The last release closes the socket. Idempotent. */
  release(): Promise<void>;
}

let slot: Promise<CdpConnection> | null = null;
let refs = 0;
const defaultConnect = (): Promise<CdpConnection> => resolveWsUrl().then((url) => CdpConnection.connect(url));
let connectShared = defaultConnect;

/** Internal, for offline tests: replace how the shared connection opens. Returns a restore function. */
export function _setSharedConnect(connect: () => Promise<CdpConnection>): () => void {
  connectShared = connect;
  return () => connectShared = defaultConnect;
}

/** Acquire the process-wide CDP connection, connecting on first use. */
export async function acquireCdp(): Promise<CdpLease> {
  if (!slot) {
    const connecting: Promise<CdpConnection> = connectShared();
    slot = connecting;
    refs = 0;
    connecting.then(
      (cdp) =>
        cdp.whenClosed.then(() => {
          if (slot === connecting) {
            slot = null;
            refs = 0;
          }
        }),
      () => {
        if (slot === connecting) slot = null;
      },
    );
  }
  const current = slot;
  // Count this reference before awaiting, so a concurrent last release cannot close the socket under us.
  refs++;
  let cdp: CdpConnection;
  try {
    cdp = await current;
  } catch (error) {
    if (slot === current) refs--;
    throw error;
  }
  let released = false;
  return {
    cdp,
    async release() {
      if (released) return;
      released = true;
      if (slot !== current) return;
      refs--;
      if (refs <= 0) {
        slot = null;
        refs = 0;
        await cdp.close();
      }
    },
  };
}

const MAC_PROFILES = [
  "Library/Application Support/Google/Chrome",
  "Library/Application Support/Google/Chrome Canary",
  "Library/Application Support/Comet",
  "Library/Application Support/Arc/User Data",
  "Library/Application Support/Dia/User Data",
  "Library/Application Support/Microsoft Edge",
  "Library/Application Support/Microsoft Edge Beta",
  "Library/Application Support/Microsoft Edge Dev",
  "Library/Application Support/Microsoft Edge Canary",
  "Library/Application Support/BraveSoftware/Brave-Browser",
  "Library/Application Support/BraveSoftware/Brave-Origin",
];
const LINUX_PROFILES = [
  ".config/google-chrome",
  ".config/chromium",
  ".config/chromium-browser",
  ".config/microsoft-edge",
  ".config/microsoft-edge-beta",
  ".config/microsoft-edge-dev",
  ".var/app/org.chromium.Chromium/config/chromium",
  ".var/app/com.google.Chrome/config/google-chrome",
  ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
  ".var/app/com.microsoft.Edge/config/microsoft-edge",
];
const WINDOWS_PROFILES = [ // relative to %LOCALAPPDATA%; SxS = Canary channel
  "Google/Chrome/User Data",
  "Google/Chrome SxS/User Data",
  "Google/Chrome Beta/User Data",
  "Google/Chrome Dev/User Data",
  "Chromium/User Data",
  "Microsoft/Edge/User Data",
  "Microsoft/Edge Beta/User Data",
  "Microsoft/Edge Dev/User Data",
  "Microsoft/Edge SxS/User Data",
  "BraveSoftware/Brave-Browser/User Data",
];

const readEnv = (env: Env | undefined, key: string): string | undefined => {
  const value = env ? env[key] : Deno.env.get(key);
  return value ? value : undefined;
};

/** Chrome-family profile directories that may contain DevToolsActivePort. */
export function profileDirs(os: typeof Deno.build.os = Deno.build.os, env?: Env): string[] {
  const home = readEnv(env, "HOME") ?? readEnv(env, "USERPROFILE") ?? "";
  if (os === "windows") {
    const local = readEnv(env, "LOCALAPPDATA") ?? join(home, "AppData/Local");
    return WINDOWS_PROFILES.map((p) => join(local, p));
  }
  if (os === "darwin") return MAC_PROFILES.map((p) => join(home, p));
  return LINUX_PROFILES.map((p) => join(home, p));
}

const PERMISSION_BLOCKED =
  "permission-blocked: Chrome is reachable, but the per-session Allow remote debugging popup has not been accepted";

/** GET `<base>/json/version`. Network failures throw; the body is always consumed. */
async function version(url: string, timeoutMs: number): Promise<{ status: number; ws?: string }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    const text = await response.text();
    if (response.status !== 200) return { status: response.status };
    let ws: unknown;
    try {
      ws = JSON.parse(text)?.webSocketDebuggerUrl;
    } catch {
      ws = undefined;
    }
    return { status: 200, ws: typeof ws === "string" && ws ? ws : undefined };
  } finally {
    clearTimeout(timer);
  }
}

async function activePort(dir: string): Promise<{ port: string; wsPath: string } | null> {
  let content: string;
  try {
    content = await Deno.readTextFile(join(dir, "DevToolsActivePort"));
  } catch (error) {
    if (error instanceof Deno.errors.NotFound || error instanceof Deno.errors.NotADirectory) return null;
    throw error;
  }
  const lines = content ? content.split(/\r\n|\r|\n/) : [];
  const port = (lines[0] ?? "").trim();
  if (!port) return null;
  return { port, wsPath: (lines[1] ?? "").trim() };
}

async function wsFromActivePort(httpUrl: string, dirs: string[]): Promise<string | null> {
  let parsed: URL;
  try {
    parsed = new URL(httpUrl);
  } catch {
    return null;
  }
  const wantPort = parsed.port;
  if (!wantPort) return null;
  let host = parsed.hostname || "127.0.0.1";
  if (host.includes(":") && !host.startsWith("[")) host = `[${host}]`;
  for (const dir of dirs) {
    const active = await activePort(dir);
    if (active && active.port === wantPort && active.wsPath) return `ws://${host}:${active.port}${active.wsPath}`;
  }
  return null;
}

/**
 * Find Chrome's browser WebSocket URL: BU_CDP_WS, then BU_CDP_URL, then a profile's DevToolsActivePort,
 * then ports 9222/9223.
 */
export async function resolveWsUrl(env?: Env): Promise<string> {
  const direct = readEnv(env, "BU_CDP_WS");
  if (direct) return direct;
  const dirs = profileDirs(Deno.build.os, env);
  const httpUrl = readEnv(env, "BU_CDP_URL");
  if (httpUrl) {
    const base = httpUrl.replace(/\/+$/, "");
    const deadline = performance.now() + 30_000;
    let lastError: unknown = null;
    while (performance.now() < deadline) {
      try {
        const result = await version(`${base}/json/version`, 5000);
        if (result.status === 200 && result.ws) return result.ws;
        if (result.status === 403) throw new CdpError(PERMISSION_BLOCKED);
        if (result.status === 404) {
          const ws = await wsFromActivePort(httpUrl, dirs);
          if (ws) return ws;
        }
        lastError = result.status === 200 ? "missing webSocketDebuggerUrl" : `HTTP ${result.status}`;
      } catch (error) {
        if (error instanceof CdpError) throw error;
        lastError = error instanceof Error ? error.message : error;
      }
      await sleep(1000);
    }
    throw new CdpError(
      `BU_CDP_URL=${httpUrl} unreachable after 30s: ${lastError} -- is the dedicated automation Chrome running?`,
    );
  }
  const deadline = performance.now() + 3000;
  while (true) {
    for (const dir of dirs) {
      const active = await activePort(dir);
      if (!active) continue;
      try {
        const result = await version(`http://127.0.0.1:${active.port}/json/version`, 1000);
        if (result.status === 200 && result.ws) return result.ws;
        if (result.status === 403) throw new CdpError(PERMISSION_BLOCKED);
        if (result.status === 404 && active.wsPath) return `ws://127.0.0.1:${active.port}${active.wsPath}`;
      } catch (error) {
        if (error instanceof CdpError) throw error;
      }
    }
    if (performance.now() >= deadline) break;
    await sleep(200);
  }
  for (const port of [9222, 9223]) {
    try {
      const result = await version(`http://127.0.0.1:${port}/json/version`, 1000);
      if (result.status === 200 && result.ws) return result.ws;
      if (result.status === 403) throw new CdpError(PERMISSION_BLOCKED);
    } catch (error) {
      if (error instanceof CdpError) throw error;
    }
  }
  throw new CdpError(
    `DevToolsActivePort not found in ${
      JSON.stringify(dirs)
    } — enable chrome://inspect/#remote-debugging, or set BU_CDP_WS / BU_CDP_URL`,
  );
}
