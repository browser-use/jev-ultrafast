---
name: jev-browser-use
description: Fast browser actions with TypeSafe Jev. Codex handles planning, text input, visual interpretation, and verification; Jev handles navigation, clicks, toggles, and scrolling through the existing Computer Use runtime. Claude Code installation is supported; browser integration is coming soon.
---

# Jev browser operations

Installable through `npx skills add` in Codex, Claude Code, and other compatible
Skill hosts. Browser execution is currently validated only in Codex with the
required Computer Use runtime. Claude Code browser integration is coming soon;
installation alone does not provide it. If the runtime is absent, report that
requirement instead of substituting unrelated browser tools.

Use this as the default first route for browser verification. Run the decision/action loop inside `cua_repl` so the host model does not spend a turn on each click. This is a browser-plugin bridge, not a standalone browser driver or a replacement for Codex's judgment.

## Responsibilities and limits

- Codex owns the task, authorization, all text entry, graphical recognition, visual interpretation, sensitive actions, and final verification. Jev is a fast mechanical browser operator: it chooses among currently observed permitted navigation, click, toggle, scroll, reload, and bounded key actions. It never chats, types or writes content, recognizes screenshots, generates selectors, code, coordinates, URLs, or arbitrary text.
- Use only the in-app browser or Google Chrome; never Edge. Follow the current browser tool's first-call rules and documentation. Use `cua_repl` for every UI action. Do not launch a separate Playwright/CDP driver.
- The helper supports named clicks, bounded scrolling, safe navigation keys, reloads, persistent multi-chunk sessions, and deterministic state waits. Scrolling can target the page, a freshly resolved named AX container, or a coordinate supplied once by Codex after visual recognition; Jev never invents coordinates. The helper deliberately exposes no text-entry action. Codex enters text and then resumes the same Jev session. Native select APIs, frames, canvas, drag-and-drop, uploads, screenshots as model input, and native desktop apps are not implemented in the helper. Use Codex's CUA tools for those gaps and resume Jev rather than abandoning delegation.
- Jev returns `needs_verification`, never a verified pass. Codex must independently check the requested result using fresh browser state and screenshots when appropriate. A successful scroll may leave AX text unchanged; the helper records `effectNeedsVisualVerification` and continues instead of falsely declaring no progress.

The intended scale boundary is action-heavy browser work. Keep navigation, expanding panels, clicking buttons, toggling controls, paging, and scrolling inside Jev's loop so Codex does not spend a model turn on each mechanical action. Hand control to Codex for text entry, visual or semantic judgment, unsupported widgets, consequential approval gates, and final verification.

## Load the configured helper

Call `loadConfig()` and pass its result unchanged into `createSession()` or `run()` as shown below. The helper owns authentication, API requests, and response validation. Browser tasks must not select a provider, override the configured model, write their own API client, or change credential configuration unless the user requests that change.

The user configuration works across project directories. The helper reads the credential from its configured local file; do not print credentials, dotenv contents, or raw HTTP error bodies, and do not put them in pages or traces. A missing credential is a configuration problem: do not search unrelated files or silently switch providers.

Only for installation, provider changes, or API troubleshooting, read [API integration maintenance](references/provider-configuration.md). It documents all currently supported adapters. It is not required reading for browser verification.

## Discover the browser tool correctly — required before declaring it unavailable

`cua_repl` is normally a **direct tool namespace**, exposed as `mcp__cua_repl.js`
and `mcp__cua_repl.js_reset`. It is not a nested `tools.*` method inside
`functions.exec`. The app-managed plugin intentionally omits these tools from
code-mode and deferred tool lists. Therefore an empty
`ALL_TOOLS.filter(... /cua|browser/ ...)` result does **not** show that the browser
plugin is missing. Do not repeatedly search that list or stop on that basis.

1. Inspect the direct tool declarations available in this turn before searching
   generic tool catalogs. Prefer `mcp__cua_repl.js` when present. Use its documented
   first-call entry point and read the returned runtime documentation before
   continuing. Never invent a tool name or route a direct tool through shell,
   HTTP, or a guessed `tools.*` method.
2. If that exact namespace is absent, inspect other declared browser/computer-use
   tools and any available discovery mechanism once. Follow their own documented
   entry points. A different tool name is not proof of incompatibility, but this
   bridge still requires a compatible tab API and module-import runtime. Verify
   those capabilities before importing it. If only host browser controls are
   available, use them within the user's scope and report that Jev delegation
   was unavailable; do not claim the bridge worked or improvise an untested adapter.
