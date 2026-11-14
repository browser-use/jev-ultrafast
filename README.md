<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

> [!NOTE]
> **Jev Ultrafast is now ultrafast mode in [Browser Use](https://github.com/browser-use/browser-use).** Same idea, built in: the agent acts on numbered page elements. Browser Use also handles long multi-step tasks.
>
> Python: `pip install browser-use`, then `Agent(task, mode='ultrafast')` (already the default on the `bu-fast` and `bu-ultrafast` models). Hosted: the `bu-ultrafast` model in Browser Use Cloud.
>
> This repo stays up as the original Browser Use × TypeSafe experiment.

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **BU Ultrafast, Browser Use Cloud's own hosted agent, is live on every plan.** Set `model: "bu-ultrafast"` to use it.
> **[Learn more](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)** · **[Open Browser Use Cloud](https://cloud.browser-use.com?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered. `WAIT` is a 800 ms action whose duration is carried by the observation, so both backends spend the same time on the same decision.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try the original demo

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

The default backend is the original Browser Harness session. To use Ego Browser for one task, install its `ego-browser` executable and select it explicitly:

```bash
uv run --env-file .env python examples/run.py \
  --backend ego \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.' \
  --record-dir artifacts/ego-run
```

`ULTRAFAST_BROWSER_BACKEND=ego` is the equivalent environment setting. The explicit `--backend` value wins. Ego starts one task-scoped runtime, reuses it for every observation and action, and closes it when the `Agent` context exits. An Ego freshness check is a read-only probe of the decided target — page identity plus that node's trimmed guard, actionable and writable flags — so it never re-snapshots the page or rebuilds the action list; when the target is really gone the loop re-observes and decides again instead of replaying the mutation. A target Ego's snapshot gives no ref for is never offered as a choice, since choosing it could only burn a stale retry, and after a mutation the next observation waits (bounded and read-only) for the action set to hold still, so a decision is never made on a transitional frame. `metrics.json` contains wall and policy timings, Jev calls, action success, stale counts, and backend operation timings; `trace.json` omits raw model requests, usage payloads, typed values, and full page text. Startup checks report only whether credentials are configured. The V2 hardening report with its live-run evidence is in [V2_RELIABILITY_HARDENING_REPORT.md](V2_RELIABILITY_HARDENING_REPORT.md), and the Ego vs Chrome comparison is in [docs/final_backend_benchmark.md](docs/final_backend_benchmark.md).

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the original library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London for one adult in economy, "
    "departing on the first date the calendar offers. Stop once the search is "
    "committed for that route and date.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Drive it from an MCP host

`jev_ultrafast/mcp_server.py` is a stdio MCP server over the same agent. It uses
the standard library only, so it adds no dependency, and it redirects everything
the agent and its browser backend print to stderr: stdout carries protocol frames
and nothing else. It reads the ignored `.env` itself, because an MCP host scrubs
the environment it spawns children in.

| Tool | Job |
| --- | --- |
| `run_goal` | One URL and one natural-language goal; returns status, executed steps, metrics, and an independent re-read of the final page. |
| `browser_status` | Reports whether credentials and the backend are configured. Starts no browser and makes no paid call. |

```bash
python -m jev_ultrafast.mcp_server --print-tools   # the advertised surface
python -m jev_ultrafast.mcp_server --check         # configuration only
```

Register it with any host that speaks MCP over stdio. For a DSH profile:

```yaml
- insert:
    - id: mcp-jev-ultrafast
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: stdio
        serverName: jev-ultrafast
        command: /absolute/path/to/jev-ultrafast/.venv/bin/python
        args: [-m, jev_ultrafast.mcp_server]
        cwd: /absolute/path/to/jev-ultrafast
        toolCallTimeoutMs: 900000   # above run_goal's 180 s default budget
        failOnStartupError: false
```

Tools then appear as `mcp__jev-ultrafast__run_goal` and `mcp__jev-ultrafast__browser_status`. Set `JEV_MCP_TRACE_PATH` to append every protocol frame to a JSONL file; entries carry the server pid, so sessions in one append-only file stay distinguishable. `browser_harness` (Chrome) remains the default backend, and a run still stops on `DONE`, `BLOCKED`, or its time budget. `scripts/mcp_client_run.py` is a minimal client for exercising the server and recording that trace.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, action guards |
| [probe.js](jev_ultrafast/probe.js) | Read-only freshness probe: page identity plus the target's trimmed guard, actionable and writable flags |
| [timeline.py](jev_ultrafast/timeline.py) | Bounded, redacted JSONL execution timeline for the Ego loop |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |
| [mcp_server.py](jev_ultrafast/mcp_server.py) | Stdio MCP server over the same loop |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. That run's own independent check verified the one-way setting, Zürich, London, its dated departure (September 20, 2026), and visible flight options. The shipped example derives the same check differently so it stays runnable: `examples/flights.py` asks for today + 21 days, reads the committed itinerary out of Google's `tfs` payload (the departure date, and the trip type as protobuf field 19), and reports the visible flights without requiring a particular date. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
