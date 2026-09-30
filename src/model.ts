/** TypeSafe makes choices; an optional small OpenAI-compatible model writes field values. */

import { APIConnectionError, APIError, type SystemOneRequest, TypeSafeClient, TypeSafeError } from "@typesafe-ai/sdk";
import { JevError, ModelError } from "./errors.ts";
import { NEXT_ACTION, TARGET, TEXT_VALUE } from "./questions.ts";
import { pythonJsonDumps } from "./json.ts";
import type {
  Action,
  ChoiceAnswer,
  ChoiceQuestionBody,
  Decision,
  Element,
  ElementAction,
  Env,
  Fetch,
  FieldContext,
  HistoryEntry,
  Operation,
  PageState,
  ScrollAction,
  SystemOneBody,
  TextHelperInfo,
  WaitAction,
} from "./types.ts";

/** Injected dependencies, so tests need neither network nor env permission. */
export interface ModelOptions {
  fetch?: Fetch;
  env?: Env;
}

/** The dynamic action space for one observation. */
export interface ActionSpace {
  /** One entry per observed element, indexed from "1". */
  elements: Element[];
  /** Per operation: target index (`"3"`, or `"3:2"` for a dropdown option) → observed action. */
  targets: Partial<Record<Operation, Record<string, ElementAction>>>;
  /** Page controls keyed by upper-cased id (`SCROLL_DOWN`, `SCROLL_UP`, `WAIT`). */
  controls: Record<string, ScrollAction | WaitAction>;
}

const INVALID = "Invalid TypeSafe response; no action executed.";

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const readEnv = (opts: ModelOptions, key: string): string | undefined => opts.env ? opts.env[key] : Deno.env.get(key);

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const getOr = (object: object, key: string, fallback: unknown): unknown =>
  key in object ? (object as Record<string, unknown>)[key] : fallback;

