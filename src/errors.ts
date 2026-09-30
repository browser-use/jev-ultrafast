/** Errors raised by the agent. Every expected failure is a {@link JevError}. */

/** An expected, user-facing failure. The demo reports these as HTTP 400. */
export class JevError extends Error {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = new.target.name;
  }
}

/** A decision no longer refers to the observed page. */
export class StalePage extends JevError {}

/** Chrome DevTools returned an error, or the connection failed. */
export class CdpError extends JevError {
  /** The CDP error code, when Chrome reported one. */
  readonly code?: number;
  /** The CDP method that failed, when known. */
  readonly method?: string;

  constructor(message: string, code?: number, method?: string) {
    super(message);
    this.code = code;
    this.method = method;
  }
}

/** A CDP command did not answer in time. It is never resent. */
export class CdpTimeout extends JevError {}

/** A model provider failed; no action was executed. */
export class ModelError extends JevError {}