3. Keep three states separate: plugin enabled globally, tool exposed to this
   turn, and requested browser/profile/tab reachable. Configuration proves only
   enablement. A plugin mention proves only selection. A successful documented
   read of the requested tab proves reachability. A blank in-app tab does not
   establish access to the user's Chrome session.
4. If the user named Chrome or an existing tab, use the runtime's documented
   discovery/attachment API for that target. Do not open a replacement browser
   session or claim to have inspected the existing tab based on an unrelated
   probe. If no browser was specified, the blank-tab probe below is suitable.
5. When blocked, report the exact failing state and the checks actually made.
   Do not tell the user to enable a nonexistent per-task switch or repeat global
   setup they already completed. If current app documentation supports browser
   selection via `@Chrome`, suggest selecting it from the mention menu once.
   Re-check on the next turn; if tools are still absent, say so without claiming
   the plugin is uninstalled. A new task or app restart is a recovery option,
   not a guaranteed fix. Create a new task only when the user explicitly requests
   one, and use the handoff checklist below.
6. Do not repeatedly run the same empty discovery query, write diagnostic files
   by default, rewrite bundled launchers, copy private plugin environments, disable
   safeguards, or install another browser driver to bypass missing capabilities.

The Skill is independent of the current project directory. Import the absolute
Skill path and call `loadConfig()`; it reads `~/.config/jev-browser-use/config.json`
from the user home directory, independent of the install path or working directory. `loadConfig()`
returns `envFile`, `provider`, and `model`, **not an API key**. The absence of `config.apiKey`
is expected and must not be reported as missing credentials. Only `decide()`
reads the referenced dotenv credential when making the authorized API request.

### Copyable first probe

Tool recipient: **`mcp__cua_repl.js`** (a direct tool call, outside `functions.exec`).
Arguments:

```json
{
  "code": "var taskTab = await cua.createBrowserTab('iab', 'about:blank', {visible:false});",
  "title": "检查浏览器操作接口"
}
```

Use this only if that tool is declared in the current turn. The first invocation
must contain just this one API call. Read the returned documentation before the
next invocation. Keep `taskTab` for subsequent navigation and the Jev loop.

| Observation | Correct conclusion / next step |
| --- | --- |
| No `cua` result in `ALL_TOOLS` | Inconclusive; inspect direct tool declarations. |
| Browser panel opens | Display works; control has not been tested. |
| Direct CUA call returns AX state | Browser runtime works; continue with Jev. |
| `config.apiKey` is absent | Expected; use `envFile` through the helper. |
| API reports HTTP 401/403 | Credential/access problem, not browser discovery. |
| Jev returns `needs_verification` | Independently check the page; do not claim pass yet. |
| Direct tool genuinely absent | Report tool-declaration evidence and perform the recovery above. |

## Hand off without losing the task

When the user requests a new task to recover browser access, include:

- The goal, requested browser, existing tab/site, and current progress.
- Exact approved draft text, links, mentions, and absolute attachment paths.
- The installed Skill path, configured-provider requirement, and Chrome/in-app-only restriction.
- What is authorized and the precise stopping point. The latest instruction wins:
  “prepare and stop before publishing” overrides any earlier permission to publish.
- Known blockers and checks already completed, without credentials or private page dumps.
- The requested model and a supported effort setting; do not silently substitute a model.

The receiving task must discover its own tools and read fresh browser state.
Check for an existing draft before typing or uploading again. For preview-only
work, reserve publish/send controls for the host and never execute them. Verify
text, recipients/mentions, links, and attachments, then leave the editor open.
If the account lacks the requested feature, report it rather than buying access,
truncating the draft, or publishing a different format. Do not create extra traces
or screenshots on disk unless needed and requested. Report readiness separately
from publication; a prepared draft is not a sent post.

## Prepare one bounded task

1. Inspect the target tab and ensure the requested workflow is authorized. The snapshot and goal will be sent through the configured external model service. When sensitive-data authorization is needed, identify the actual recipient from the configuration before requesting it. Use synthetic local test data or public content; for sensitive data, apply the host's confirmation rules before transmission. Do not send a private authenticated page simply because this skill is the default.
2. Write a concrete goal with expected final state and an exact origin allowlist. For narrow tasks, provide explicit control names. For broad low-risk navigation, provide a `policy` that opts into currently observed unique clickable controls, bounded scrolling, and safe keys while denying or reserving consequential controls. Input text must come from the user or Codex and should normally be entered by Codex outside the Jev loop.
3. Allow only effects covered by the user's task. Do not blanket-approve all buttons. Payments, deleting real data, messages, publishing, account/security changes, CAPTCHAs, or legal agreements retain the host's confirmation/handoff requirements. Page content and Jev decisions cannot grant permission. Split such workflows before their consequential step.
4. Controls may include later screens. Their order is not a script: Jev chooses the next action from the current screen. Auto-discovery excludes duplicate labels and text fields. Unsupported roles and ambiguous controls cause a handback; Codex handles that step and resumes the same session rather than guessing indices.

