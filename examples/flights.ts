/**
 * Live Google Flights search. Calls TypeSafe; never selects or books a flight.
 *
 * deno task flights [--output artifacts/flights/latest] [--keep-open]
 */

import { parseArgs } from "@std/cli/parse-args";
import { decodeBase64Url } from "@std/encoding/base64url";
import { Agent, type AgentSnapshot, type Browser, type PageState } from "../src/mod.ts";

export const URL = "https://www.google.com/travel/flights?hl=en";
export const GOALS = "Find one-way flights from Zurich to London on September 20, 2026, for one adult in economy. " +
  "Stop when matching flight options are visible. Do not select or book a flight.";

export interface Verification {
  passed: boolean;
  checks: Record<string, boolean>;
  visible_flights: string[];
}

type LooseAction = { label: string; value?: unknown };

function bytesContain(bytes: Uint8Array, needle: string): boolean {
  // Latin-1 view of the decoded bytes: every byte maps to one code unit.
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return text.includes(needle);
}

/** Independent checks on the resulting page, not the model's DONE answer. */
export function verify(page: PageState): Verification {
  let hostname = "";
  let pathname = "";
  let encoded = "";
  try {
    const parsed = new globalThis.URL(page.url);
    hostname = parsed.hostname;
    pathname = parsed.pathname;
    encoded = parsed.searchParams.get("tfs") ?? "";
  } catch {
    // An unparsable URL fails the page/date checks below.
  }
  let dateInUrl: boolean;
  try {
    dateInUrl = bytesContain(decodeBase64Url(encoded + "=".repeat((4 - (encoded.length % 4)) % 4)), "2026-09-20");
  } catch {
    dateInUrl = false;
  }
  const actions = page.actions as unknown as LooseAction[];
  const values = new Map<string, unknown>();
  for (const a of actions) values.set(a.label.trim(), a.value ?? null);
  const flights = actions.filter((a) => a.label.includes("Select flight")).map((a) => a.label);
  const checks: Record<string, boolean> = {
    search_page: hostname === "www.google.com" && pathname === "/travel/flights/search",
    one_way: values.get("Change ticket type. One way") === "One way",
    origin: values.get("Where from?") === "Zürich",
    destination: values.get("Where to?") === "London",
    date: values.get("Departure") === "Sun, Sep 20",
    year: dateInUrl || page.text.includes("departing 2026-09-20"),
    results: flights.length > 0 && flights.every((f) => f.includes("Sunday, September 20")),
  };
  return { passed: Object.values(checks).every(Boolean), checks, visible_flights: flights };
}

async function main(): Promise<void> {
  const args = parseArgs(Deno.args, {
    string: ["output"],
    boolean: ["keep-open"],
    default: { output: "artifacts/flights/latest" },
  });
  const folder = args.output;
  await Deno.mkdir(folder, { recursive: true });
  const agent = await Agent.create(URL, GOALS);
  let verification: Verification;
  try {
    for await (const step of agent.run()) {
      const last = step.history.at(-1);
      console.log(step.elapsed_ms, step.status, last?.action ?? "");
    }
  } finally {
    const state: AgentSnapshot & { verification?: Verification } = agent.snapshot();
    verification = verify(state.page);
    state.verification = verification;
    await Deno.writeTextFile(`${folder}/state.json`, JSON.stringify(state, null, 2));
    await Deno.writeTextFile(
      `${folder}/session.json`,
      JSON.stringify({ target: agent.browser.target, session: agent.browser.session }),
    );
    if (!args["keep-open"]) await agent.close();
    // Keep the tab open, but release the DevTools connection so the process can exit.
    else await (agent.browser as Browser).detach();
  }
  console.log(JSON.stringify(verification, null, 2));
  if (!verification.passed) {
    console.error("Final page did not satisfy the route/date checks");
    Deno.exit(1);
  }
}

if (import.meta.main) await main();
