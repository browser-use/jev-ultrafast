/**
 * Jev Ultrafast: a browser agent with a dynamic, indexed action space.
 *
 * TypeSafe chooses an operation and an observed target in one request; code owns execution.
 *
 * @module
 */

export { Agent } from "./agent.ts";
export type { AgentInit, AgentOptions, ModelFns, PendingText } from "./agent.ts";
export { Browser, browserOperation, fingerprint } from "./browser.ts";
export { acquireCdp, CdpConnection, resolveWsUrl } from "./cdp.ts";
export type { CdpLease } from "./cdp.ts";
export { loadEnvironment } from "./env.ts";
export { CdpError, CdpTimeout, JevError, ModelError, StalePage } from "./errors.ts";
export type { ActionSpace, ModelOptions } from "./model.ts";
export { MAX_STEPS } from "./questions.ts";
export type * from "./types.ts";
