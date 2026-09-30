/**
 * Renders the panel's element line out of the shipped static/app.js and compares the strings.
 *
 * No browser, no model call: the production file is loaded into node against a fake DOM that only
 * records what render() writes into #action-count, so the judgement is on rendered text, not on
 * whether a word appears in the file.
 *
 * Run: node scripts/panel_note_check.js [path/to/app.js]
 */

const fs = require("fs");
const path = require("path");

const APP = process.argv[2] || path.join(__dirname, "..", "jev_ultrafast", "static", "app.js");
const source = fs.readFileSync(APP, "utf8");

const nodes = new Map();
const element = (id) => {
  if (!nodes.has(id)) {
    const store = {
      id,
      textContent: "",
      innerHTML: "",
      hidden: false,
      disabled: false,
      checked: true,
      src: "",
      value: "",
      style: {},
      addEventListener: () => {},
    };
    nodes.set(id, new Proxy(store, { get: (t, k) => (k in t ? t[k] : undefined), set: (t, k, v) => ((t[k] = v), true) }));
  }
  return nodes.get(id);
};

const document = {
  getElementById: element,
  querySelector: () => ({ content: "test-token" }),
  addEventListener: () => {},
  createElement: () => element("created"),
};

// The top-level wiring installs timers and window listeners; fake them, this check only judges render().
const globals = {
  addEventListener: () => {},
  setInterval: () => 0,
  setTimeout: () => 0,
  requestAnimationFrame: () => 0,
};

// render() reads the script's own top-level `state`, which is out of reach from outside: append a closure setter.
const load = new Function(
  "document",
  "window",
  "fetch",
  "addEventListener",
  "setInterval",
  "setTimeout",
  "requestAnimationFrame",
  `${source}\nreturn { render, setState: (value) => { state = value; }, getState: () => state };`,
);
const app = load(
  document,
  globals,
  async () => ({ ok: true, json: async () => ({}) }),
  globals.addEventListener,
  globals.setInterval,
  globals.setTimeout,
  globals.requestAnimationFrame,
);

const page = (extra) => ({
  url: "https://www.google.com/travel/flights",
  title: "Flights",
  w: 1120,
  h: 780,
  text: "one way",
  actions: [{ id: "e1", kind: "click", label: "Search", rect: { x: 1, y: 2, w: 10, h: 10 }, node: 1 }],
  fingerprint: "fp",
  screenshot: "",
  ...extra,
});

const state = (pageExtra) => ({
  status: "ready",
  goal: "find flights",
  page: page(pageExtra),
  elements: [{ index: "1", label: "Search", role: "button", operations: ["CLICK"] }],
  decision: null,
  history: [],
  decisions: [],
  elapsed_ms: 1200,
  cost: { jev_calls: 1, text_calls: 0, input_tokens: 1234, usd: 0.0005 },
});

const cases = [
  [{}, "1 elements"],
  [{ overlay_reads: 2 }, "1 elements · 2 re-reads for the overlay"],
  [{ overlay_reads: 1 }, "1 elements · 1 re-read for the overlay"],
  [{ aria_hidden_fallback: true }, "1 elements · read through the hidden page"],
  [{ aria_hidden_fallback: true, overlay_reads: 3 }, "1 elements · read through the hidden page · 3 re-reads for the overlay"],
];

let failed = 0;
for (const [extra, want] of cases) {
  nodes.clear();
  app.setState(state(extra));
  app.render();
  const got = element("action-count").textContent;
  const ok = got === want;
  if (!ok) failed += 1;
  console.log(`${ok ? "OK  " : "FAIL"} ${JSON.stringify(extra)} -> ${JSON.stringify(got)}${ok ? "" : `  want ${JSON.stringify(want)}`}`);
}
console.log(failed ? `\n${failed}/${cases.length} rendered lines differ` : `\n${cases.length}/${cases.length} rendered lines match`);
process.exit(failed ? 1 : 0);