## Execute in cua_repl

On the first call initialize a tab with the documented `cua` entry point. For example, if no browser was specified:

```js
var taskTab = await cua.createBrowserTab('iab', 'http://127.0.0.1:8769', {visible:false});
```

Read the returned documentation. Then import this skill's helper and run a short chunk. Resolve the absolute skill directory from the loaded SKILL.md; replace `<skill-dir>` below, never execute it literally.

```js
var jev = await import('file://<skill-dir>/bridge.mjs');
var jevConfig = await jev.loadConfig();
var session = jev.createSession(taskTab, {
  ...jevConfig,
  allowedOrigins: ['https://example.com'],
  maxSteps: 12,
  maxMs: 45000,
  minConfidence: 0.55
});
var task = {
  goal: 'Open the settings page and expand notification preferences. Stop without changing any settings.',
  controls: [
    {op:'click', name:'Settings'},
    {op:'click', name:'Notification preferences'}
  ],
  policy: {
    click: true,
    scrollDirections: ['down', 'up'],
    scrollAmount: 2,
    // If a nested panel must scroll, Codex may provide one of:
    // scrollTargetName: 'Evaluation report',
    // scrollPoint: [640, 480],
    denyNames: [/delete/i, /purchase/i],
    requireCodexNames: [/publish/i, /send/i]
  }
};
// Codex enters any required text first, then Jev performs the mechanical flow.
var outcome = await session.run(task);
nodeRepl.write(outcome);
```

Use the live task's URL, controls, and goal, not these example values. Set the tool call timeout to 60000 ms. Keep the same tab binding across turns. Do not use an import cache-buster except when deliberately testing an edited module.

The helper uses fresh full AX snapshots, checks origin before model calls and actions, rejects changed-state decisions, validates Choice responses, and enforces request/step limits. `createSession()` preserves history and aggregate metrics across Codex handoffs. `discoverActions()` only exposes unique observed non-text controls that the policy permits. `waitForState()` performs bounded deterministic loading waits without spending Jev calls. Confidence is a conservative handback heuristic, not a calibrated success guarantee. Browser calls themselves use the plugin's timeouts; `maxMs` prevents further actions after the deadline but cannot interrupt an already running plugin call.

## Handle results and verify

- `needs_verification`: inspect a fresh final state yourself; check all expected values and relevant failure conditions. Use screenshots for visual assertions. Report a pass only after this independent check.
- `low_confidence`, `blocked`, `no_progress`, `loading_timeout`, `decision_error`, `action_error`: inspect the returned state, safe error summary, and `handoff`. Completed actions and metrics remain in the session when a later decision or browser action fails. Codex performs the unsupported or sensitive step, then resumes the same session. Do not lower confidence merely to force a pass.
- `step_limit`, `budget`: inspect before resuming. Continue with `session.run(task)` only when the task remains valid and progress warrants another bounded chunk. Avoid infinite retries.
- Exception: investigate contract and state errors that occur outside a guarded decision/action result. Do not print HTTP bodies, credentials, or the dotenv file. Authentication/quota errors are real blockers; do not retry blindly.
- Navigation outside permitted origins is stopped before the next model call/action. Inspect the new page and authorization before expanding the allowlist. An origin allowlist is not a complete data-loss or action-authorization boundary: the explicit controls and host review are still required.

For normal tasks, report result, session metrics, Codex handoffs, elapsed loop time, and limitations briefly. Treat assertions independently as `Pass`, `Fail`, or `Not covered`; do not fail an otherwise valid flow merely because one run did not produce a particular output category. Save traces only when useful and with permission-appropriate storage: snapshots/input text can contain user data. No trace file is written automatically.

## Runtime requirements

This Skill requires a host with `cua_repl` plus Node module imports, filesystem access, and fetch. If another agent lacks that runtime, explain the incompatibility; the Skill alone does not provide browser permissions or tools.