/** POST JSON with bounded retries on 429/503/529 only. Used by the text helper. */
export async function postJson(url: string, key: string, body: unknown, fetchFn?: Fetch): Promise<unknown> {
  const send: Fetch = fetchFn ?? ((input, init) => fetch(input, init));
  for (let attempt = 0; attempt < 3; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25_000);
    try {
      let response: Response;
      try {
        response = await send(url, {
          method: "POST",
          headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
      } catch {
        throw new ModelError("Model connection failed; no action executed.");
      }
      if ([429, 529, 503].includes(response.status) && attempt < 2) {
        await response.body?.cancel().catch(() => {});
        clearTimeout(timer);
        await sleep(500 * 2 ** attempt);
        continue;
      }
      if (response.status >= 400) {
        await response.body?.cancel().catch(() => {});
        throw new ModelError(`Model provider returned HTTP ${response.status}; no action executed.`);
      }
      let text: string;
      try {
        text = await response.text();
      } catch {
        throw new ModelError("Model connection failed; no action executed.");
      }
      try {
        return JSON.parse(text);
      } catch {
        throw new JevError("Model provider returned invalid JSON; no action executed.");
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw new ModelError("Model unavailable");
}

/** Validate a TypeSafe choice answer against the offered ids. */
export function validateChoice(answer: unknown, ids: readonly string[]): ChoiceAnswer {
  let valid = false;
  if (isObject(answer) && isObject(answer.probabilities) && "confidence" in answer) {
    const probabilities = answer.probabilities;
    const values = Object.values(probabilities);
    const numbers = [...values, answer.confidence];
    const keys = Object.keys(probabilities);
    const choice = answer.choice;
    valid = typeof choice === "string" && ids.includes(choice) &&
      keys.length === new Set(ids).size && keys.every((k) => ids.includes(k)) &&
      numbers.every((n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1) &&
      Math.abs((values as number[]).reduce((a, b) => a + b, 0) - 1) < 0.02 &&
      (probabilities[choice] as number) >= Math.max(...(values as number[])) - 1e-6;
  }
  if (!valid) throw new JevError(INVALID);
  return answer as unknown as ChoiceAnswer;
}

/** One index per observed element; each operation has its own valid target choices. */
export function actionSpace(actions: readonly Action[]): ActionSpace {
  const elements: Element[] = [];
  const indices = new Map<number, string>();
  const targets: Partial<Record<Operation, Record<string, ElementAction>>> = {};
  const controls: Record<string, ScrollAction | WaitAction> = {};
  const operations: Record<string, Operation> = { click: "CLICK", fill: "TYPE_TEXT", select: "SELECT" };
  for (const action of actions) {
    if (action.kind === "scroll" || action.kind === "wait") {
      controls[action.id.toUpperCase()] = action;
      continue;
    }
    const node = action.node;
    if (!indices.has(node)) {
      const index = String(elements.length + 1);
      indices.set(node, index);
      const element: Record<string, unknown> = {};
      for (const k of ["role", "value", "checked", "selected", "expanded"]) {
        if (k in action) element[k] = (action as unknown as Record<string, unknown>)[k];
      }
      Object.assign(element, { index, label: action.label.split(" → ")[0], operations: [] });
      if (action.kind === "select") {
        element.value = getOr(action, "current_value", "");
        element.options = [];
      }
      elements.push(element as unknown as Element);
    }
    const index = indices.get(node)!;
    const operation = operations[action.kind];
    const group = targets[operation] ??= {};
    const element = elements[Number(index) - 1];
    if (!element.operations.includes(operation)) element.operations.push(operation);
    let target = index;
    if (action.kind === "select") {
      target = `${index}:${element.options!.length + 1}`;
      element.options!.push({ index: target, label: action.label, value: action.value });
    }
    group[target] = action;
  }
  return { elements, targets, controls };
}

const LABELS: Record<Operation, string> = {
  CLICK: "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
  TYPE_TEXT: "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
  SELECT: "Select an observed dropdown value.",
};

/** Choose an operation and its target in one TypeSafe request. Only the selected operation's head is used. */
export async function choose(
  state: PageState,
  goal: string,
  history: readonly HistoryEntry[],
  opts: ModelOptions = {},
): Promise<Decision> {
  const { elements, targets, controls } = actionSpace(state.actions);
  const operations: Record<string, string> = {};
  for (const key of Object.keys(targets) as Operation[]) operations[key] = LABELS[key];
  for (const [key, value] of Object.entries(controls)) operations[key] = value.label;
  operations.DONE = "Every requirement is visibly satisfied.";
  operations.BLOCKED = "No supported operation can progress.";
  const questions: Record<string, ChoiceQuestionBody> = {
    operation: { type: "choice", criteria: operations, instructions: { goal, rules: NEXT_ACTION } },
  };
  for (const [operation, candidates] of Object.entries(targets)) {
    const criteria: Record<string, unknown> = {};
    for (const [index, a] of Object.entries(candidates)) {
      const entry: Record<string, unknown> = {
        element: `[${index}] ${a.label}`,
        current_value: getOr(a, "current_value", getOr(a, "value", "")),
      };
      for (const k of ["role", "checked", "selected", "expanded"]) {
        if (k in a) entry[k] = (a as unknown as Record<string, unknown>)[k];
      }
      criteria[index] = entry;
    }
    questions[operation.toLowerCase() + "_target"] = {
      type: "choice",
      criteria,
      instructions: { goal, operation, rules: [NEXT_ACTION, TARGET] },
    };
  }
  const body: SystemOneBody = {
    model: readEnv(opts, "TYPESAFE_MODEL") ?? "jev-latest",
    state: {
      page: { url: state.url, title: state.title, text: state.text },
      elements,
      recent_actions: history.slice(-10).map((h) => ({
        action: h.action ?? null,
        kind: h.kind ?? null,
        text: h.text ?? null,
        page_changed: h.page_changed ?? null,
      })),
    },
    questions,
  };
  const apiKey = readEnv(opts, "TYPESAFE_API_KEY");
  if (!apiKey) throw new JevError("Choosing needs TYPESAFE_API_KEY; no action executed.");
  const started = performance.now();
  let result: unknown;
  try {
    // Every option is explicit, so the SDK never reads process.env.
    const client = new TypeSafeClient({
      apiKey,
      baseURL: "https://api.typesafe.ai",
      defaultModel: body.model,
      logLevel: "off",
      timeout: 25_000,
      fetch: opts.fetch,
      retry: {
        maxRetries: 2,
        backoffInitialMs: 500,
        backoffMaxMs: 5000,
        backoffJitter: 0,
        httpStatuses: new Set([429, 503, 529]),
        respectRetryAfter: false,
        apiConnectionError: false,
        apiTimeoutError: false,
      },
    });
    result = await client.systemOne(body as unknown as SystemOneRequest);
  } catch (error) {
    if (error instanceof APIError) {
      throw new ModelError(`Model provider returned HTTP ${error.status}; no action executed.`);
    }
    if (error instanceof APIConnectionError) throw new ModelError("Model connection failed; no action executed.");
    if (error instanceof TypeSafeError) throw new JevError(error.message);
    throw error;
  }
  if (!isObject(result) || !isObject(result.answers)) throw new JevError(INVALID);
  const answers = result.answers;
  const operationAnswer = validateChoice(getOr(answers, "operation", {}), Object.keys(operations));
  const operation = operationAnswer.choice;
  let target: string | null = null;
  let targetAnswer: ChoiceAnswer | null = null;
  const probabilities: Record<string, number> = {};
  let choice: string;
  const heads = targets as Record<string, Record<string, ElementAction>>;
  if (Object.hasOwn(heads, operation)) {
    // Unused target heads cannot cause an action. Validate the head selected by the operation.
    const candidates = heads[operation];
    targetAnswer = validateChoice(getOr(answers, operation.toLowerCase() + "_target", {}), Object.keys(candidates));
    target = targetAnswer.choice;
    choice = candidates[target].id;
    for (const [index, a] of Object.entries(candidates)) probabilities[a.id] = targetAnswer.probabilities[index];
  } else {
    choice = Object.hasOwn(controls, operation) ? controls[operation].id : operation;
    probabilities[choice] = operationAnswer.probabilities[operation];
  }
  return {
    choice,
    operation,
    target,
    confidence: operationAnswer.confidence,
    probabilities,
    operation_probabilities: operationAnswer.probabilities,
    target_probabilities: targetAnswer ? targetAnswer.probabilities : {},
    target_confidence: targetAnswer ? targetAnswer.confidence : null,
    raw_answers: answers,
    model: result.model as string,
    usage: getOr(result, "usage", {}) as Record<string, unknown>,
    latency_ms: Math.round(performance.now() - started),
    request: body,
  };
}

/** The complete text-helper input for one field. */
export function fieldContext(
  goal: string,
  action: Action,
  page: PageState,
  history: readonly HistoryEntry[],
): FieldContext {
  const a = action as Partial<ElementAction>;
  return {
    goal,
    field: { label: a.label ?? null, role: a.role ?? null, value: a.value ?? null },
    page: { title: page.title, text: page.text.slice(0, 6000) },
    recent_actions: history.slice(-6).map((h) => ({ action: h.action ?? null, text: h.text ?? null })),
  };
}

/** Ask the text LLM for one field value. Returns `[text, helper]`. Nothing is hardcoded or guessed. */
export async function fieldText(
  context: FieldContext | Record<string, unknown>,
  opts: ModelOptions = {},
): Promise<[text: string, helper: TextHelperInfo]> {
  const key = readEnv(opts, "TEXT_MODEL_API_KEY");
  if (!key) {
    throw new JevError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.");
  }
  const base = (readEnv(opts, "TEXT_MODEL_BASE_URL") ?? "https://api.deepseek.com/v1").replace(/\/+$/, "");
  const model = readEnv(opts, "TEXT_MODEL") ?? "deepseek-chat";
  let reasoning: Record<string, unknown> = base.includes("api.deepseek.com/")
    ? { thinking: { type: "disabled" } }
    : { reasoning: { effort: "low" } };
  if (readEnv(opts, "TEXT_MODEL_REASONING") === "none") reasoning = { reasoning: { enabled: false } };
  const started = performance.now();
  const result = await postJson(
    base + "/chat/completions",
    key,
    {
      model,
      max_tokens: 1024,
      response_format: { type: "json_object" },
      ...reasoning,
      messages: [
        { role: "system", content: TEXT_VALUE },
        { role: "user", content: pythonJsonDumps(context) },
      ],
    },
    opts.fetch,
  );
  let value: unknown;
  try {
    // deno-lint-ignore no-explicit-any
    const output = JSON.parse((result as any).choices[0].message.content);
    if (!isObject(output)) throw new Error();
    value = output.text;
    const keys = Object.keys(output);
    if (keys.length !== 1 || keys[0] !== "text" || typeof value !== "string" || !value.trim()) throw new Error();
    if ([...value].length > 2000) throw new Error();
  } catch {
    throw new JevError("Text helper returned no valid field value; nothing typed.");
  }
  return [value as string, {
    model,
    latency_ms: Math.round(performance.now() - started),
    usage: (isObject(result) ? getOr(result, "usage", {}) : {}) as Record<string, unknown>,
  }];
}
