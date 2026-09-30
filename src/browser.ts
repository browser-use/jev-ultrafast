/** Observed actions through one direct CDP session. Code owns execution; the model never emits selectors. */

import { acquireCdp, type CdpLease } from "./cdp.ts";
import { CdpError, CdpTimeout, JevError, StalePage } from "./errors.ts";
import { canonicalJson, isTruthy, jsonEqual } from "./json.ts";
import type {
  Action,
  ActResult,
  BrowserLike,
  BrowserOperation,
  BrowserOperationRequest,
  CdpParams,
  CdpResult,
  CdpTransport,
  ObserveOptions,
  PageState,
} from "./types.ts";

/** Atomically read visible content and controls, preserving actual DOM node identity. */
export const READ_STATE: string = await (await fetch(new URL("./snapshot.js", import.meta.url))).text();
/** Read only the freshness marker of the current page. */
export const MARKER: string = `(() => { const state=${READ_STATE}; return state?.marker ?? null; })()`;

// Read-only settle wait after input. Copied verbatim from the Python original.
const AFTER_INPUT = String.raw`(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(`;

// Code-owned node IDs refer to actual observed elements, never model-generated selectors.
const RESOLVE_TARGET = String.raw`(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(`;

const MODIFIER = Deno.build.os === "darwin" ? 4 : 2;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** SHA-256 hex of the page's url, text, actions, and scroll position. */
export async function fingerprint(state: Pick<PageState, "url" | "text" | "actions" | "scroll">): Promise<string> {
  const content = { url: state.url, text: state.text, actions: state.actions, scroll: state.scroll };
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(content)));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Execute one observe or act request. Never retries; an act request mutates the page at most once. */
export const browserOperation: BrowserOperation = async function browserOperation(
  request: BrowserOperationRequest,
  cdp: CdpTransport,
): Promise<PageState | ActResult> {
  const session = request.session;
  const call = (method: string, params?: CdpParams) => cdp.send(method, params, { sessionId: session });
  const evaluate = async (expression: string): Promise<unknown> => {
    const result = await call("Runtime.evaluate", { expression, returnByValue: true });
    if (isTruthy(result.exceptionDetails)) {
      if (request.operation === "act" && request.action.kind === "select") {
        throw new JevError("Dropdown execution was interrupted; inspect before retrying.");
      }
      throw new StalePage("Document changed during evaluation");
    }
    return result.result?.value ?? null;
  };

  if (request.operation === "act") {
    const action = request.action;
    const kind = action.kind;
    if (kind === "scroll") {
      await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: 550, y: 650, deltaX: 0, deltaY: action.delta });
    } else if (kind !== "wait") {
      if (!Number.isInteger(action.node)) throw new JevError("Invalid observed node");
      const text = request.text;
      if (kind === "fill" && typeof text !== "string") {
        throw new JevError("TYPE_TEXT needs generated text; nothing typed.");
      }
      const target = await evaluate(RESOLVE_TARGET + JSON.stringify(action) + ")") as { x: number; y: number } | null;
      if (target === null) {
        if (kind === "select") throw new JevError("Dropdown execution was not confirmed; inspect before retrying.");
        throw new StalePage("Target changed or is covered. Observe again.");
      }
      if (kind !== "select") {
        const { x, y } = target;
        for (const type of ["mousePressed", "mouseReleased"]) {
          await call("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
        }
        if (kind === "fill") {
          await call("Input.dispatchKeyEvent", {
            type: "keyDown",
            key: "a",
            code: "KeyA",
            modifiers: MODIFIER,
            commands: ["selectAll"],
          });
          await call("Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: MODIFIER });
          await call("Input.insertText", { text });
        }
      }
    }
    return { executed: action.id };
  }

  const info = await evaluate(READ_STATE) as PageState | null;
  if (info === null) throw new StalePage("Document is navigating");
  info.fingerprint = await fingerprint(info);
  if (request.screenshot !== false) {
    info.screenshot = (await call("Page.captureScreenshot", { format: "jpeg", quality: 72 })).data;
  }
  return info;
};

/** An owned background tab with one flattened CDP session. */
export class Browser implements BrowserLike {
  /** The owned target id; null after close or detach. */
  target: string | null;
  /** The flattened CDP session id. */
  readonly session: string;
  /** The underlying CDP connection. */
  readonly cdp: CdpTransport;
  /** The executor. Replaceable in tests. */
  operation: BrowserOperation = browserOperation;
  /** The last executed input action, awaited read-only before the next observation. */
  afterInput: Action | null = null;
  private lease: CdpLease | undefined;

  /** Wrap an attached session. Performs no I/O. */
  constructor(cdp: CdpTransport, target: string, session: string, lease?: CdpLease) {
    this.cdp = cdp;
    this.target = target;
    this.session = session;
    this.lease = lease;
  }

