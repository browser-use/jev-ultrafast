const $ = (id) => document.getElementById(id);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const trunc = (text, n) => (String(text ?? "").length > n ? `${String(text).slice(0, n)}…` : String(text ?? ""));

let state = null;
let registry = null; // model catalogue from the host ("models" messages)
let approvalId = null;
let modelsSignature = "";

function setConnection(connected) {
  $("connection").classList.toggle("on", connected);
  $("connection").querySelector("em").textContent = connected ? "host online" : "offline";
  $("run").disabled = !connected;
}

/* ── main render ─────────────────────────────────────────────────── */

function render() {
  if (!state) return;
  const mode = state.mode || "browser";
  const live = mode === "orchestrated" ? state.browser || null : state;
  const running = !["done", "blocked", "idle", "stopped", "error"].includes(state.status);
  $("stop").hidden = !running;
  $("live").hidden = !(live && live.page);
  $("task").hidden = mode !== "orchestrated";
  if (mode === "orchestrated") renderTask(state);
  if (live && live.page) renderLive(live);
  const footer = [state.planner && `planner · ${state.planner}`, state.policy && `policy · ${state.policy}`]
    .filter(Boolean)
    .join("   ·   ") || "no models configured";
  $("models").textContent = footer;
  renderModelsPanel();
  renderProviders(state.providers);
  renderError(state.error);
}

