// Offline contracts for browser execution over a fake CDP transport. No Chrome is started.
import {
  assert,
  assertEquals,
  assertInstanceOf,
  assertNotEquals,
  assertRejects,
  assertStringIncludes,
} from "@std/assert";
import { assertSpyCalls, spy } from "@std/testing/mock";
import { Browser, browserOperation, CdpError, CdpTimeout, fingerprint, JevError, StalePage } from "../src/mod.ts";
import type { Action, PageState } from "../src/mod.ts";
import { FakeCdp, page } from "./_helpers.ts";

type Operation = typeof browserOperation;

Deno.test("observation is one atomic browser read", async () => {
  const p = await page();
  const cdp = new FakeCdp(() => ({ result: { value: p } }));
  const actual = await browserOperation({ operation: "observe", session: "test", screenshot: false }, cdp) as PageState;
  assertEquals(actual.actions, p.actions);
  assertEquals(cdp.calls.length, 1);
  assertEquals(cdp.calls[0].method, "Runtime.evaluate");
  assertEquals(cdp.calls[0].opts?.sessionId, "test");
});

Deno.test("executor rejects a stale page before browser input", async () => {
  const p = await page();
  const b = new Browser(new FakeCdp(() => ({})), "target", "session");
  b.fresh = () => Promise.resolve(false);
  const operation = spy((() => Promise.resolve(null)) as unknown as Operation);
  b.operation = operation;
  await assertRejects(() => b.act(p.actions[0], p, "book"), StalePage);
  assertSpyCalls(operation, 0);
});

const INTERRUPTED: Record<string, unknown> = {
  exceptionDetails: { exceptionDetails: { text: "Execution context destroyed" } },
  result: { result: {} },
};

for (const [name, response] of Object.entries(INTERRUPTED)) {
  Deno.test(`interrupted dropdown mutation cannot be retried as stale: ${name}`, async () => {
    // A navigation can destroy the evaluation result after the change event already fired.
    const cdp = new FakeCdp(() => response);
    const action = { id: "e1", kind: "select", node: 1, value: "Design" } as unknown as Action;
    const error = await browserOperation({ operation: "act", session: "test", action }, cdp).catch((e) => e);
    assertInstanceOf(error, JevError);
    assert(!(error instanceof StalePage), "an interrupted dropdown must not look retryable");
    assertStringIncludes(error.message, "Dropdown execution");
    assertEquals(cdp.calls.length, 1);
  });
}

Deno.test("fingerprint tracks values and identity, not screenshots", async () => {
  const p = await page();
  const other = structuredClone(p);
  other.screenshot = "changed";
  assertEquals(await fingerprint(p), await fingerprint(other));
  (other.actions[0] as { node: number }).node = 99;
  assertNotEquals(await fingerprint(p), await fingerprint(other));
});

/** A browser that has just clicked "Go", so its next observe runs the read-only after-input wait. */
async function browserAfterClick(afterInput: () => unknown) {
  const p = await page();
  const cdp = new FakeCdp(({ method, params }) => {
    assertEquals(method, "Runtime.evaluate");
    if (params?.awaitPromise) return afterInput();
    return { result: { value: [p.page_key, p.guards["20"]] } }; // freshness guard for node 20
  });
  const b = new Browser(cdp, "target", "session");
  const operation = spy(
    ((req: { operation: string }) =>
      Promise.resolve(req.operation === "act" ? { executed: "e3" } : p)) as unknown as Operation,
  );
  b.operation = operation;
  await b.act(p.actions[2], p);
  assertSpyCalls(operation, 1);
  return { b, p, cdp, operation };
}

Deno.test("after-input wait swallows CdpError and still observes", async () => {
  const { b, p, cdp, operation } = await browserAfterClick(() => {
    throw new CdpError("Execution context was destroyed", -32000, "Runtime.evaluate");
  });
  assertEquals(await b.observe({ screenshot: false }), p);
  assertSpyCalls(operation, 2);
  assertEquals(cdp.calls.filter((c) => c.params?.awaitPromise).length, 1);
  // The wait runs once per executed action.
  await b.observe({ screenshot: false });
  assertEquals(cdp.calls.filter((c) => c.params?.awaitPromise).length, 1);
});

Deno.test("after-input wait does not swallow CdpTimeout", async () => {
  const { b, operation } = await browserAfterClick(() => {
    throw new CdpTimeout("Runtime.evaluate timed out after 5s");
  });
  await assertRejects(() => b.observe({ screenshot: false }), CdpTimeout);
  assertSpyCalls(operation, 1);
});

Deno.test("observe retries a settling page ten times, then surfaces StalePage", async () => {
  const b = new Browser(new FakeCdp(() => ({})), "target", "session");
  const operation = spy((() => Promise.reject(new StalePage("Document is navigating"))) as unknown as Operation);
  b.operation = operation;
  await assertRejects(() => b.observe({ screenshot: false }), StalePage);
  assertSpyCalls(operation, 10);
});

Deno.test("observe without a screenshot is exactly one CDP call", async () => {
  const p = await page();
  const cdp = new FakeCdp(() => ({ result: { value: p } }));
  const b = new Browser(cdp, "target", "session");
  const observed = await b.observe({ screenshot: false });
  assertEquals(observed.actions, p.actions);
  assertEquals(cdp.calls.map((c) => c.method), ["Runtime.evaluate"]);
});
