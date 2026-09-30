/**
 * Explicit live smoke: local fixtures + paid model APIs. Not run by `deno task test`.
 *
 * Needs `deno task demo` serving the fixture on 127.0.0.1:8766.
 */

import { parseArgs } from "@std/cli/parse-args";
import { assert, assertEquals, assertStringIncludes } from "@std/assert";
import { join } from "@std/path";
import { Agent, type AgentSnapshot, type Browser, loadEnvironment } from "../src/mod.ts";

const GOALS = "Use the destination search and filters to find Design stays in Lisbon with Free cancellation, " +
  "then open Casa Flora.";

function utcStamp(now: Date): string {
  // Python's %Y%m%dT%H%M%S%fZ; JavaScript clocks stop at milliseconds, so microseconds end in 000.
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}T` +
    `${p(now.getUTCHours())}${p(now.getUTCMinutes())}${p(now.getUTCSeconds())}${p(now.getUTCMilliseconds(), 3)}000Z`;
}

async function main(): Promise<void> {
  const args = parseArgs(Deno.args, {
    string: ["max-actions", "goal"],
    default: { "max-actions": "15", goal: GOALS },
  });
  const maxActions = Number(args["max-actions"]);
  if (!Number.isInteger(maxActions)) {
    console.error(`smoke.ts: error: argument --max-actions: invalid int value: '${args["max-actions"]}'`);
    Deno.exit(2);
  }
  loadEnvironment();
  const output = join("artifacts/dynamic/fixture", utcStamp(new Date()));
  await Deno.mkdir(output, { recursive: true });
  console.log(`Trace: ${output}`);
  await using agent = await Agent.create("http://127.0.0.1:8766/fixture.html?scenario=travel", args.goal);
  let state: AgentSnapshot & { verification_text?: unknown };
  try {
    for await (const step of agent.run()) {
      const history = step.history;
      console.log(step.elapsed_ms, "ms", history.length, "actions", history.length ? history.at(-1)!.action : "");
      await Deno.writeTextFile(join(output, "state.json"), JSON.stringify(step, null, 2));
      if (history.length >= maxActions) throw new Error(`Diagnostic stopped at ${maxActions} actions`);
    }
  } finally {
    state = agent.snapshot();
    try {
      state.verification_text = await (agent.browser as Browser).evaluate("document.body.innerText");
    } finally {
      await Deno.writeTextFile(join(output, "state.json"), JSON.stringify(state, null, 2));
    }
  }
  assertEquals(state.status, "done");
  assert(state.page.url.endsWith("#casa-flora"));
  assertStringIncludes(
    String(state.verification_text),
    "Your filters: Design · Free cancellation enabled · Destination Lisbon",
  );
  const result = {
    ms: state.elapsed_ms,
    verified: true,
    decisions: state.decisions.length,
    actions: state.history.length,
  };
  console.log(JSON.stringify(result, null, 2));
  await Deno.writeTextFile(join(output, "summary.json"), JSON.stringify(result, null, 2));
}

if (import.meta.main) await main();
