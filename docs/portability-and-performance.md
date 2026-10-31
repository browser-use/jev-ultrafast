# Portability & Performance Audit

This document records the issues found in the `jev-ultrafast` codebase, the fixes applied, what each change impacted, and the solution developed. Verification commands and results are listed at the end.

The guiding loop was: **page → indexed elements → operation + target → execution**. Each fix kept that loop intact — the TypeSafe decision contract, one model round trip per cycle, and code-owned execution are unchanged.

## Issues fixed at a glance

| # | File(s) | Issue | Fix | Type |
| --- | --- | --- | --- | --- |
| 1 | `model.py` | Import-time `httpx.Client(http2=True)` crashed without `h2`; leaked connections | Lazy thread-safe client with HTTP/1.1 fallback, `atexit` close, `MODEL_TIMEOUT` | Portability |
| 2 | `model.py` | Unconfigured non-DeepSeek endpoints got `reasoning` params they may reject | Send reasoning params only when configured | Portability |
| 3 | `model.py` | Non-JSON `200` bodies raised unhandled `ValueError` | Guarded decode with a clear provider/status error | Robustness |
| 4 | `pyproject.toml`, `uv.lock` | `requires-python >=3.12` needlessly narrow | Lowered to `>=3.11` | Portability |
| 5 | `scripts/render_demo.py`, `scripts/render_fixture.py`, `scripts/fonts.py` | Hardcoded macOS font paths | Cross-platform font resolver with overrides | Portability |
| 6 | `agent.py` | `action_space` recomputed per snapshot/predict/act | Memoized per page fingerprint, passed into `choose` | Performance |
| 7 | `agent.py` | O(n) `next()` action lookup by id | O(1) `id → action` dict built once per page | Performance |
| 8 | `model.py` | Per-action `int()` parse + list re-lookup in `action_space` | Store element dict directly in the node index | Performance |
| 9 | `demo.py` | Static files read + token-injected on every request | Cache built once at startup | Performance |
| 10 | `snapshot.js`, `browser.py` | `fresh()` scanned layout-heavy guards it never uses | `__jevFastSkipGuards` flag skips guard loop on marker path | Performance |
| 11 | `model.py` | TypeSafe endpoint was hardcoded, blocking alternate inference backends | `TYPESAFE_BASE_URL` env override (default: `https://api.typesafe.ai/v1`) | Portability |
| 12 | `model.py`, `demo.py` | Text-helper defaults disagreed with `.env.example` (DeepSeek vs OpenRouter) | Defaults now match `.env.example`; reasoning params only sent when configured | Portability |
| 13 | `demo.py` | Static assets and `.env` read with the platform locale encoding (breaks UTF-8 on Windows) | Explicit `utf-8` reads; `.env` values stripped of quotes | Portability |
| 14 | `agent.py` | Empty first observation predicted `BLOCKED` instead of waiting for controls to paint | Re-observe (up to 3×, 200 ms apart) when the action space is empty | Robustness |
| 15 | `browser.py` | Fully backgrounded owned tabs can stop painting modal menus (Windows) | `TYPESAFE_FOREGROUND=1` creates the owned tab in the foreground | Portability |
| 16 | `README.md` | Model-call budget (`MAX_STEPS × 2`) was undocumented | Documented; two tests added (`test_model_call_budget_blocks_after_max_steps_twice`, `test_empty_first_observation_reobserves_before_choosing`) | Documentation |
| 17 | `README.md` | Clone URL pointed at the upstream repo, not the fork | Points to `github.com/atharvaHJoshi/jev-ultrafast`; provider/key docs added | Documentation |
| 18 | `model.py` | `ALL_PROXY=socks5://…` crashed the client at construction without `socksio` | Client built with `trust_env=False`; SOCKS proxies ignored, explicit `TYPESAFE_HTTP_PROXY` wins | Portability |
| 19 | `browser.py`, `README.md` | Browser Harness attached to the user's everyday Chrome with no warning | `BU_CDP_WS` documented; a warning prints when the attached browser already has personal tabs | Documentation |
| 20 | `browser.py` | Foreground tab env only set `background=False`; focus emulation still did not restore paint priority on Windows | `Page.bringToFront` is now also called when foreground is requested (`TYPESAFE_FOREGROUND` or `JEV_FOREGROUND`) | Robustness |
| 21 | `browser.py` | Post-input wait gave combobox-style controls only 50 ms and only for `fill`, so clicked menus stayed occluded | Combobox `click` now gets the same 300 ms option-readiness window as `fill` | Robustness |
| 22 | `snapshot.js` | Element table missed clickable non-native rows (bare `<li>`/`<div>`/`<span>`), so suggestion lists were unreachable | Bounded second pass for pointer-cursor rows in positioned layers or pointer-cursor sibling lists; innermost only, capped | Robustness |
| 23 | `browser.py`, `agent.py` | A slow daemon's screenshot timeout stalled the run even though screenshots are optional | Screenshot failure is non-fatal (`observe` returns `screenshot=None`; recording skips missing frames) | Robustness |

