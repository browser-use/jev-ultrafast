const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
const SITE_NAMES = {
  flights: "Google Flights",
  booking: "Booking.com",
  expedia: "Expedia",
  travel: "Travel planner (fixture)",
  research: "Reading room (fixture)",
};
const goals = {
  flights: 'Find one-way flights from Zurich to London on September 20, 2026, for one adult in economy. Stop when matching flight options are visible. Do not select or book a flight.',
  booking: 'Find the best round-trip flight options from Paris to Cancun, departing 2nd of October and returning 21st of November, for 2 people in economy class. Stop when matching flight options are visible. Do not select or book a flight.',
  expedia: 'Find the best round-trip flight options from Paris to Cancun, departing 2nd of October and returning 21st of November, for 2 people in economy class. Stop when matching flight options are visible. Do not select or book a flight.',
  travel: 'Find a Design stay in Lisbon with Free cancellation and open Casa Flora.',
  research:
    "Open the article about using finite choices to control browser agents.",
};

let state = { sites: {} };
let busy = false;
const autoFlags = new Set(); // scenarios currently running their own "Run automatically" loop
let runAllActive = false;
const panels = new Map(); // scenario -> panel root element

const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value) => `${(value * 100).toFixed(value < 0.01 ? 1 : 0)}%`;

async function call(name, body = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  state = data;
  render();
  return data;
}

function checkedSites() {
  return [...document.querySelectorAll('input[name="site"]:checked')].map((i) => i.value);
}

