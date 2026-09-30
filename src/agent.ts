/** The complete agent loop. Typed choices, observable state, bounded execution. */

import { decodeBase64 } from "@std/encoding/base64";
import { join } from "@std/path";
import { Browser } from "./browser.ts";
import { JevError, StalePage } from "./errors.ts";
import { canonicalJson } from "./json.ts";
import { actionSpace, choose, fieldContext, fieldText } from "./model.ts";
import { MAX_STEPS } from "./questions.ts";
import type {
  AgentSnapshot,
  AgentState,
  BrowserLike,
  CdpTransport,
  Env,
  Fetch,
  PageState,
  TextHelperInfo,
} from "./types.ts";

/** The model functions the agent calls. Replaceable in tests. */
export interface ModelFns {
  choose: typeof choose;
  fieldText: typeof fieldText;
}

/** Options for an agent around an already-open browser. */
export interface AgentInit {
  /** Write a JPEG per observation into this directory. Implies screenshots. */
  recordDir?: string | null;
  /** Capture screenshots on each observation. The model never consumes them. */
  screenshots?: boolean;
  /** Fetch used by the model calls. */
  fetch?: Fetch;
  /** Environment used by the model calls, instead of Deno.env. */
  env?: Env;
  /** Model function overrides. */
  model?: Partial<ModelFns>;
}

/** Options for {@link Agent.create}. */
export interface AgentOptions extends AgentInit {
  /** CDP connection to open the tab on; defaults to the shared connection. */
  cdp?: CdpTransport;
  /** Custom browser factory, used instead of {@link Browser.open}. */
  openBrowser?: (url: string) => Promise<BrowserLike>;
}

/** Generated text awaiting execution, keyed by the canonical JSON of its entire helper input. */
export interface PendingText {
  key: string;
  text: string;
  helper: TextHelperInfo;
}

/** Jev chooses an observed action. Code owns execution. */
export class Agent implements AsyncDisposable {
  /** The browser this agent drives. Cast to {@link Browser} for CDP-specific members. */
  readonly browser: BrowserLike;
  /** Observable JSON state. */
  state: AgentState;
  /** Generated text reusable only while its helper input is identical. */
  pendingText: PendingText | null = null;
  /** Frame directory, when recording. */
  readonly recordDir: string | null;
  /** Whether observations capture screenshots. */
  readonly screenshots: boolean;
  #model: ModelFns;
  #modelOptions: { fetch?: Fetch; env?: Env };

  /** Wrap an open browser and its first observation. Performs no I/O. */
  constructor(browser: BrowserLike, task: string, page: PageState, init: AgentInit = {}) {
    const plan = [task];
    this.browser = browser;
    this.recordDir = init.recordDir ? init.recordDir : null;
    this.screenshots = Boolean(init.screenshots) || Boolean(this.recordDir);
    this.#model = { choose, fieldText, ...init.model };
    this.#modelOptions = { fetch: init.fetch, env: init.env };
    this.state = {
      goal: plan.join("\n"),
      page,
      decision: null,
      history: [],
      status: "ready",
      plan,
      plan_index: 0,
      decisions: [],
      text_calls: [],
      elapsed_ms: 0,
      started_at: null,
      record: Boolean(this.recordDir),
    };
  }

  /** Open `url`, observe it, and return an agent for one natural-language goal. */
  static async create(url: string, goals: string | readonly string[], opts: AgentOptions = {}): Promise<Agent> {
    const task = typeof goals === "string" ? goals.trim() : goals.join("\n").trim();
    if (!task) throw new JevError("Supply a task");
    const browser = opts.openBrowser ? await opts.openBrowser(url) : await Browser.open(url, { cdp: opts.cdp });
    const screenshots = Boolean(opts.screenshots) || Boolean(opts.recordDir);
    try {
      const page = await browser.observe({ screenshot: screenshots });
      const agent = new Agent(browser, task, page, opts);
      if (agent.recordDir) {
        await Deno.mkdir(agent.recordDir, { recursive: true });
        await Deno.writeFile(join(agent.recordDir, "000000.jpg"), decodeBase64(page.screenshot ?? ""));
      }
      return agent;
    } catch (error) {
      await browser.close().catch(() => {});
      throw error;
    }
  }

  /** State plus the current element table. */
  snapshot(): AgentSnapshot {
    return { ...this.state, elements: actionSpace(this.state.page.actions).elements };
  }