---

## Found issues, solutions, and impact

### 1. The package failed to import on systems without the optional `h2` extra

**Issue** — `model.py` created `httpx.Client(http2=True)` at module import time. HTTP/2 support requires the optional `h2` package, which does not come with the base `httpx` install. On a machine without it, the whole package crashed on `import jev_ultrafast`. The client was also built unconditionally, even for runs that never call a model, and was never closed (a connection leak for the process lifetime).

**Solution** — Replaced the module-level client with a lazy, thread-safe `_http()` factory:
- The shared client is built on the first real model call, not at import time.
- It tries HTTP/2 first and falls back to HTTP/1.1 when `h2` is missing (`ImportError`).
- Timeout is configurable via `MODEL_TIMEOUT` (default 25 s).
- The client is closed on interpreter exit via `atexit`.

**Impact** — Import becomes portable to any `httpx` installation; no connection is opened for offline runs; the leak is closed.

---

### 2. Non-DeepSeek endpoints received a reasoning parameter they may reject

**Issue** — `field_text` sent `{"reasoning": {"effort": "low"}}` to every non-DeepSeek helper endpoint unless `TEXT_MODEL_REASONING=none`. Strict OpenAI-compatible providers (Gemini, GLM, and some proxies) reject unknown parameters, so the text helper could fail on otherwise valid setups.

**Solution** — Reasoning parameters are now sent only when explicitly configured:
- `TEXT_MODEL_REASONING=none` → `{"reasoning": {"enabled": false}}`
- `TEXT_MODEL_REASONING=low|medium|high` → `{"reasoning": {"effort": <mode>}}`
- DeepSeek base URL with no setting → `{"thinking": {"type": "disabled"}}`
- Anything else → no reasoning parameter is sent at all.

**Impact** — The shipped demo behavior is unchanged (`.env.example` sets `none`; README claims stay consistent). Arbitrary OpenAI-compatible providers now get clean minimum requests and work out of the box.

---

### 3. Non-JSON model responses surfaced as an uncaught exception

**Issue** — `post_json` only handled `httpx.HTTPError` and HTTP error statuses. A `200 OK` response with a non-JSON body raised an unhandled `ValueError` from `response.json()`.

**Solution** — The JSON decode is now guarded; the error names the provider host and the HTTP status.

**Impact** — Clear, actionable failures instead of a bare traceback, still with **no action executed** on any failure.

---

### 4. Unnecessarily narrow Python requirement

**Issue** — `pyproject.toml` required Python `>=3.12`, but no code uses 3.12-only features and `browser-harness` supports `>=3.11`.

**Solution** — Lowered `requires-python` to `>=3.11` in `pyproject.toml` and the matching line in `uv.lock`.

**Impact** — Installs cleanly on Python 3.11; no dependency resolution changes.

---

### 5. Render scripts crashed outside macOS

**Issue** — `scripts/render_demo.py` and `scripts/render_fixture.py` hardcoded macOS-only font paths (`/System/Library/Fonts/Supplemental/Arial.ttf`, `/System/Library/Fonts/Menlo.ttc`). Rendering the recorded footage failed on Linux and Windows.

**Solution** — New `scripts/fonts.py` resolves the `sans`, `sans_bold`, and `mono` slots across common macOS, Linux, and Windows font locations, with `FONT_SANS` / `FONT_MONO` / `FONT_DIR` overrides. Both render scripts use it.

**Impact** — Deterministic footage rendering on any OS; a clear error message tells the user what to install or set if no usable font exists.

---

### 6. The action space was recomputed on every snapshot, predict, and act

**Issue** — `Agent.command` recomputed `action_space(page["actions"])` inside `snapshot()` and again during `predict` and `act`, even when the page had not changed. That is one O(n) pass over the element table per call, several times per decision cycle.

**Solution** — A memoized `Agent._space(page)` returns `(elements, targets, controls, by_id)` keyed by the page fingerprint. Only a changed observation recomputes; every later `snapshot`, `predict`, and `act` reuses the cached tables. The precomputed space is passed into `choose()`.