function renderLive(live) {
  const labels = {
    idle: "idle",
    ready: "page observed · ready",
    predicted: "choice ready",
    done: "done ✓",
    blocked: "blocked",
  };
  $("status").textContent = labels[live.status] || live.status || "";
  $("step-count").textContent = [
    live.history && live.history.length ? `${live.history.length} actions` : "",
    live.elapsed_ms ? `${(live.elapsed_ms / 1000).toFixed(1)} s` : "",
    state.tokens ? `${state.tokens >= 1000 ? (state.tokens / 1000).toFixed(1) + "k" : state.tokens} tokens` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  if (live.page.screenshot) $("screenshot").src = `data:image/jpeg;base64,${live.page.screenshot}`;
  $("screenshot").alt = live.page.title || live.page.url;
  const plan = live.plan || [];
  $("plan").innerHTML = plan.length
    ? plan
        .map((step, i) => {
          const cls = i < live.plan_index ? "done" : i === live.plan_index ? "current" : "";
          return `<li class="${cls}"><span>${i < live.plan_index ? "✓" : i + 1}</span>${escape(step)}</li>`;
        })
        .join("")
    : '<li class="done"><span></span>Single-goal run</li>';
  const history = live.history || [];
  $("history").innerHTML = history.length
    ? history
        .slice(-12)
        .map(
          (h) =>
            `<div><span class="number">${String(h.step).padStart(2, "0")}</span><span>${escape(h.action)}` +
            (h.text ? ` <b>“${escape(h.text)}”</b>` : "") +
            `</span><span class="time">${h.latency_ms} ms</span></div>`,
        )
        .join("")
    : '<div><span class="number">—</span><span>No actions yet</span></div>';
}

function renderTask(task) {
  const labels = { running: "working…", done: "done ✓", stopped: "stopped", error: "failed", idle: "idle" };
  $("task-status").textContent = labels[task.status] || task.status || "";
  const usage = task.usage || {};
  const tokens = [usage.input_tokens || usage.prompt_tokens, usage.output_tokens || usage.completion_tokens]
    .filter(Boolean)
    .reduce((a, b) => a + b, 0);
  $("task-meta").textContent = [
    (task.log || []).length ? `${task.log.length} steps` : "",
    task.latency_ms ? `${(task.latency_ms / 1000).toFixed(1)} s` : "",
    tokens ? `${tokens} tokens` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  $("thinking").hidden = true; // a step arrived: the live model output is now a step entry
  const skills = task.skills || [];
  $("task-skills").innerHTML = skills.length
    ? skills.map((s) => `<span class="chip" title="Skill guiding this mission">🧩 ${escape(s)}</span>`).join("")
    : "";
  const log = task.log || [];
  $("task-log").innerHTML = log.length
    ? log
        .map((s) => {
          const head = s.tool
            ? `🔧 <code>${escape(s.tool)}(${escape(compactArgs(s.args))})</code>`
            : s.final
              ? "🏁 final answer"
              : `⚠️ ${escape(s.error || "step")}`;
          const body = [
            s.error && !s.final ? `<span class="err">${escape(s.error)}</span>` : "",
            s.result ? `<span class="res">${escape(trunc(s.result, 240))}</span>` : "",
          ]
            .filter(Boolean)
            .join("");
          return `<div class="task-step${s.error && !s.final ? " bad" : ""}"><div class="head"><span class="number">${String(s.step).padStart(2, "0")}</span><span>${head}</span></div>${body}</div>`;
        })
        .join("")
    : '<div class="task-step"><div class="head"><span class="number">—</span><span>No steps yet</span></div></div>';
  $("task-log").scrollTop = $("task-log").scrollHeight;
  if (task.final) {
    $("task-final").textContent = task.final;
    $("task-final").hidden = false;
  } else {
    $("task-final").hidden = true;
  }
}

function compactArgs(args) {
  if (!args || typeof args !== "object") return "";
  const parts = Object.entries(args).map(([k, v]) => `${k}=${trunc(typeof v === "string" ? v : JSON.stringify(v), 60)}`);
  return parts.join(", ");
}

/* ── models panel (MVP-1) ────────────────────────────────────────── */

function modelOptions(roleKey, selection, registryData) {
  const current = `${selection.provider || ""}::${selection.model || ""}`;
  let html = "";
  if (roleKey === "policy" && state && state.policy_builtin) {
    const active = !selection.provider || selection.provider === "typesafe";
    html += `<option value="builtin:jev"${active ? " selected" : ""}>TypeSafe Jev (built-in)</option>`;
  }
  const groups = [];
  if (registryData && registryData.providers) {
    for (const [provider, report] of Object.entries(registryData.providers)) {
      if (!report || !report.ok || !report.models || !report.models.length) continue;
      const opts = report.models
        .map((m) => {
          const value = `${provider}::${m.id}`;
          const caps = m.capabilities
            ? [m.capabilities.vision ? "👁" : "", m.capabilities.reasoning ? "🧠" : ""].filter(Boolean).join("")
            : "";
          return `<option value="${escape(value)}"${value === current ? " selected" : ""}>${escape(m.displayName)}${caps ? " " + caps : ""}</option>`;
        })
        .join("");
      groups.push(`<optgroup label="${escape(provider)}">${opts}</optgroup>`);
    }
  }
  const inCatalogue = groups.some((g) => g.includes(`value="${escape(current)}"`)) || html.includes('value="builtin:jev"');
  if (current && !current.endsWith("::") && !inCatalogue) {
    const label = selection.display && selection.display !== "—" ? selection.display : `${selection.provider}:${selection.model}`;
    groups.push(`<optgroup label="current">${`<option value="${escape(current)}" selected>${escape(label)} (current)</option>`}</optgroup>`);
  }
  return html + groups.join("");
}

function renderModelsPanel() {
  if (!state || !state.selection || !state.schema) return;
  const signature = JSON.stringify([state.selection, state.presets, state.schema, state.profiles, registry]);
  if (signature === modelsSignature) return; // don't rebuild while the user interacts
  modelsSignature = signature;
  const roles = state.schema.roles || [];
  const params = state.schema.parameters || {};
  const presets = state.presets || {};
  $("presets").innerHTML = Object.entries(presets)
    .map(
      ([key, p]) =>
        `<button class="chip" data-preset="${escape(key)}" title="${escape(p.description || "")}">${escape(p.label)}</button>`,
    )
    .join("");
  $("profiles").innerHTML = (state.profiles || [])
    .map(
      (name) =>
        `<span class="chip profile" data-profile="${escape(name)}" title="Click to apply this profile">${escape(name)}` +
        `<i class="del" data-profile-del="${escape(name)}" title="Delete profile">×</i></span>`,
    )
    .join("") || '<span class="hint">none saved yet</span>';
  $("model-roles").innerHTML = roles
    .map((role) => {
      const sel = state.selection[role.key] || {};
      const controls = Object.entries(params)
        .map(([name, schema]) => {
          if (schema.type === "enum") {
            const options = [`<option value=""${!sel.params || !sel.params[name] ? " selected" : ""}>Default</option>`]
              .concat(
                (schema.values || []).map(
                  (v) =>
                    `<option value="${escape(v)}"${sel.params && sel.params[name] === v ? " selected" : ""}>${escape((schema.labels && schema.labels[v]) || v)}</option>`,
                ),
              )
              .join("");
            return `<label>${escape(name)}</label><select data-param="${escape(name)}" data-role="${escape(role.key)}">${options}</select>`;
          }
          return `<label>${escape(name)}</label><input type="number" data-param="${escape(name)}" data-role="${escape(role.key)}" min="${schema.min}" max="${schema.max}" step="${schema.step}" placeholder="default" value="${sel.params && sel.params[name] ? escape(sel.params[name]) : ""} />`;
        })
        .join("");
      return `<div class="role-row">
        <div class="role-head"><b>${escape(role.label)}</b><span class="hint">${escape(role.hint || "")}</span></div>
        <select class="model-select" data-role="${escape(role.key)}">${modelOptions(role.key, sel, registry)}</select>
        <details class="adv"><summary>advanced</summary><div class="param-grid">${controls}</div></details>
      </div>`;
    })
    .join("");
  renderRegistryStatus();
  wireModelsPanel();
}

function renderRegistryStatus() {
  const box = $("registry-status");
  if (!registry) {
    box.textContent = "catalogue not loaded";
    return;
  }
  const providers = Object.values(registry.providers || {});
  const ok = providers.filter((p) => p && p.ok);
  const models = ok.reduce((sum, p) => sum + (p.models ? p.models.length : 0), 0);
  const failed = providers.length - ok.length;
  box.textContent = `${models} models · ${ok.length} providers${failed ? ` · ${failed} failed` : ""}`;
}

function wireModelsPanel() {
  document.querySelectorAll(".model-select").forEach((select) => {
    select.addEventListener("change", async () => {
      const role = select.dataset.role;
      let provider = "";
      let model = "";
      if (select.value !== "builtin:jev") {
        [provider, model] = select.value.split("::");
      }
      const reply = await browser.runtime.sendMessage({ cmd: "select-model", role, provider, model });
      if (reply && reply.error) showError(reply.error);
    });
  });
  document.querySelectorAll("[data-param]").forEach((input) => {
    const handler = async () => {
      const value = input.value === "" ? null : input.value;
      const params = { [input.dataset.param]: value };
      const reply = await browser.runtime.sendMessage({ cmd: "params", role: input.dataset.role, params });
      if (reply && reply.error) showError(reply.error);
    };
    if (input.tagName === "SELECT") input.addEventListener("change", handler);
    else input.addEventListener("change", handler);
  });
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", async () => {
      const reply = await browser.runtime.sendMessage({ cmd: "params", preset: button.dataset.preset });
      if (reply && reply.error) showError(reply.error);
    });
  });
  document.querySelectorAll("[data-profile]").forEach((chip) => {
    chip.addEventListener("click", async () => {
      const reply = await browser.runtime.sendMessage({ cmd: "profile", action: "apply", name: chip.dataset.profile });
      if (reply && reply.error) showError(reply.error);
    });
  });
  document.querySelectorAll("[data-profile-del]").forEach((del) => {
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      const reply = await browser.runtime.sendMessage({ cmd: "profile", action: "delete", name: del.dataset.profileDel });
      if (reply && reply.error) showError(reply.error);
    });
  });
}

