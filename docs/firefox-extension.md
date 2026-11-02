# Firefox extension

Run the same agent — planner (e.g. GLM-5.3 on NVIDIA NIM) plus fast executor (e.g. GPT-OSS-20B on Groq) — inside **your own Firefox tab**, driven from a persistent sidebar.

The extension is the spec's *Firefox Bridge*: Firefox does not speak CDP, so instead of the Chrome path (`Browser` + browser-harness), a WebExtension exposes the live tab to the local agent host over a loopback WebSocket. The host is the same loop you already know: observe → choose → validate → execute.

```text
FIREFOX                          LOCAL HOST (this repo)
┌─────────────────────────┐      ┌─────────────────────────────────┐
│ Sidebar (chat, plan ✓,  │      │ BridgeServer (ws://127.0.0.1)   │
│ activity, stop)         │◄────►│ TaskRunner → Agent              │
│ Background ◄────────────┘      │   planner → policy → text       │
│   └─ content script ────┼─────►│ FirefoxBrowser (observe/act/    │
│      snapshot.js + act  │      │   fresh) — same contract as the │
└─────────────────────────┘      │   Chrome Browser                │
                                 └─────────────────────────────────┘
```

## Install (temporary add-on)

1. **Start the host** (from the repo root) — pick one:

   ```bash
   uv sync
   cp .env.example .env   # add GROQ_API_KEY / NVIDIA_API_KEY, see docs/providers.md
   uv run --env-file .env jev-firefox
   ```

   …or double-click `start-host.bat` (Windows) / `start-host.command` (macOS) / `start-host.sh` (Linux). The first run creates the environment and the `.env` for you — the full no-console walkthrough is [getting-started-gui.md](getting-started-gui.md).

   It prints `Jev Ultrafast Firefox bridge: ws://127.0.0.1:8767` and waits.

2. **Load the extension**: Firefox → `about:debugging` → *This Firefox* → *Load Temporary Add-on* → select `extension/manifest.json` (or the `.xpi` attached to the GitHub release).

3. Open the sidebar with the toolbar button (or `View → Sidebar → Jev Agent`). The dot turns green when the bridge connects.

4. Navigate to the page you want the agent to work on, type a goal in the sidebar, press **Run**.

The agent attaches to the tab you are on, observes its elements, plans (if `PLANNER_*` is configured), and executes step by step. The sidebar shows the live checklist (✓ completed steps), the last screenshot, every action, and a Stop button. If the current tab cannot be scripted (new-tab page, `about:*`), the agent opens DuckDuckGo in a new tab and works there. Errors stay visible in the sidebar until the next run, and a **Test setup** button (plus an automatic check on connect) pings each configured provider and shows per-model status with the provider's exact error message. `export trace` is available from the Python API as usual.

## What runs where

| Piece | File | Notes |
| --- | --- | --- |
| Sidebar UI | `extension/sidebar/` | Chat, plan checklist, activity, models footer. |
| Bridge client | `extension/background.js` | WebSocket to the host, command routing, screenshots (`tabs.captureTab`). |
| Tab driver | `extension/content.js` | Validates observed targets, executes click/type/select/scroll, waits for autocomplete — a port of `jev_ultrafast/browser.py`'s executor. |
| Element indexing | `extension/snapshot.js` | Verbatim copy of `jev_ultrafast/snapshot.js` (a test fails if they drift). |
| WebSocket server | `jev_ultrafast/firefox.py` | Stdlib-only RFC 6455 server on 127.0.0.1; command/response with ids, broadcasts state. |
| Browser driver | `jev_ultrafast/firefox.py` `FirefoxBrowser` | Same `observe/act/fresh/close` contract as the Chrome `Browser`. |
| Task runner | `jev_ultrafast/firefox.py` `TaskRunner` | One task at a time, broadcasts every agent state to the sidebar. Routes browser missions to the fast loop; tool missions to the orchestrator. |
| Orchestrator | `jev_ultrafast/orchestrator.py` + `tools.py` | JSON-protocol tool loop over nine workspace-scoped tools; the planner model drives it. |
| Skills | `skills/*/skill.json` | Keyword-selected procedural instructions appended to the orchestrator prompt. |
| Approvals | `firefox.py` `ApprovalGate` | Sensitive `run_command` calls broadcast `approval_request`; no answer in 120 s = denied. |

Configuration: `FIREFOX_BRIDGE_PORT` (default 8767), `FIREFOX_BRIDGE_TOKEN` (optional shared secret; the extension sends it during hello). All model roles (`PLANNER_*`, `POLICY_*`, `TEXT_MODEL_*`) follow [providers.md](providers.md) — e.g. planner on NVIDIA NIM and executor on Groq at the same time.

## Security model

- The bridge binds **127.0.0.1 only**; the handshake rejects any `Origin` that is not a `moz-extension://` URL.
- Optional token (`FIREFOX_BRIDGE_TOKEN`) compared in constant time during hello.
- API keys never leave the host process; the sidebar only ever sees model names.
- The content script executes **only** the model-chosen operation on an **observed** node id — the same no-selectors, no-code contract as the Chrome path. Freshness guards (document key, form values, target guard) are re-checked before every click/select.
- Tool files are jailed to the per-task `workspace/` directory (path traversal rejected); downloads cap at 25 MB; every tool result is size-capped.
- Terminal commands follow a three-way policy: read-only allow-list (`ls`, `git status`, …) runs — and only with arguments inside the workspace (`cat ~/.ssh/id_rsa` asks first) — destructive patterns (`sudo`, `rm -rf`, `curl | sh`, …) are denied, and anything else — including redirects and compound commands — requires an explicit sidebar approval. Unanswered approvals fail closed. On POSIX, approved commands also run under rlimits (CPU seconds, 2 GB address space, 512 MB file writes).
- Every tool call appends one record to `artifacts/audit.jsonl` (tool, arguments, command verdict, approval, duration, exit code) tagged with the task's trace id — the same id that ties the run record in `runs.jsonl` and every state broadcast.
- All error surfaces pass a redaction layer that masks API-key-shaped strings before they reach logs, artifacts, or the sidebar.

## Bridge protocol (extension ⇄ host)

Extension → host: `hello`, `run {goal, url, tabId}`, `stop`, `check`, `models {refresh}`, `models.select {role, provider, model}`, `params.set {preset}` or `params.set {role, params}`, `profile.save|apply|delete {name}`, `approval_response {id, approved}`.

Host → extension: command/response pairs with ids (`open`, `observe`, `act`, `fresh`); broadcasts `welcome`, `state` (carries `mode`, `selection`, `schema`, `presets`, `profiles`, `tokens`, `providers`, and for orchestrated tasks `log`/`final`/`skills` plus the live browser sub-state under `browser`), `models {registry}`, `approval_request {id, command}`, `delta {text}` (live model output during orchestrated steps, throttled), `error`.

## Known limitations (MVP)

- Clicks and typing use DOM events from the content script, not trusted OS-level input. Most sites accept them; a few strict widgets may not.
- The agent works on the current tab; pages like `about:*`, the Add-ons Manager and other privileged URLs cannot be scripted.
- Background tabs can throttle animations (no focus emulation on Firefox yet); keep the tab visible while it works.
- One task at a time; `Stop` finishes the current action and halts.
- Temporary add-ons are removed when Firefox restarts — reload once per session (or sign the XPI later for permanent install).