  #elapsed(): number {
    return Math.round(performance.now() - (this.state.started_at ?? performance.now()));
  }

  /** Run `tick`, `predict`, or `act`. */
  async command(name: string, body: Record<string, unknown> = {}): Promise<AgentSnapshot> {
    const state = this.state;
    const browser = this.browser;
    if (name === "tick") {
      try {
        await this.command("predict", {});
        return await this.command("act", { fingerprint: state.page.fingerprint });
      } catch (error) {
        if (!(error instanceof StalePage)) throw error;
        state.decision = null;
        state.status = "ready";
        state.page = await browser.observe({ screenshot: this.screenshots });
        state.elapsed_ms = this.#elapsed();
        return this.snapshot();
      }
    } else if (name === "predict") {
      state.started_at ??= performance.now();
      if (!(await browser.fresh(state.page))) state.page = await browser.observe({ screenshot: this.screenshots });
      state.decision = null;
      if (state.status === "done" || state.status === "blocked") {
        throw new JevError("This run has stopped. Start a fresh demo.");
      }
      if (state.decisions.length >= MAX_STEPS * 2) throw new JevError("Reached the demo's model-call budget");
      const page = state.page;
      const decision = await this.#model.choose(page, state.goal, state.history, this.#modelOptions);
      state.decision = decision;
      state.decisions.push({ ...decision, fingerprint: page.fingerprint, elapsed_ms: this.#elapsed() });
      state.status = "predicted";
    } else if (name === "act") {
      const decision = state.decision;
      const page = state.page;
      if (!decision || body.fingerprint !== page.fingerprint) throw new JevError("Observe and choose before acting");
      // Consume once, before any mutation or model call. A retry cannot double-click.
      state.decision = null;
      const selected = decision.choice;
      if (selected === "DONE" || selected === "BLOCKED") {
        if (!(await browser.fresh(page))) {
          state.status = "ready";
          throw new StalePage("Page changed since the decision. Choose again.");
        }
        state.status = selected === "DONE" ? "done" : "blocked";
        state.plan_index = Number(selected === "DONE");
        state.elapsed_ms = this.#elapsed();
        return this.snapshot();
      }
      const action = page.actions.find((a) => a.id === selected);
      if (!action) throw new JevError("Decision does not match an observed action; no action executed.");
      if (state.history.length >= MAX_STEPS) {
        state.status = "blocked";
        throw new JevError(`Stopped at the ${MAX_STEPS}-action demo budget`);
      }
      let text: string | null = null;
      let helper: TextHelperInfo | null = null;
      if (action.kind === "fill") {
        if (!(await browser.fresh(page))) throw new StalePage("Page changed before text generation. Choose again.");
        const context = fieldContext(state.goal, action, page, state.history);
        const key = canonicalJson(context);
        if (this.pendingText && this.pendingText.key === key) {
          ({ text, helper } = this.pendingText);
        } else {
          const [value, info] = await this.#model.fieldText(context, this.#modelOptions);
          text = value;
          helper = info;
          this.pendingText = { key, text: value, helper: info };
          state.text_calls.push({ ...info, field: action.label, value });
        }
      }
      // Browser.act checks freshness immediately before input, including after text generation.
      await browser.act(action, page, text);
      this.pendingText = null;
      state.elapsed_ms = this.#elapsed();
      // Record execution before observing. A stale post-action observation must not erase the action.
      state.history.push({
        step: state.history.length + 1,
        action: action.label,
        kind: action.kind,
        choice: selected,
        probability: decision.probabilities[selected],
        confidence: decision.confidence,
        latency_ms: decision.latency_ms,
        text,
        text_helper: helper ? helper.model : null,
        text_latency_ms: helper ? helper.latency_ms : 0,
        operation: decision.operation,
        target: decision.target,
        page_changed: null,
        url: page.url,
        usage: decision.usage,
        executed_ms: this.#elapsed(),
        elapsed_ms: state.elapsed_ms,
      });
      state.page = await browser.observe({ screenshot: this.screenshots });
      state.elapsed_ms = this.#elapsed();
      const last = state.history[state.history.length - 1];
      last.page_changed = state.page.fingerprint !== page.fingerprint;
      last.url = state.page.url;
      last.elapsed_ms = state.elapsed_ms;
      if (state.record && this.recordDir) {
        await Deno.writeFile(
          join(this.recordDir, `${String(state.elapsed_ms).padStart(6, "0")}.jpg`),
          decodeBase64(state.page.screenshot ?? ""),
        );
      }
      const repeated = state.history.slice(-3);
      state.status = repeated.length === 3 && repeated.every((h) => h.page_changed === false && h.kind !== "wait")
        ? "blocked"
        : "ready";
    } else {
      throw new JevError("Unknown command");
    }
    return this.snapshot();
  }

  /** Tick until done or blocked, yielding each snapshot. */
  async *run(): AsyncGenerator<AgentSnapshot> {
    while (this.state.status !== "done" && this.state.status !== "blocked") yield await this.command("tick");
  }

  /** Close the browser tab. */
  close(): Promise<void> {
    return this.browser.close();
  }

  /** Close on `await using`. */
  [Symbol.asyncDispose](): Promise<void> {
    return this.close();
  }
}