  /** Open `url` in a new background tab. Uses `opts.cdp`, or the shared connection. */
  static async open(url: string, opts: { cdp?: CdpTransport } = {}): Promise<Browser> {
    const lease = opts.cdp ? undefined : await acquireCdp();
    const cdp: CdpTransport = opts.cdp ?? lease!.cdp;
    let target: string | undefined;
    try {
      target = (await cdp.send("Target.createTarget", { url: "about:blank", background: true })).targetId as string;
      const session = (await cdp.send("Target.attachToTarget", { targetId: target, flatten: true }))
        .sessionId as string;
      const browser = new Browser(cdp, target, session, lease);
      await browser.call("Emulation.setDeviceMetricsOverride", {
        width: 1120,
        height: 780,
        deviceScaleFactor: 1,
        mobile: false,
      });
      // Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
      await browser.call("Emulation.setFocusEmulationEnabled", { enabled: true });
      await browser.call("Page.navigate", { url });
      const deadline = performance.now() + 15_000;
      while (performance.now() < deadline) {
        if (await browser.evaluate("document.readyState") === "complete") break;
        await sleep(20);
      }
      return browser;
    } catch (error) {
      if (target) await cdp.send("Target.closeTarget", { targetId: target }).catch(() => {});
      await lease?.release();
      throw error;
    }
  }

  /** Send a command on this tab's session. */
  call<T = CdpResult>(method: string, params?: CdpParams, opts: { timeoutMs?: number } = {}): Promise<T> {
    return this.cdp.send<T>(method, params, { ...opts, sessionId: this.session });
  }

  /** Evaluate an expression by value. A page exception means the document changed. */
  async evaluate(expression: string): Promise<unknown> {
    const response = await this.call("Runtime.evaluate", { expression, returnByValue: true });
    if (isTruthy(response.exceptionDetails)) throw new StalePage("Document changed during evaluation");
    return response.result?.value ?? null;
  }

  /** Observe the page atomically. Screenshots default on; `{ screenshot: false }` is one CDP call. */
  async observe(opts: ObserveOptions = {}): Promise<PageState> {
    const screenshot = opts.screenshot ?? true;
    if (this.afterInput) {
      const action = this.afterInput;
      this.afterInput = null;
      // This is read-only and happens after execution was logged, even if navigation interrupts or outlasts it.
      try {
        await this.call("Runtime.evaluate", {
          expression: AFTER_INPUT + JSON.stringify(action) + ")",
          awaitPromise: true,
          returnByValue: true,
        });
      } catch (error) {
        if (!(error instanceof CdpError || error instanceof CdpTimeout)) throw error;
      }
    }
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        return await this.operation({ operation: "observe", session: this.session, screenshot }, this.cdp) as PageState;
      } catch (error) {
        // Observation is read-only, so a slow navigation (timeout) is re-read like a settling page.
        if (!(error instanceof StalePage || error instanceof CdpTimeout) || attempt === 9) throw error;
        await sleep(20);
      }
    }
    throw new StalePage("Page did not settle");
  }

  /** Whether `page` still describes the document; for clicks and selects, the target node's guard. */
  async fresh(page: PageState, action?: Action | null): Promise<boolean> {
    if (action && (action.kind === "click" || action.kind === "select")) {
      const node = action.node;
      if (!Number.isInteger(node)) return false;
      const current = await this.evaluate(
        "(() => { const c=window.__jevFast; " +
          `return c ? [c.pageKey(),c.guard(c.nodes.get(${node}))] : null; })()`,
      );
      return jsonEqual(current, [page.page_key, page.guards[String(node)] ?? null]);
    }
    return jsonEqual(await this.evaluate(MARKER), page.marker);
  }

  /** Execute one observed action once. Freshness is checked immediately before input. */
  async act(action: Action, page: PageState, text: string | null = null): Promise<ActResult> {
    if (!(await this.fresh(page, action))) throw new StalePage("Page changed since this decision. Observe again.");
    if (action.kind === "wait") await sleep(100);
    const result = await this.operation(
      { operation: "act", session: this.session, action, text: text ?? null },
      this.cdp,
    ) as ActResult;
    this.afterInput = action.kind !== "wait" ? action : null;
    return result;
  }

  /** Close the owned tab and release the shared connection. Idempotent. */
  async close(): Promise<void> {
    const target = this.target;
    this.target = null;
    try {
      if (target) await this.cdp.send("Target.closeTarget", { targetId: target });
    } finally {
      await this.detach();
    }
  }

  /** Release the shared connection without closing the tab. */
  async detach(): Promise<void> {
    this.target = null;
    const lease = this.lease;
    this.lease = undefined;
    await lease?.release();
  }
}