**Impact** — One action-space computation per observation instead of per call; smaller, reused tables instead of rebuilt ones.

---

### 7. Element lookup by action id was O(n)

**Issue** — `act` scanned the whole action list with `next(a for a in actions if a["id"] == selected)` to resolve the choice.

**Solution** — `_space()` now also builds an `id → action` dict once per page; `act` looks the choice up in O(1).

**Impact** — Constant-time resolution of the selected action during execution.

---

### 8. Element indexing inside `action_space` did redundant work

**Issue** — `action_space` stored a string index per node and then found the element with `elements[int(index) - 1]` on every action, including an `int()` parse and a list re-lookup.

**Solution** — The node index now stores the element dict directly; each action hits the index once.

**Impact** — One dict lookup per action instead of a parse plus a scan.

---

### 9. The demo server re-read and rewrote static files on every request

**Issue** — `demo.py` read `index.html`, `app.js`, `style.css`, and `fixture.html` from disk and injected the per-run `__TOKEN__` on every GET, even though the files never change after startup.

**Solution** — A `STATIC` route map plus an `_ASSETS` memo builds each file (with token injected) once and serves the cached bytes afterward.

**Impact** — No disk reads or string replacement in the request path; identical responses.

---

### 10. The freshness check scanned layout-heavy guards it never uses

**Issue** — The fast marker path (`fresh()` on every `predict`, and on every non-click `act`) evaluated the entire `snapshot.js`, including the per-element `innerText` guard scan, which forces layout on each node. The marker itself never includes guards, so that work was wasted in the hot path.

**Solution** — `snapshot.js` now honors a `__jevFastSkipGuards` flag: `MARKER` in `browser.py` sets it before evaluation and the guard loop is skipped. The full guard scan still runs on `observe()`, where guards are stored and used by the click/select freshness check, and the flag is reset within the snapshot body so later full reads are unaffected.

**Impact** — Cheaper freshness checks on the decision hot path; click/select guard semantics unchanged.

---

## Verification

All checks pass (run with the project-backed tooling; `uv` was not installed in the working environment, so equivalent commands ran in a venv):

| Check | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | All checks passed |
| Tests | `pytest` | 39 passed |
| JS syntax | `node --check jev_ultrafast/static/app.js` | OK |
| JS syntax | `node --check jev_ultrafast/snapshot.js` | OK |
| Package | `python -m build --wheel` | Built `jev_ultrafast-0.1.0-py3-none-any.whl` |
| Demo server | start + GET `/`, `/app.js`, `/api/state`, unknown path | 200 / 200 / 200 / 404 |

All 39 tests pass — the public behavior (choice contract, retry/cache rules, `DONE` verification, execution-first logging) is preserved.

## Design boundary (issue #26)

This project intentionally draws the line **between observed facts and the decision itself**:

1. **The Runtime proves and limits facts.** It observes the page atomically, assigns stable element indices, filters to supported visible controls, builds operation-specific target sets, checks freshness and occlusion before executing, and logs execution before observing the result. These are mechanically checkable properties, so they belong in code, not in a model.
2. **The provider chooses *within* that grounded space.** TypeSafe picks an operation and an operation-specific target from observed elements only. Code converts that choice into the click/type/select — model output never becomes selectors, coordinates, or scripts.
3. **The Runtime does not encode task judgement.** Schedules, forms, or per-site rules never enter the loop. The goal decides; the policy supplies plain next-step rules; `DONE` is verified independently (a `DONE` choice is not proof of success).

On the two boundary questions raised in #26:

- **"No useful action" vs "option not exposed"** — the exposed table is exactly the supported action space, and the two live together: an empty or collapsed table is rare and now handled by re-observing (see 14 above). The Runtime's job is to make the table *as complete and as small as it honestly can* (the bounded non-native-clickable pass is exactly that, see 22) and to say `BLOCKED` only when no supported operation can progress. Because the loop re-observes before choosing, a transiently hidden control does not become a fake `BLOCKED`.
- **Where the line sits** — facts that a browser can answer are hard constraints in the Runtime; which available action advances the goal stays judgment for the decision provider. This keeps the Runtime provider-agnostic: swap TypeSafe for another model and the same observed, grounded action space is served.

## What was intentionally not changed

- The TypeSafe decision contract: operation + target heads in **one** network round trip.
- Code-owned execution: model output still never becomes selectors, coordinates, shell commands, or executable JavaScript.
- Execution-first logging and independent outcome verification — a `DONE` choice is not proof of success.
- Text-helper retry caching: a stale retry reuses a value only when the entire helper input is identical.