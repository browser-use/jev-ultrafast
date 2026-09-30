// Offline contracts for the agent loop: consumed decisions, text reuse, no-progress stops. No browser, no model.
import { assert, assertEquals, assertRejects, assertStrictEquals } from "@std/assert";
import { assertSpyCalls, spy } from "@std/testing/mock";
import { Agent, JevError, StalePage } from "../src/mod.ts";
import type { Action, BrowserLike, PageState, TextHelperInfo } from "../src/mod.ts";
import { decision, page } from "./_helpers.ts";

/** A BrowserLike whose behaviour each test reconfigures through the `*Impl` fields. */
class FakeBrowser implements BrowserLike {
  target: string | null = "target";
  session = "session";
  freshImpl: () => boolean = () => true;
  observeImpl: () => PageState;
  actResults: (() => unknown)[] = [];

  constructor(public page: PageState) {
    this.observeImpl = () => this.page;
  }

  fresh = spy((_page: PageState, _action?: Action): Promise<boolean> => Promise.resolve().then(() => this.freshImpl()));
  observe = spy((_opts?: { screenshot?: boolean }): Promise<PageState> =>
    Promise.resolve().then(() => this.observeImpl())
  );
  act = spy((action: Action, _page: PageState, _text?: string | null): Promise<{ executed: string }> =>
    Promise.resolve().then(() => {
      const next = this.actResults.shift();
      if (next) next();
      return { executed: action.id };
    })
  );
  close = spy((): Promise<void> => Promise.resolve());
}

type Helper = (context: unknown) => Promise<[string, TextHelperInfo]>;

async function runner(fieldText?: Helper) {
  const p = await page();
  const browser = new FakeBrowser(p);
  const helper = spy(
    fieldText ?? ((_context: unknown) => Promise.resolve(["book", { model: "test", latency_ms: 10 }])),
  );
  const agent = new Agent(browser, "Find a book", p, {
    screenshots: false,
    env: {},
    // deno-lint-ignore no-explicit-any
    model: { fieldText: helper as any, choose: () => Promise.reject(new Error("choose must not be called")) },
  });
  agent.state.decision = decision();
  agent.state.status = "predicted";
  agent.state.started_at = performance.now();
  agent.state.record = false;
  agent.state.text_calls = [];
  agent.pendingText = null;
  return { agent, browser, helper };
}

function act(agent: Agent) {
  return agent.command("act", { fingerprint: agent.state.page.fingerprint });
}

Deno.test("stale decision is consumed before any mutation", async () => {
  const { agent, browser } = await runner();
  browser.freshImpl = () => false;
  await assertRejects(() => act(agent), StalePage);
  assertSpyCalls(browser.act, 0);
  assertStrictEquals(agent.state.decision, null);
});

Deno.test("generated text is reused only for an identical retry context", async () => {
  const { agent, browser, helper } = await runner();
  browser.actResults = [() => {
    throw new StalePage("Changed before input");
  }];
  await assertRejects(() => act(agent), StalePage);
  agent.state.decision = decision();
  await act(agent);
  assertSpyCalls(helper, 1);
  assertSpyCalls(browser.act, 2); // The first call rejects before any browser input.
  assertStrictEquals(agent.pendingText, null);
  assertEquals(agent.state.text_calls.length, 1);
  assertEquals(agent.state.history.at(-1)!.text, "book");
});

Deno.test("changed field context does not reuse generated text", async () => {
  const { agent, browser, helper } = await runner();
  browser.actResults = [() => {
    throw new StalePage("Changed before input");
  }];
  await assertRejects(() => act(agent), StalePage);
  agent.state.page.text = "Different page context";
  agent.state.decision = decision();
  await act(agent);
  assertSpyCalls(helper, 2);
});

Deno.test("loading waits do not trigger the no-progress stop", async () => {
  const { agent } = await runner();
  for (let i = 0; i < 5; i++) {
    agent.state.decision = decision("wait");
    await act(agent);
  }
  assertEquals(agent.state.history.length, 5);
  assertEquals(agent.state.status, "ready");
});

Deno.test("three unchanged non-wait actions block the run", async () => {
  const { agent } = await runner();
  for (let i = 0; i < 3; i++) {
    agent.state.decision = decision("e3");
    await act(agent);
  }
  assertEquals(agent.state.history.map((h) => h.page_changed), [false, false, false]);
  assertEquals(agent.state.status, "blocked");
});

Deno.test("stale observation preserves the executed action", async () => {
  const { agent, browser } = await runner();
  agent.state.decision = decision("e3");
  browser.observeImpl = () => {
    throw new StalePage("changed");
  };
  await assertRejects(() => act(agent), StalePage);
  assertEquals(agent.state.history.at(-1)!.action, "Go");
  assertSpyCalls(browser.act, 1);
});

Deno.test("navigation during prediction re-observes without action", async () => {
  const { agent, browser } = await runner();
  browser.freshImpl = () => {
    throw new StalePage("Document navigating");
  };
  await agent.command("tick");
  assertEquals(agent.state.status, "ready");
  assertStrictEquals(agent.state.decision, null);
  assertSpyCalls(browser.act, 0);
  assertSpyCalls(browser.observe, 1);
});

Deno.test("snapshot adds elements and excludes the browser", async () => {
  const { agent } = await runner();
  const snapshot = agent.snapshot();
  assertEquals(snapshot.elements.length, 2);
  assert(!("browser" in snapshot));
  assertEquals(JSON.parse(JSON.stringify(snapshot)).goal, "Find a book");
});

Deno.test("an empty task is refused before a browser opens", async () => {
  const openBrowser = spy((_url: string): Promise<BrowserLike> => Promise.reject(new Error("must not open")));
  await assertRejects(
    () => Agent.create("https://example.test/", ["  ", ""], { openBrowser }),
    JevError,
    "Supply a task",
  );
  assertSpyCalls(openBrowser, 0);
});

Deno.test("Agent.create closes the browser when the first observation fails", async () => {
  const p = await page();
  const browser = new FakeBrowser(p);
  browser.observeImpl = () => {
    throw new StalePage("Document is navigating");
  };
  await assertRejects(
    () => Agent.create("https://example.test/", "Find a book", { openBrowser: () => Promise.resolve(browser) }),
    StalePage,
  );
  assertSpyCalls(browser.close, 1);
});
