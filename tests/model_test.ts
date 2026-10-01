// Offline contracts for TypeSafe choices and the text helper. Every request goes to an injected fake fetch.
import { assert, assertEquals, assertInstanceOf, assertRejects, assertStringIncludes, assertThrows } from "@std/assert";
import { FakeTime } from "@std/testing/time";
import { JevError, ModelError } from "../src/mod.ts";
import { actionSpace, choose, fieldContext, fieldText, postJson, validateChoice } from "../src/model.ts";
import { canonicalJson, pythonJsonDumps } from "../src/json.ts";
import { choice, fakeFetch, page } from "./_helpers.ts";

// deno-lint-ignore no-explicit-any
type Json = Record<string, any>;

const TYPESAFE = { TYPESAFE_API_KEY: "test" };
const TEXT = { TEXT_MODEL_API_KEY: "test" };

/** Drive fake timers until `promise` settles so retry backoff costs no real time. */
async function settleWithFakeTime<T>(time: FakeTime, promise: Promise<T>): Promise<T> {
  let settled = false;
  promise.then(() => settled = true, () => settled = true);
  for (let i = 0; i < 1000 && !settled; i++) {
    if (!(await time.nextAsync())) await time.runMicrotasks();
  }
  return await promise;
}

function textReply(content: string): Response {
  return Response.json({ choices: [{ message: { content } }] });
}

// --- validateChoice ---------------------------------------------------------------------------------------------

const MUTATIONS: Record<string, (a: Json) => void> = {
  unknown: (a) => a.choice = "invented",
  nan: (a) => a.probabilities.a = NaN,
  missing: (a) => delete a.probabilities.b,
  negative: (a) => a.probabilities.b = -1,
  non_max: (a) => a.choice = "b",
  confidence: (a) => a.confidence = 5,
};

for (const [mutation, mutate] of Object.entries(MUTATIONS)) {
  Deno.test(`invalid choice is rejected: ${mutation}`, () => {
    const a: Json = choice(["a", "b"], "a");
    mutate(a);
    assertThrows(() => validateChoice(a, ["a", "b"]), JevError, "Invalid TypeSafe");
  });
}

Deno.test("valid choice is accepted", () => {
  const a = choice(["a", "b"], "a");
  assertEquals(validateChoice(a, ["a", "b"]), a);
});

Deno.test("boolean probabilities and confidence are rejected", () => {
  assertThrows(
    () => validateChoice({ choice: "a", confidence: 1, probabilities: { a: true, b: false } }, ["a", "b"]),
    JevError,
    "Invalid TypeSafe",
  );
  assertThrows(
    () => validateChoice({ choice: "a", confidence: true, probabilities: { a: 1, b: 0 } }, ["a", "b"]),
    JevError,
    "Invalid TypeSafe",
  );
});

// --- actionSpace / choose ---------------------------------------------------------------------------------------

Deno.test("one index per node with operation-specific targets", async () => {
  const { elements, targets, controls } = actionSpace((await page()).actions);
  assertEquals(elements.length, 2);
  assertEquals(elements[0].operations, ["TYPE_TEXT", "CLICK"]);
  assertEquals(targets.TYPE_TEXT!["1"].id, "e1");
  assertEquals(targets.CLICK!["1"].id, "e2");
  assertEquals(targets.CLICK!["2"].id, "e3");
  assert("WAIT" in controls);
});

Deno.test("all heads are one request and only the matching head executes", async () => {
  const fetch = fakeFetch(({ body }) => {
    const b = body as Json;
    return Response.json({
      model: "test",
      answers: {
        operation: choice(b.questions.operation.criteria, "TYPE_TEXT"),
        type_text_target: choice(["1"], "1"),
        click_target: { choice: "invented" },
      },
    });
  });
  const d = await choose(await page(), "Find a book", [], { fetch, env: TYPESAFE });
  assertEquals(fetch.calls.length, 1);
  assertEquals(fetch.calls[0].url, "https://api.typesafe.ai/v1/systemone");
  assertEquals(fetch.calls[0].headers.get("authorization"), "Bearer test");
  assert(d.operation === "TYPE_TEXT" && d.target === "1" && d.choice === "e1");
  const questions = (fetch.calls[0].body as Json).questions;
  assertEquals(new Set(Object.keys(questions)), new Set(["operation", "click_target", "type_text_target"]));
  assertEquals((fetch.calls[0].body as Json).model, "jev-latest");
});

Deno.test("click cannot consume a text target", async () => {
  const fetch = fakeFetch(({ body }) =>
    Response.json({
      model: "test",
      answers: {
        operation: choice((body as Json).questions.operation.criteria, "CLICK"),
        type_text_target: choice(["1"], "1"),
        click_target: choice(["1", "2", "999"], "999"),
      },
    })
  );
  const p = await page();
  await assertRejects(() => choose(p, "Find a book", [], { fetch, env: TYPESAFE }), JevError, "Invalid TypeSafe");
});