/* ── providers / error ───────────────────────────────────────────── */

function renderProviders(providers) {
  const box = $("providers");
  if (!providers || !Object.keys(providers).length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const names = { planner: "Planner", policy: "Executor", text: "Text writer" };
  box.innerHTML = Object.values(providers)
    .map((p) => {
      const dot = p.ok ? "🟢" : "🔴";
      const extra = p.ok
        ? `${p.latency_ms != null ? ` ${p.latency_ms} ms` : ""}`
        : `<small>${escape(p.detail || "not configured")}</small>`;
      return `<div class="provider ${p.ok ? "ok" : "bad"}" title="${escape(p.detail || "")}">${dot} ${names[p.role] || p.role} · <b>${escape(p.model || "?")}</b>${extra}</div>`;
    })
    .join("");
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

function renderError(error) {
  if (error) showError(error);
}

/* ── approvals (MVP-4) ───────────────────────────────────────────── */

function showApproval(message) {
  approvalId = message.id;
  $("approval-command").textContent = message.command || "";
  $("approval").hidden = false;
}

async function answerApproval(approved) {
  if (!approvalId) return;
  const id = approvalId;
  approvalId = null;
  $("approval").hidden = true;
  await browser.runtime.sendMessage({ cmd: "approval", id, approved });
}

/* ── messages ────────────────────────────────────────────────────── */

browser.runtime.onMessage.addListener((message) => {
  if (message.type === "status") setConnection(message.connected);
  if (message.type === "state") {
    state = message.state;
    render();
  }
  if (message.type === "error") {
    // The next state broadcast carries the same error persistently; show it now.
    showError(message.message);
  }
  if (message.type === "checking") {
    $("providers").hidden = false;
    $("providers").innerHTML = '<div class="provider">⏳ Checking the model connections…</div>';
  }
  if (message.type === "models") {
    registry = message.registry || null;
    modelsSignature = ""; // force a rebuild with the fresh catalogue
    if (message.error) showError(message.error);
    if (state) render();
  }
  if (message.type === "approval_request") showApproval(message);
  if (message.type === "delta") {
    $("thinking").textContent = message.text || "";
    $("thinking").hidden = false;
  }
});

$("run").addEventListener("click", async () => {
  const goal = $("goal").value.trim();
  if (!goal) return;
  $("error").hidden = true;
  $("error").textContent = "";
  if (state) state.error = null;
  const reply = await browser.runtime.sendMessage({ cmd: "run", goal });
  if (reply && reply.error) showError(reply.error);
});

$("check").addEventListener("click", async () => {
  $("error").hidden = true;
  const reply = await browser.runtime.sendMessage({ cmd: "check" });
  if (reply && reply.error) showError(reply.error);
});

$("stop").addEventListener("click", () => {
  browser.runtime.sendMessage({ cmd: "stop" });
});

$("refresh-models").addEventListener("click", async () => {
  $("registry-status").textContent = "refreshing…";
  const reply = await browser.runtime.sendMessage({ cmd: "models", refresh: true });
  if (reply && reply.error) showError(reply.error);
});

$("profile-save").addEventListener("click", async () => {
  const name = $("profile-name").value.trim();
  if (!name) return;
  const reply = await browser.runtime.sendMessage({ cmd: "profile", action: "save", name });
  if (reply && reply.error) showError(reply.error);
  else $("profile-name").value = "";
});

$("approval-yes").addEventListener("click", () => answerApproval(true));
$("approval-no").addEventListener("click", () => answerApproval(false));

(async () => {
  const status = await browser.runtime.sendMessage({ cmd: "status" });
  setConnection(status.connected);
  if (status.state) {
    state = status.state;
    render();
  }
  if (status.connected) {
    browser.runtime.sendMessage({ cmd: "models" }).catch(() => {}); // ask for the catalogue
  }
})();
