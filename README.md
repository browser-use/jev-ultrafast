<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

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

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `ESCAPE`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

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

## Try it

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

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from datetime import date, timedelta
from jev_ultrafast import Agent

day = date.today() + timedelta(days=21)  # Google Flights only offers today and later
with Agent(
    "https://www.google.com/travel/flights?hl=en",
    f"Find one-way flights from Zurich to London on {day:%B} {day.day}, {day.year}, "
    "for one adult in economy. Stop when matching flight options are visible.",
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

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Give a no-op action its turn back.** An executed action that leaves the observed state unchanged is dropped from the next decision's targets, so the runner-up gets chosen instead of repeating into the no-progress stop. The exclusion is keyed to the action table's fingerprint, not the DOM freshness guard: ids are positional, so they are only carried across a re-observation while that table is identical.
- **Bound the wandering that buys.** A run ends once `MAX_NO_PROGRESS` actions -- a fifth of `MAX_STEPS` -- have changed nothing, whether they arrive three in a row or scattered between actions that did. Waits are excluded: waiting on a loading page is not an action that went nowhere.
- **Re-read a page that hid itself.** A popup can mark the page behind it `aria-hidden`, which is advice for assistive technology and does not block input. That advice is honoured, until honouring it would leave no control at all to act on; then the hidden subtree is read again, because an empty table is a worse report than a disagreeable one. `inert` is never re-read, because `inert` really does block input.
- **Let a menu finish opening.** If the input that just ran leaves the page hidden *and* offering nothing, or leaves the control it just clicked reporting itself expanded while its own options have not reached the table, that table is last frame's page, so the reader re-reads for up to `OVERLAY_SETTLE`. The wait is tied to an input, never to a cold observation, and a table that moves ends it -- so an autocomplete that keeps its field expanded after its suggestions land pays no extra second for it. The measured window it covers is 0.6-2.2 s wide with the option row on the table at 2.4 s. Typing a city and opening the date picker never enter that state, so the budget is spent on menus and nothing else. The wait reports itself: an observation carries how many re-reads it took, the panel's element line says so, and every executed step repeats that number in the saved history, so a slow run is attributed from its own trace instead of by an experiment afterwards.
- **Say what is already open.** The table carries `aria-expanded`, and the instructions forbid re-clicking a control that reports itself expanded, because that is how its menu closes. Before the rule existed, one run clicked the ticket-type trigger 41 times without the menu's option row appearing in any of that run's 61 decision contexts; across the five verified runs that followed, that row appeared in 7, 6, 6, 15, and 89 of theirs.
- **Do not ask one table twice.** A decision can be refused before its input lands while the page re-renders text the model was never shown, so the answer is thrown away and the question is paid for again. Google Flights' date dialog did this 85 times over one byte-identical action table (1.18M input tokens, and the answer only changed on the 86th question). Re-reading the page is free, re-asking it is not: when the new table matches the one the decision was made on, that decision is offered again without a model call, for up to `MAX_STALE_REPLAY` attempts. A control that refuses for the whole cap leaves the table, so the runner-up gets a turn and the no-progress cap can end an episode that never settles.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, and nested scrolling remain outside this MVP, as does any keyboard input beyond the single `ESCAPE` key. Owned tabs share the existing Chrome profile.

On Google Flights' ticket-type menu, raw DOM tallies taken immediately before and after each snapshot: while 90 of 91 painted controls sit under an `aria-hidden` ancestor, the observation still carries 29 addressable controls and 409 characters of text; once the menu's own options finish rendering, the re-read stands down and the snapshot holds exactly those 3. Before the re-read existed, that first moment had been measured as an observation with no addressable control in it at all, and `BLOCKED` was the policy's correct answer about it. The re-read can therefore surface the page behind an overlay, which is the point and the cost. `ESCAPE`, offered on every page, returned that menu's site to 29 controls and 390 characters of text in one press.

The example's own saved traces bound that cost. Nine runs of `examples/flights.py` on 2026-09-27, same goal, same models: the first, before the re-read and the `aria-expanded` rule, took 55 steps, 35.0 s, and ended `blocked` (20.5 s of it model time). The five that followed passed all seven independent checks in 15 to 29 executed steps and **16.2 s to 79.9 s** of wall time, at $0.0045 to $0.0536 of policy tokens. The spread was the loop paying twice: one of those runs spent 107 predictions on 17 executed steps because Google's date dialog kept re-rendering text inside a click guard's scope for 43 s -- invisible in the action table the model is shown, so 85 rejections each re-sent the same question, 1.18M input tokens, $0.0494. The three runs after "Do not ask one table twice" above passed the same seven checks in 20 to 35 steps, 44.6 s to 60.7 s, and **$0.0060 to $0.0093**; two of them hit that same dialog and paid two predictions for its 11-12 s of churn. [docs/performance.md](docs/performance.md) has the before/after and the zero-call replay that located the moving field. The added seconds are page work, plus at most `OVERLAY_SETTLE` per input that opens a menu. The **7,073 ms** figure above belongs to an earlier code state and that recording's own boundary; these runs were not paired against it.

The paired arms say how much of that was the loop's own doing. Two runs on the code as shipped, then five on the widened re-read, same goal, same models, same morning: **46.7 s and 48.9 s** of wall time over 21-22 executed steps, with the ticket-type trigger clicked 10 and 11 times, against **12.0 s to 15.1 s** over 11-14 steps with it clicked **once**, at $0.0035-$0.0044 of policy tokens versus $0.0062-$0.0064. All seven independent checks passed in every run of both arms, so the shorter run is not a shorter attempt. What was being paid for is visible in the saved decision tables: of the 407 predictions those nine 2026-09-27 runs paid, **0** showed the trigger reporting itself expanded together with the menu's own option rows, and 76 were asked while the menu was open with none of it in the table -- and the cheapest move in that table is to click the trigger again, which closes it.

Dropping no-op actions trades a fast stop for a longer exploration when the goal cannot be satisfied: the runner-up is not guaranteed to be useful, so an unsatisfiable task spends up to `MAX_NO_PROGRESS` of its steps discovering that, instead of stopping at the first dead end.

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