function createPanel(scenario) {
  const root = $("site-template").content.firstElementChild.cloneNode(true);
  root.dataset.scenario = scenario;
  root.querySelector(".site-name").textContent = SITE_NAMES[scenario] || scenario;
  root.querySelector(".choose").addEventListener("click", () =>
    performSite(scenario, () => call("predict", { scenario }), "Jev is comparing the actions…"),
  );
  root.querySelector(".execute").addEventListener("click", () =>
    performSite(
      scenario,
      () => call("act", { scenario, fingerprint: state.sites[scenario].page.fingerprint }),
      "Executing the choice…",
    ),
  );
  root.querySelector(".auto").addEventListener("click", () =>
    performSite(
      scenario,
      async () => {
        autoFlags.add(scenario);
        updateControls();
        const maxSteps = state.max_steps;
        for (let i = 0; i < maxSteps * 2 && autoFlags.has(scenario); i++) {
          setStatusText(scenario, "Running…");
          if ($("pace").checked) {
            await call("predict", { scenario });
            await new Promise((resolve) => setTimeout(resolve, 450));
            if (!autoFlags.has(scenario)) break;
            await call("act", { scenario, fingerprint: state.sites[scenario].page.fingerprint });
          } else {
            await call("tick", { scenario });
          }
          if (["done", "blocked"].includes(state.sites[scenario].status)) break;
        }
        autoFlags.delete(scenario);
      },
      "Running the browser…",
    ),
  );
  root.querySelector(".stop").addEventListener("click", () => {
    autoFlags.delete(scenario);
    setStatusText(scenario, "Pausing after the current request…");
    updateControls();
  });
  root.querySelector(".download").addEventListener("click", () => {
    const { page, ...rest } = state.sites[scenario];
    const blob = new Blob(
      [JSON.stringify({ ...rest, page: { ...page, screenshot: undefined } }, null, 2)],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `typesafe-browser-trace-${scenario}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });
  root.querySelector(".choices").addEventListener("pointerover", (event) => {
    const id = event.target.closest("[data-action]")?.dataset.action;
    root.querySelectorAll(".target").forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === id ||
          t.dataset.action === state.sites[scenario]?.decision?.target?.split(":")[0],
      ),
    );
  });
  root.querySelector(".choices").addEventListener("pointerleave", () => {
    root.querySelectorAll(".target").forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === state.sites[scenario]?.decision?.target?.split(":")[0],
      ),
    );
  });
  return root;
}

function setStatusText(scenario, text) {
  panels.get(scenario)?.querySelector(".status-text").replaceChildren(document.createTextNode(text));
}

async function performSite(scenario, fn, label) {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  updateControls();
  setStatusText(scenario, label);
  try {
    await fn();
  } catch (error) {
    autoFlags.delete(scenario);
    try {
      state = await fetch("/api/state").then((r) => r.json());
      render();
    } catch {
      /* Preserve the original failure if the server disconnected. */
    }
    setStatusText(scenario, `Paused · ${error.message}`);
  } finally {
    busy = false;
    updateControls();
  }
}

async function performGlobal(fn) {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  updateControls();
  try {
    await fn();
  } catch (error) {
    $("error").textContent = error.message;
    $("error").hidden = false;
  } finally {
    busy = false;
    updateControls();
  }
}

function updateControls() {
  const scenarios = Object.keys(state.sites);
  const anyLive =
    scenarios.some((s) => !["done", "blocked"].includes(state.sites[s].status)) ||
    (state.queued || []).length > 0;
  $("queue-note").textContent = (state.queued || []).length
    ? `Waiting to start: ${state.queued.map((s) => SITE_NAMES[s] || s).join(", ")}`
    : "";
  $("run-all").disabled = busy || !anyLive;
  $("run-all").hidden = runAllActive;
  $("stop-all").hidden = !runAllActive;
  $("start").disabled = busy;
  document.querySelectorAll('input[name="site"]').forEach((i) => (i.disabled = busy));
  $("goal").disabled = busy;
  for (const scenario of scenarios) {
    const root = panels.get(scenario);
    if (!root) continue;
    const site = state.sites[scenario];
    const live = site.page && !["done", "blocked"].includes(site.status);
    const auto = autoFlags.has(scenario);
    root.querySelector(".choose").disabled = busy || !live;
    root.querySelector(".execute").disabled = busy || !site.decision || !live;
    root.querySelector(".auto").disabled = busy || !live;
    root.querySelector(".auto").hidden = auto;
    root.querySelector(".stop").hidden = !auto;
    root.querySelector(".download").disabled = !site.history?.length;
  }
}

function renderSite(scenario, site, root) {
  const labels = {
    idle: "Ready to explore",
    ready: "Page observed · ready for a decision",
    predicted: "Choice ready · inspect or execute",
    done: "Jev reports complete · inspect the page",
    blocked: "Stopped · no supported next action",
  };
  if (!autoFlags.has(scenario)) setStatusText(scenario, labels[site.status] || site.status);
  const page = site.page,
    d = site.decision || (site.status === "done" ? site.decisions?.at(-1) : null);
  root.querySelector(".plan").innerHTML = (site.plan || [])
    .map(
      (goal, i) =>
        `<div class="plan-step ${i === site.plan_index ? "current" : ""}"><span>${i < site.plan_index ? "✓" : i + 1}</span>${escape(goal)}</div>`,
    )
    .join("");
  if (!page) return;
  root.querySelector(".empty").hidden = true;
  const screenshot = root.querySelector(".screenshot");
  screenshot.hidden = false;
  screenshot.src = `data:image/jpeg;base64,${page.screenshot}`;
  root.querySelector(".url").textContent = page.url;
  root.querySelector(".page-title").textContent = page.title;
  root.querySelector(".action-count").textContent = `${site.elements.length} elements`;
  const chosen = page.actions.find((a) => a.id === d?.choice);
  root.querySelector(".choice-title").textContent = d ? chosen?.label || d.choice : "Choose an action";
  root.querySelector(".latency").textContent = d ? `${d.latency_ms} ms` : "—";
  root.querySelector(".confidence").textContent =
    d?.target_confidence != null ? percent(d.target_confidence) : "—";
  root.querySelector(".completion").textContent = d ? d.operation : "—";
  root.querySelector(".ranking-note").textContent = d ? "Ranked by Jev" : "Unranked";
  const op = Object.entries(d?.operation_probabilities || {}).sort((a, b) => b[1] - a[1]);
  root.querySelector(".operation-choices").innerHTML = op
    .map(
      ([name, p]) =>
        `<span class="operation-choice ${name === d.operation ? "best" : ""}">${escape(name)} <b>${percent(p)}</b></span>`,
    )
    .join("");
  const probability = (e) =>
    d?.target_probabilities[e.index] ??
    Math.max(-1, ...(e.options || []).map((o) => d?.target_probabilities[o.index] ?? -1));
  const selectedIndex = d?.target?.split(":")[0];
  const elements = [...site.elements];
  if (d) elements.sort((a, b) => probability(b) - probability(a));
  root.querySelector(".choices").innerHTML = elements
    .map((e) => {
      const p = probability(e);
      return `<div class="choice ${selectedIndex === e.index ? "best" : ""}" data-action="${escape(e.index)}"><span class="choice-id">[${escape(e.index)}]</span><div class="choice-label">${escape(e.label)}<small>${escape(e.role)} · ${escape(e.operations.join(" / "))}${e.value ? " · " + escape(e.value) : ""}${e.checked !== undefined ? " · checked " + escape(e.checked) : ""}</small>${p >= 0 ? `<div class="bar" style="--probability:${p * 100}%"></div>` : ""}</div><span class="probability">${p >= 0 ? percent(p) : "—"}</span></div>`;
    })
    .join("");
  const targets = new Map();
  for (const a of page.actions) if (a.rect && !targets.has(a.node)) targets.set(a.node, a);
  root.querySelector(".targets").innerHTML = [...targets.values()]
    .map((a, i) => {
      const index = String(i + 1);
      return `<div class="target ${index === selectedIndex ? "selected" : ""}" data-action="${index}" style="left:${(100 * a.rect.x) / page.w}%;top:${(100 * a.rect.y) / page.h}%;width:${(100 * a.rect.w) / page.w}%;height:${(100 * a.rect.h) / page.h}%"><span>${index}</span></div>`;
    })
    .join("");
  root.querySelector(".targets").hidden = !$("overlays").checked;
  root.querySelector(".history").innerHTML = site.history.length
    ? site.history
        .map(
          (h) =>
            `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.page_changed ? "Page changed" : "No change observed"}</span></div>`,
        )
        .join("")
    : '<p class="muted">Each executed action leaves an observed result.</p>';
  root.querySelector(".step-count").textContent =
    `${site.history.length} actions · ${(site.elapsed_ms / 1000).toFixed(2)} s`;
  root.querySelector(".model-state").textContent = JSON.stringify(
    d?.request || {
      goal: site.goal,
      url: page.url,
      text: page.text,
      actions: page.actions.map(({ rect, node, ...rest }) => rest),
    },
    null,
    2,
  );
}

function render() {
  $("helper").textContent = `Text helper · ${state.text_model}`;
  const setupError = state.errors?.setup;
  if (setupError) {
    $("error").textContent = setupError;
    $("error").hidden = false;
  }
  const scenarios = Object.keys(state.sites);
  const mounted = [...panels.keys()];
  if (mounted.join(",") !== scenarios.join(",")) {
    panels.clear();
    $("sites").innerHTML = "";
    for (const scenario of scenarios) {
      const root = createPanel(scenario);
      panels.set(scenario, root);
      $("sites").appendChild(root);
    }
  }
  for (const scenario of scenarios) renderSite(scenario, state.sites[scenario], panels.get(scenario));
  updateControls();
}

$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runAllActive = false;
  autoFlags.clear();
  const scenarios = checkedSites();
  if (!scenarios.length) {
    $("error").textContent = "Pick at least one site";
    $("error").hidden = false;
    return;
  }
  performGlobal(() => call("reset", { scenarios, goal: $("goal").value }));
});
document.querySelectorAll('input[name="site"]').forEach((input) =>
  input.addEventListener("change", () => {
    const known = new Set(Object.values(goals));
    const checked = checkedSites();
    if (checked.length === 1 && known.has($("goal").value.trim())) {
      $("goal").value = goals[checked[0]];
    }
  }),
);
$("run-all").addEventListener("click", () =>
  performGlobal(async () => {
    runAllActive = true;
    updateControls();
    const totalSites = Object.keys(state.sites).length + (state.queued || []).length;
    const budget = state.max_steps * 2 * Math.max(1, totalSites);
    for (let i = 0; i < budget && runAllActive; i++) {
      await call("tick_all");
      const scenarios = Object.keys(state.sites);
      const allSettled = scenarios.every((s) => ["done", "blocked"].includes(state.sites[s].status));
      if (allSettled && !(state.queued || []).length) break;
    }
    runAllActive = false;
  }),
);
$("stop-all").addEventListener("click", () => {
  runAllActive = false;
  updateControls();
});
$("overlays").addEventListener("change", () => {
  for (const root of panels.values()) root.querySelector(".targets").hidden = !$("overlays").checked;
});
fetch("/api/state")
  .then((r) => r.json())
  .then((s) => {
    state = s;
    render();
  })
  .catch(() => {
    $("error").textContent = "Cannot reach local demo server";
    $("error").hidden = false;
  });