Deno.test("target head receives control state and full next-step rules", async () => {
  const p = await page();
  p.actions.unshift(
    {
      id: "toggle",
      kind: "click",
      label: "Free cancellation",
      node: 30,
      role: "checkbox",
      checked: "true",
      selected: false,
    } as unknown as (typeof p.actions)[number],
  );
  const fetch = fakeFetch(({ body }) => {
    const questions = (body as Json).questions;
    return Response.json({
      model: "test",
      answers: {
        operation: choice(questions.operation.criteria, "CLICK"),
        click_target: choice(questions.click_target.criteria, "3"),
      },
    });
  });
  const d = await choose(p, "Search with free cancellation", [], { fetch, env: TYPESAFE });
  const questions = (fetch.calls[0].body as Json).questions;
  const target = questions.click_target;
  assertEquals(target.criteria["1"].checked, "true");
  assertEquals(target.criteria["1"].selected, false);
  assert(target.instructions.rules.includes(questions.operation.instructions.rules));
  assertEquals(d.choice, "e3");
});

Deno.test("choose without TYPESAFE_API_KEY stops before any request", async () => {
  const fetch = fakeFetch(() => Response.json({}));
  await assertRejects(
    async () => await choose(await page(), "Find a book", [], { fetch, env: { TYPESAFE_API_KEY: "" } }),
    JevError,
    "TYPESAFE_API_KEY",
  );
  assertEquals(fetch.calls.length, 0);
});

Deno.test("choose retries 503 with backoff, then succeeds", async () => {
  using time = new FakeTime();
  const fetch = fakeFetch(({ body }, i) =>
    i < 2 ? Response.json({ error: "busy" }, { status: 503 }) : Response.json({
      model: "test",
      answers: {
        operation: choice((body as Json).questions.operation.criteria, "CLICK"),
        click_target: choice(["1", "2"], "2"),
      },
    })
  );
  const d = await settleWithFakeTime(time, choose(await page(), "Go", [], { fetch, env: TYPESAFE }));
  assertEquals(fetch.calls.length, 3);
  assertEquals(d.choice, "e3");
});

Deno.test("choose gives up after three rate-limited attempts", async () => {
  using time = new FakeTime();
  const fetch = fakeFetch(() => Response.json({ error: "slow down" }, { status: 429 }));
  const p = await page();
  const error = await settleWithFakeTime(time, choose(p, "Go", [], { fetch, env: TYPESAFE })).catch((e) => e);
  assertInstanceOf(error, ModelError);
  assertStringIncludes(error.message, "HTTP 429");
  assertEquals(fetch.calls.length, 3);
});

Deno.test("choose does not retry other HTTP errors or connection failures", async () => {
  const p = await page();
  const server = fakeFetch(() => Response.json({ error: "bad" }, { status: 500 }));
  await assertRejects(() => choose(p, "Go", [], { fetch: server, env: TYPESAFE }), ModelError, "HTTP 500");
  assertEquals(server.calls.length, 1);
  const offline = fakeFetch(() => {
    throw new TypeError("network down");
  });
  await assertRejects(() => choose(p, "Go", [], { fetch: offline, env: TYPESAFE }), ModelError, "connection failed");
  assertEquals(offline.calls.length, 1);
});

// --- field text -------------------------------------------------------------------------------------------------

Deno.test("quoted task text still uses the LLM", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  const p = await page();
  const context = fieldContext('Fly from "Zurich" to London', p.actions[0], p, []);
  const [text] = await fieldText(context, { fetch, env: TEXT });
  assertEquals(text, "Zurich");
  assertEquals(fetch.calls.length, 1);
  assertEquals(fetch.calls[0].url, "https://api.deepseek.com/v1/chat/completions");
  const sent = JSON.parse((fetch.calls[0].body as Json).messages[1].content);
  assertEquals(sent.goal, 'Fly from "Zurich" to London');
});

Deno.test("missing text credential stops before guessing", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  await assertRejects(
    () => fieldText({ goal: 'Enter "Zurich"' } as never, { fetch, env: {} }),
    JevError,
    "TEXT_MODEL_API_KEY",
  );
  assertEquals(fetch.calls.length, 0);
});

for (const content of ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']) {
  Deno.test(`text helper rejects invalid values: ${content}`, async () => {
    const fetch = fakeFetch(() => textReply(content));
    await assertRejects(
      () => fieldText({ goal: "Find a flight" } as never, { fetch, env: TEXT }),
      JevError,
      "nothing typed",
    );
  });
}

Deno.test("OpenAI mode sends Chat Completions fields with the OpenAI key", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  const env = { USE_OPENAI: "true", OPENAI_API_KEY: "sk-test", TEXT_MODEL_API_KEY: "other", TEXT_MODEL: "ignored" };
  const [text, helper] = await fieldText({ goal: "Fly from Zurich" }, { fetch, env });
  assertEquals(text, "Zurich");
  assertEquals(helper.model, "gpt-6-luna");
  assertEquals(fetch.calls[0].url, "https://api.openai.com/v1/chat/completions");
  assertEquals(fetch.calls[0].headers.get("authorization"), "Bearer sk-test");
  const body = fetch.calls[0].body as Json;
  assertEquals(body.max_completion_tokens, 1024);
  assertEquals(body.reasoning_effort, "none");
  assertEquals(body.response_format, { type: "json_object" });
  assert(!("max_tokens" in body) && !("reasoning" in body) && !("thinking" in body));
});

