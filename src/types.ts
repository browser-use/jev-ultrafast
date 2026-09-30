/** JSON shapes shared by the browser, model, agent, and inspector. Keys match the Python originals. */

/** An operation that needs an observed target. */
export type Operation = "CLICK" | "TYPE_TEXT" | "SELECT";

/** Agent run status. */
export type Status = "ready" | "predicted" | "done" | "blocked";

/** A fetch implementation compatible with the global `fetch`. */
export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** Environment variables, injected so tests need no env permission. */
export type Env = Record<string, string | undefined>;

/** Viewport rectangle of an observed element. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A click, fill, or native-select option on an observed DOM node. */
export interface ElementAction {
  id: string;
  kind: "click" | "fill" | "select";
  node: number;
  role?: string;
  label: string;
  rect?: Rect;
  value: string;
  current_value?: string;
  checked?: string;
  selected?: string;
  expanded?: string;
}

/** A page scroll control. */
export interface ScrollAction {
  id: string;
  kind: "scroll";
  label: string;
  delta: number;
}

/** A short wait for the page to update. */
export interface WaitAction {
  id: string;
  kind: "wait";
  label: string;
}

/** Any observed action. */
export type Action = ElementAction | ScrollAction | WaitAction;

/** The atomic page observation produced by snapshot.js, plus fingerprint and optional screenshot. */
export interface PageState {
  url: string;
  title: string;
  w: number;
  h: number;
  text: string;
  scroll: { y: number; height: number };
  actions: Action[];
  marker: unknown[];
  page_key: unknown[];
  guards: Record<string, unknown[] | null>;
  omitted_actions: number;
  fingerprint: string;
  /** Base64 JPEG, present only when requested. */
  screenshot?: string;
}

/** A native dropdown option offered as a SELECT target. */
export interface ElementOption {
  index: string;
  label: string;
  value: string;
}

/** One indexed element in the model's element table. */
export interface Element {
  role?: string;
  value?: string;
  checked?: string;
  selected?: string;
  expanded?: string;
  index: string;
  label: string;
  operations: Operation[];
  options?: ElementOption[];
}

/** A validated TypeSafe choice answer. */
export interface ChoiceAnswer {
  type?: "choice";
  choice: string;
  confidence: number;
  probabilities: Record<string, number>;
}

/** A TypeSafe choice question as sent in the request body. */
export interface ChoiceQuestionBody {
  type: "choice";
  criteria: Record<string, unknown>;
  instructions: Record<string, unknown>;
}

/** The exact TypeSafe System One request body. */
export interface SystemOneBody {
  model: string;
  state: {
    page: { url: string; title: string; text: string };
    elements: Element[];
    recent_actions: {
      action: string | null;
      kind: string | null;
      text: string | null;
      page_changed: boolean | null;
    }[];
  };
  questions: Record<string, ChoiceQuestionBody>;
}

/** One model decision: an operation plus, when needed, the matching target. */
export interface Decision {
  /** An observed action id (`e3`, `scroll_down`, `wait`) or `DONE`/`BLOCKED`. */
  choice: string;
  operation: string;
  target: string | null;
  confidence: number;
  probabilities: Record<string, number>;
  operation_probabilities: Record<string, number>;
  target_probabilities: Record<string, number>;
  target_confidence: number | null;
  raw_answers: Record<string, unknown>;
  model: string;
  usage: Record<string, unknown>;
  latency_ms: number;
  request: SystemOneBody;
}

/** A decision as logged in agent state. */
export interface DecisionRecord extends Decision {
  fingerprint: string;
  elapsed_ms: number;
}

/** Metadata about one text-helper call. */
export interface TextHelperInfo {
  model: string;
  latency_ms: number;
  usage?: Record<string, unknown>;
}

/** A logged text-helper call. */
export interface TextCall extends TextHelperInfo {
  field: string;
  value: string;
}

/** The complete text-helper input. Generated text is reused only while this is identical. */
export interface FieldContext {
  goal: string;
  field: { label: string | null; role: string | null; value: string | null };
  page: { title: string; text: string };
  recent_actions: { action: string | null; text: string | null }[];
}

/** One executed action. Logged before the post-action observation. */
export interface HistoryEntry {
  step: number;
  action: string;
  kind: Action["kind"];
  choice: string;
  probability: number;
  confidence: number;
  latency_ms: number;
  text: string | null;
  text_helper: string | null;
  text_latency_ms: number;
  operation: string;
  target: string | null;
  page_changed: boolean | null;
  url: string;
  usage: Record<string, unknown>;
  executed_ms: number;
  elapsed_ms: number;
}

/** Observable agent state (JSON). */
export interface AgentState {
  goal: string;
  page: PageState;
  decision: Decision | null;
  history: HistoryEntry[];
  status: Status;
  plan: string[];
  plan_index: number;
  decisions: DecisionRecord[];
  text_calls: TextCall[];
  elapsed_ms: number;
  started_at: number | null;
  record: boolean;
  scenario?: string;
}

/** Agent state plus the current element table. */
export type AgentSnapshot = AgentState & { elements: Element[] };

/** Result of an executed browser action. */
export interface ActResult {
  executed: string;
}

/** Observation options. */
export interface ObserveOptions {
  /** Capture a JPEG screenshot. Default true on Browser. */
  screenshot?: boolean;
}

/** The browser surface the agent needs. */
export interface BrowserLike {
  readonly target: string | null;
  readonly session: string;
  observe(opts?: ObserveOptions): Promise<PageState>;
  fresh(page: PageState, action?: Action | null): Promise<boolean>;
  act(action: Action, page: PageState, text?: string | null): Promise<ActResult>;
  close(): Promise<void>;
}

/** CDP command parameters. */
export type CdpParams = Record<string, unknown>;
/** CDP command result. */
// deno-lint-ignore no-explicit-any
export type CdpResult = Record<string, any>;

/** Per-command CDP options. */
export interface CdpSendOptions {
  sessionId?: string;
  timeoutMs?: number;
}

/** A CDP connection: commands, events, close. */
export interface CdpTransport {
  send<T = CdpResult>(method: string, params?: CdpParams, opts?: CdpSendOptions): Promise<T>;
  on(method: string, handler: (params: CdpResult, sessionId?: string) => void): () => void;
  close(): Promise<void>;
}

/** Observe request for {@link BrowserOperation}. */
export interface ObserveRequest {
  operation: "observe";
  session: string;
  screenshot?: boolean;
}

/** Act request for {@link BrowserOperation}. */
export interface ActRequest {
  operation: "act";
  session: string;
  action: Action;
  text?: string | null;
}

/** A browser operation request. */
export type BrowserOperationRequest = ObserveRequest | ActRequest;

/** Executes one observe or act request against a CDP session. */
export type BrowserOperation = (
  request: BrowserOperationRequest,
  cdp: CdpTransport,
) => Promise<PageState | ActResult>;
