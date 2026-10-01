/**
 * One live measured flight search; freeze source externally to compare revisions.
 *
 * deno task measure --output DIR [--source CHECKOUT]
 */

import { parseArgs } from "@std/cli/parse-args";
import { encodeHex } from "@std/encoding/hex";
import { dirname, extname, join, resolve, toFileUrl } from "@std/path";
import type { AgentSnapshot, Browser, CdpConnection } from "../src/mod.ts";
import { GOALS, URL, type Verification, verify } from "../examples/flights.ts";

type Library = typeof import("../src/mod.ts");

async function sha256(data: Uint8Array<ArrayBuffer> | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  return encodeHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function sourceHashes(dir: string): Promise<Record<string, string>> {
  const names: string[] = [];
  for await (const entry of Deno.readDir(dir)) {
    if (entry.isFile && [".ts", ".js"].includes(extname(entry.name))) names.push(entry.name);
  }
  const hashes: Record<string, string> = {};
  for (const name of names.sort()) hashes[name] = await sha256(await Deno.readFile(join(dir, name)));
  return hashes;
}

const args = parseArgs(Deno.args, { string: ["source", "output"], default: { source: "." } });
if (!args.output) {
  console.error("usage: measure_flights.ts [--source SOURCE] --output OUTPUT");
  console.error("measure_flights.ts: error: the following arguments are required: --output");
  Deno.exit(2);
}
const source = resolve(args.source);
const lib: Library = await import(toFileUrl(join(source, "src", "mod.ts")).href);

const folder = args.output;
await Deno.mkdir(dirname(resolve(folder)), { recursive: true });
await Deno.mkdir(folder); // Refuses to overwrite an existing measurement.
const source_hashes = await sourceHashes(join(source, "src"));
const calls = new Map<string, number[]>();

const agent = await lib.Agent.create(URL, GOALS);
const browser = agent.browser as Browser;
(browser.cdp as CdpConnection).onCall = (method, ms) => {
  const times = calls.get(method) ?? [];
  times.push(Math.round(ms * 1000) / 1000);
  calls.set(method, times);
};
// Setup is excluded in both arms, as in the original demo.
calls.clear();
let error: string | null = null;
let state: AgentSnapshot & Record<string, unknown>;
try {
  for await (const step of agent.run()) {
    const last = step.history.at(-1);
    console.log(step.elapsed_ms, step.status, last?.action ?? "");
  }
} catch (exc) {
  error = exc instanceof Error ? `${exc.name}: ${exc.message}` : String(exc);
} finally {
  state = agent.snapshot() as AgentSnapshot & Record<string, unknown>;
  const measured_calls = Object.fromEntries(
    [...calls].map((
      [method, times],
    ) => [method, { count: times.length, ms: Math.round(times.reduce((a, b) => a + b, 0) * 1000) / 1000 }]),
  );
  // Both arms use a NEW final observation for the independent result check, outside timing.
  const final = await browser.observe({ screenshot: true });
  state.verification = verify(final);
  state.error = error;
  state.cdp = measured_calls;
  state.source_hashes = source_hashes;
  // Serialised like Python's json.dumps for these ASCII strings, so the hash matches the Python arm.
  state.task_hash = await sha256(`[${JSON.stringify(URL)}, ${JSON.stringify(GOALS)}]`);
  state.configuration = Object.fromEntries(
    [
      "TYPESAFE_MODEL",
      "TEXT_MODEL",
      "TEXT_MODEL_BASE_URL",
      "TEXT_MODEL_REASONING",
      "USE_OPENAI",
      "OPENAI_MODEL",
      "OPENAI_REASONING_EFFORT",
    ].map((key) => [
      key,
      Deno.env.get(key) ?? null,
    ]),
  );
  state.browser_version = (await browser.call<{ product: string }>("Browser.getVersion")).product;
  state.final_page = final;
  await Deno.writeTextFile(join(folder, "state.json"), JSON.stringify(state, null, 2));
  await agent.close();
}
console.log("VERIFIED", (state.verification as Verification).passed, "ERROR", error);
