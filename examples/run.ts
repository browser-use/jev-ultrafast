/** deno task example --url URL --goal 'A narrow goal' */

import { parseArgs } from "@std/cli/parse-args";
import { Agent, type AgentSnapshot } from "../src/mod.ts";

const USAGE = "usage: run.ts [-h] --url URL --goal GOAL";

const args = parseArgs(Deno.args, {
  string: ["url", "goal"],
  collect: ["goal"],
  boolean: ["help"],
  alias: { h: "help" },
  unknown: (arg) => {
    console.error(`${USAGE}\nrun.ts: error: unrecognized arguments: ${arg}`);
    Deno.exit(2);
  },
});
if (args.help) {
  console.log(`${USAGE}\n\noptions:\n  --url URL\n  --goal GOAL  Repeat for an ordered list of goals.`);
  Deno.exit(0);
}
const missing = [!args.url && "--url", !args.goal.length && "--goal"].filter(Boolean);
if (missing.length) {
  console.error(`${USAGE}\nrun.ts: error: the following arguments are required: ${missing.join(", ")}`);
  Deno.exit(2);
}

{
  await using agent = await Agent.create(args.url!, args.goal);
  let state: AgentSnapshot = agent.snapshot();
  for await (state of agent.run()) {
    console.log(`${String(state.elapsed_ms).padStart(5)} ms  ${state.history.length} actions  ${state.status}`);
  }
  console.log(state.page.url);
}
