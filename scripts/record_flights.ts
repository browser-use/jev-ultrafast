/**
 * A measured live run with continuous CDP screencast; original timestamps retained.
 *
 * deno task record [FOLDER]
 */

import { delay } from "@std/async/delay";
import { decodeBase64 } from "@std/encoding/base64";
import { encodeHex } from "@std/encoding/hex";
import { dirname, extname, fromFileUrl, join, resolve } from "@std/path";
import { Agent, type AgentSnapshot, type Browser, type CdpResult } from "../src/mod.ts";
import { GOALS, URL, type Verification, verify } from "../examples/flights.ts";

async function sourceHashes(dir: string): Promise<Record<string, string>> {
  const names: string[] = [];
  for await (const entry of Deno.readDir(dir)) {
    if (entry.isFile && [".ts", ".js"].includes(extname(entry.name))) names.push(entry.name);
  }
  const hashes: Record<string, string> = {};
  for (const name of names.sort()) {
    hashes[name] = encodeHex(await crypto.subtle.digest("SHA-256", await Deno.readFile(join(dir, name))));
  }
  return hashes;
}

const folder = Deno.args[0] ?? "artifacts/flights/recorded";
await Deno.mkdir(dirname(resolve(folder)), { recursive: true });
await Deno.mkdir(folder); // Refuses to overwrite an existing recording.
const source_hashes = await sourceHashes(fromFileUrl(new globalThis.URL("../src/", import.meta.url)));
const agent = await Agent.create(URL, GOALS);
const browser = agent.browser as Browser;
await Deno.mkdir(join(folder, "frames"), { recursive: true });
await Deno.writeFile(
  join(folder, "frames", "000000.jpg"),
  decodeBase64((await browser.call<{ data: string }>("Page.captureScreenshot", { format: "jpeg", quality: 85 })).data),
);
const frames = join(folder, "screencast");
await Deno.mkdir(frames, { recursive: true });
let epoch = Date.now() / 1000;
const errors: string[] = [];
const pending: Promise<void>[] = [];
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

const unsubscribe = browser.cdp.on("Page.screencastFrame", (p: CdpResult, sessionId?: string) => {
  if (sessionId !== browser.session) return;
  const timestamp = Math.max(0, Math.round((p.metadata.timestamp - epoch) * 1000));
  pending.push(
    Deno.writeFile(join(frames, `${String(timestamp).padStart(6, "0")}.jpg`), decodeBase64(p.data)).catch((e) => {
      errors.push(message(e));
    }),
  );
  browser.call("Page.screencastFrameAck", { sessionId: p.sessionId }).catch((e) => errors.push(message(e)));
});

await browser.call("Page.startScreencast", {
  format: "jpeg",
  quality: 80,
  maxWidth: 1120,
  maxHeight: 780,
  everyNthFrame: 2,
});
// The first prediction starts the run timer; this anchors video timestamps to it.
epoch = Date.now() / 1000;
let state: AgentSnapshot & Record<string, unknown>;
try {
  for await (const step of agent.run()) {
    const action = step.history.length ? step.history.at(-1)!.action : "";
    console.log(step.elapsed_ms, step.status, action);
  }
} finally {
  await delay(80); // Drain the last frame, outside the reported agent time.
  unsubscribe();
  await Promise.allSettled(pending);
  await browser.call("Page.stopScreencast");
  state = agent.snapshot() as AgentSnapshot & Record<string, unknown>;
  const finalPage = await browser.observe({ screenshot: false });
  state.final_page = finalPage;
  state.verification = verify(finalPage);
  state.source_hashes = source_hashes;
  state.recording_errors = errors;
  await Deno.writeTextFile(join(folder, "state.json"), JSON.stringify(state, null, 2));
  await Deno.writeTextFile(
    join(folder, "session.json"),
    JSON.stringify({ target: browser.target, session: browser.session }),
  );
  // Leave the tab open for inspection; only release the DevTools connection.
  await browser.detach();
}
const verification = state.verification as Verification;
console.log(JSON.stringify(verification, null, 2));
let frameCount = 0;
for await (const entry of Deno.readDir(frames)) if (entry.isFile && entry.name.endsWith(".jpg")) frameCount++;
console.log("Screencast frames", frameCount, "errors", JSON.stringify(errors));
if (!verification.passed) {
  console.error("Final-page verification failed");
  Deno.exit(1);
}
