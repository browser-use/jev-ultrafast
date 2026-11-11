/**
 * Renders the panel's element line, trace rows and cost line out of the shipped static/app.js and
 * compares the strings.
 *
 * No browser, no model call: the production file is loaded into node against a fake DOM that only
 * records what render() writes, so the judgement is on rendered text, not on whether a word appears
 * in the file.
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

const rows = (steps) =>
  steps.map(([step, action, text, helper, latency_ms, probability, page_changed]) => ({
    step,
    action,
    text,
    text_helper: helper,
    latency_ms,
    probability,
    page_changed,
  }));

const state = (pageExtra, top) => ({
  status: "ready",
  goal: "find flights",
  page: page(pageExtra),
  elements: [{ index: "1", label: "Search", role: "button", operations: ["CLICK"] }],
  decision: null,
  history: [],
  decisions: [],
  elapsed_ms: 1200,
  cost: { jev_calls: 1, text_calls: 0, input_tokens: 999, usd: 0.0005 },
  ...top,
});

// Three promises, three groups. Each group names its own tally so a red line says which promise moved.
const noteCases = [
  [{}, "1 elements"],
  [{ overlay_reads: 2 }, "1 elements · 2 re-reads for the overlay"],
  [{ overlay_reads: 1 }, "1 elements · 1 re-read for the overlay"],
  [{ aria_hidden_fallback: true }, "1 elements · read through the hidden page"],
  [{ aria_hidden_fallback: true, overlay_reads: 3 }, "1 elements · read through the hidden page · 3 re-reads for the overlay"],
];

const traceCases = [
  [
    [],
    '<p class="muted">Each executed action leaves an observed result.</p>',
    "empty history",
  ],
  [
    rows([[1, 'TYPE "origin"', "Zurich", "Gemini 3.1 Flash Lite", 412, 0.874, true]]),
    '<div class="trace-row"><span class="number">01</span><div>TYPE &quot;origin&quot; <b>“Zurich”</b>' +
      '<small>Gemini 3.1 Flash Lite</small></div><span class="time">412 ms · 87%</span>' +
      '<span class="effect">Page changed</span></div>',
    "one row credited to the text helper",
  ],
  [
    rows([
      [2, "CLICK", "", "", 9, 0.008, false],
      [10, "PRESS ENTER", "tab", "Mercury 2.5", 61, 0.5, true],
    ]),
    '<div class="trace-row"><span class="number">02</span><div>CLICK</div><span class="time">9 ms · 0.8%</span>' +
      '<span class="effect">No change observed</span></div>' +
      '<div class="trace-row"><span class="number">10</span><div>PRESS ENTER <b>“tab”</b>' +
      '<small>Mercury 2.5</small></div><span class="time">61 ms · 50%</span>' +
      '<span class="effect">Page changed</span></div>',
    "two rows joined without a separator",
  ],
];

const costHistory = rows([
  [2, "CLICK", "", "", 9, 0.008, false],
  [10, "PRESS ENTER", "tab", "Mercury 2.5", 61, 0.5, true],
]);

// Prefix, not whole line: the tail is where the in-flight calibration layer appends its audit count. A dropped
// or reordered field inside the prefix goes red; a longer tail does not, and that limit is stated on purpose.
const costCases = [
  [
    { elapsed_ms: 1200, cost: { jev_calls: 1, text_calls: 0, input_tokens: 999, usd: 0.0005 } },
    "2 actions · 1.20 s · 1 decision + 0 text calls · 999 jev in tok · $0.0005",
  ],
  [
    {
      elapsed_ms: 12345,
      cost: { jev_calls: 3, text_calls: 2, input_tokens: 999, usd: 0.0123 },
    },
    "2 actions · 12.35 s · 3 decision + 2 text calls · 999 jev in tok · $0.0123",
  ],
];

let failed = 0;
const report = (group, want, got, matched) => {
  if (!matched) failed += 1;
  console.log(`${matched ? "OK  " : "FAIL"} ${group} -> ${JSON.stringify(got)}${matched ? "" : `  want ${JSON.stringify(want)}`}`);
  return matched;
};

const seen = { notes: 0, trace: 0, cost: 0 };
const hit = { notes: 0, trace: 0, cost: 0 };

for (const [extra, want] of noteCases) {
  nodes.clear();
  seen.notes += 1;
  app.setState(state(extra));
  app.render();
  const got = element("action-count").textContent;
  if (report("note", want, got, got === want)) hit.notes += 1;
}

for (const [history, want, label] of traceCases) {
  nodes.clear();
  seen.trace += 1;
  app.setState(state({}, { history }));
  app.render();
  const got = element("history").innerHTML;
  if (report(`trace (${label})`, want, got, got === want)) hit.trace += 1;
}

for (const [top, want] of costCases) {
  nodes.clear();
  seen.cost += 1;
  app.setState(state({}, { history: costHistory, ...top }));
  app.render();
  const got = element("step-count").textContent;
  if (report("cost", want, got, got.startsWith(want))) hit.cost += 1;
}

const tally = (name, hitCount, seenCount) => `${name}: ${hitCount}/${seenCount} match`;
console.log("");
for (const [name, count] of [
  ["notes", seen.notes],
  ["trace", seen.trace],
  ["cost", seen.cost],
]) {
  console.log(tally(name, hit[name], count));
}
const total = seen.notes + seen.trace + seen.cost;
const passed = hit.notes + hit.trace + hit.cost;
console.log(`TOTAL ${passed}/${total} rendered strings match`);
process.exit(failed ? 1 : 0);