Deno.test("OpenAI mode honours model and effort overrides", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  const env = { USE_OPENAI: "1", OPENAI_API_KEY: "k", OPENAI_MODEL: "gpt-5.5", OPENAI_REASONING_EFFORT: "low" };
  await fieldText({ goal: "Fly" }, { fetch, env });
  const body = fetch.calls[0].body as Json;
  assertEquals([body.model, body.reasoning_effort], ["gpt-5.5", "low"]);
});

Deno.test("OpenAI mode without OPENAI_API_KEY stops before any request", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  await assertRejects(
    () => fieldText({ goal: "Fly" }, { fetch, env: { USE_OPENAI: "true", TEXT_MODEL_API_KEY: "other" } }),
    JevError,
    "OPENAI_API_KEY",
  );
  assertEquals(fetch.calls.length, 0);
});

Deno.test("USE_OPENAI=false keeps the OpenAI-compatible helper", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zurich"}'));
  await fieldText({ goal: "Fly" }, { fetch, env: { ...TEXT, USE_OPENAI: "false", OPENAI_API_KEY: "k" } });
  assertEquals(fetch.calls[0].url, "https://api.deepseek.com/v1/chat/completions");
  assertEquals((fetch.calls[0].body as Json).max_tokens, 1024);
});

// --- postJson ---------------------------------------------------------------------------------------------------

Deno.test("postJson retries 429/503/529 with backoff, then returns JSON", async () => {
  using time = new FakeTime();
  const statuses = [429, 529];
  const fetch = fakeFetch((_, i) => i < 2 ? new Response("busy", { status: statuses[i] }) : Response.json({ ok: 1 }));
  const result = await settleWithFakeTime(time, postJson("https://model.test/x", "key", { a: 1 }, fetch));
  assertEquals(result, { ok: 1 });
  assertEquals(fetch.calls.length, 3);
  assertEquals(fetch.calls[0].headers.get("authorization"), "Bearer key");
  assertEquals(fetch.calls[0].body, { a: 1 });
});

Deno.test("postJson gives up after three attempts", async () => {
  using time = new FakeTime();
  const fetch = fakeFetch(() => new Response("busy", { status: 503 }));
  const error = await settleWithFakeTime(time, postJson("https://model.test/x", "key", {}, fetch)).catch((e) => e);
  assertInstanceOf(error, ModelError);
  assertStringIncludes(error.message, "HTTP 503");
  assertEquals(fetch.calls.length, 3);
});

Deno.test("postJson does not retry other errors", async () => {
  const server = fakeFetch(() => new Response("no", { status: 401 }));
  await assertRejects(() => postJson("https://model.test/x", "key", {}, server), ModelError, "HTTP 401");
  assertEquals(server.calls.length, 1);
  const offline = fakeFetch(() => {
    throw new TypeError("network down");
  });
  await assertRejects(
    () => postJson("https://model.test/x", "key", {}, offline),
    ModelError,
    "Model connection failed; no action executed.",
  );
  assertEquals(offline.calls.length, 1);
  const garbage = fakeFetch(() => new Response("not json", { status: 200 }));
  await assertRejects(() => postJson("https://model.test/x", "key", {}, garbage), JevError, "invalid JSON");
});

// --- canonicalJson ----------------------------------------------------------------------------------------------

Deno.test("canonicalJson sorts keys, omits undefined keys and nulls undefined array items", () => {
  assertEquals(
    canonicalJson({ b: 1, a: { d: [1, undefined], c: "x" }, e: undefined }),
    '{"a":{"c":"x","d":[1,null]},"b":1}',
  );
  assertEquals(canonicalJson({ x: 1, y: [2, { q: 1, p: 2 }] }), canonicalJson({ y: [2, { p: 2, q: 1 }], x: 1 }));
});

Deno.test("pythonJsonDumps matches Python json.dumps bytes", () => {
  assertEquals(pythonJsonDumps({ a: "Zürich", b: [1, null] }), '{"a": "Z\\u00fcrich", "b": [1, null]}');
  assertEquals(pythonJsonDumps("✈😀"), '"\\u2708\\ud83d\\ude00"');
  assertEquals(
    pythonJsonDumps({ q: 'say "hi"\\\n\t\x7f', t: true, f: false, e: {}, l: [] }),
    '{"q": "say \\"hi\\"\\\\\\n\\t\\u007f", "t": true, "f": false, "e": {}, "l": []}',
  );
});

Deno.test("text helper sends the context as Python json.dumps text", async () => {
  const fetch = fakeFetch(() => textReply('{"text":"Zürich"}'));
  await fieldText({ goal: "Fly from Zürich 😀", field: { label: null } }, { fetch, env: TEXT });
  assertEquals(
    (fetch.calls[0].body as Json).messages[1].content,
    '{"goal": "Fly from Z\\u00fcrich \\ud83d\\ude00", "field": {"label": null}}',
  );
});
