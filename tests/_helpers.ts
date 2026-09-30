// Shared offline fixtures. Nothing here touches the network, the environment or Chrome.
import { fingerprint } from "../src/mod.ts";
import type { CdpParams, CdpResult, CdpTransport, Decision, PageState } from "../src/mod.ts";

export async function page(): Promise<PageState> {
  const state = {
    url: "https://example.test/",
    title: "Search",
    w: 1120,
    h: 780,
    text: "Search",
    scroll: { y: 0, height: 780 },
    actions: [
      { id: "e1", kind: "fill", label: "Search", role: "textbox", value: "", node: 10 },
      { id: "e2", kind: "click", label: "Open Search", role: "textbox", value: "", node: 10 },
      { id: "e3", kind: "click", label: "Go", role: "button", value: "", node: 20 },
      { id: "wait", kind: "wait", label: "Wait" },
    ],
    marker: ["marker"],
    page_key: ["page-key"],
    guards: { "10": "guard-10", "20": "guard-20" },
    omitted_actions: 0,
    fingerprint: "",
  } as unknown as PageState;
  state.fingerprint = await fingerprint(state);
  return state;
}

export type ChoiceAnswer = { choice: string; confidence: number; probabilities: Record<string, unknown> };

export function choice(ids: Iterable<string> | Record<string, unknown>, selected: string): ChoiceAnswer {
  const keys = Symbol.iterator in Object(ids) ? [...(ids as Iterable<string>)] : Object.keys(ids);
  return {
    choice: selected,
    confidence: 1.0,
    probabilities: Object.fromEntries(keys.map((i) => [i, i === selected ? 1.0 : 0.0])),
  };
}

export function decision(action = "e1"): Decision {
  return {
    choice: action,
    operation: "TYPE_TEXT",
    target: "1",
    confidence: 1.0,
    probabilities: { [action]: 1.0 },
    latency_ms: 10,
    usage: {},
  } as unknown as Decision;
}

export interface FetchCall {
  url: string;
  headers: Headers;
  body: unknown;
}

/** A fetch that records each request and answers from `handler`; a thrown handler error acts like a network error. */
export function fakeFetch(
  handler: (call: FetchCall, index: number) => Response | Promise<Response>,
): typeof fetch & { calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  const f = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const text = await request.text();
    const call = { url: request.url, headers: request.headers, body: text ? JSON.parse(text) : undefined };
    calls.push(call);
    return await handler(call, calls.length - 1);
  };
  return Object.assign(f, { calls });
}

export interface CdpCall {
  method: string;
  params?: CdpParams;
  opts?: { sessionId?: string; timeoutMs?: number };
}

/** A CdpTransport whose replies come from `handler`. */
export class FakeCdp implements CdpTransport {
  calls: CdpCall[] = [];
  constructor(public handler: (call: CdpCall, index: number) => unknown) {}

  send<T = CdpResult>(
    method: string,
    params?: CdpParams,
    opts?: { sessionId?: string; timeoutMs?: number },
  ): Promise<T> {
    const call = { method, params, opts };
    this.calls.push(call);
    const index = this.calls.length - 1;
    return Promise.resolve().then(() => this.handler(call, index) as T);
  }

  on(_method: string, _handler: (params: CdpResult, sessionId?: string) => void): () => void {
    return () => {};
  }

  close(): Promise<void> {
    return Promise.resolve();
  }
}
