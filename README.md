<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

Measured with the prior Python implementation; see [Evidence and limits](#evidence-and-limits).

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](src/agent.ts)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

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
git clone https://github.com/syncretic-cc/jev-ultrafast-typescript.git
cd jev-ultrafast-typescript
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY (or USE_OPENAI=true and OPENAI_API_KEY).
deno task demo
```

Requires Deno 2.9+ and Chrome.

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

### Connect Chrome

Jev talks to Chrome through a small built-in DevTools Protocol client; there is nothing else to install. Its discovery order follows [Browser Harness](https://github.com/browser-use/browser-harness):

1. `BU_CDP_WS`: a full `ws://…/devtools/browser/…` URL.
2. `BU_CDP_URL`: an HTTP endpoint such as `http://127.0.0.1:9222`, for a dedicated automation Chrome started with `--remote-debugging-port=9222 --user-data-dir=<separate-profile>`.
3. Your everyday Chrome: open `chrome://inspect/#remote-debugging` and allow remote debugging. Jev finds the `DevToolsActivePort` file in the usual Chrome, Chromium, Edge, and Brave profile folders. Accept Chrome's *Allow remote debugging* prompt when it appears.
4. Ports 9222 and 9223 on 127.0.0.1.

Empty variables count as unset. Jev opens its own background tab and closes it at the end; that tab shares the connected Chrome profile, including its cookies and signed-in accounts.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

To use OpenAI directly, set `USE_OPENAI=true` and `OPENAI_API_KEY`. TYPE_TEXT then calls `https://api.openai.com/v1/chat/completions` with `OPENAI_MODEL` (default `gpt-6-luna`) and `OPENAI_REASONING_EFFORT` (default `none`), and ignores the `TEXT_MODEL_*` settings. TypeSafe still chooses every action.

## Use the library

```ts
import { Agent } from "./src/mod.ts";

await using agent = await Agent.create(
  "https://www.google.com/travel/flights?hl=en",
  "Find one-way flights from Zurich to London on September 20, 2026, " +
    "for one adult in economy. Stop when matching flight options are visible.",
);
for await (const state of agent.run()) {
  console.log(state.elapsed_ms, state.status);
}
```

Run with `deno run --env-file=.env --allow-net --allow-read --allow-env your_script.ts`. `await using` closes the tab when the block ends. The same policy can run a different task:

```bash
deno task example \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`deno task flights --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

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
| [agent.ts](src/agent.ts) | The complete loop and text-helper handoff |
| [snapshot.js](src/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.ts](src/browser.ts) | Tab ownership, current geometry, execution |
| [cdp.ts](src/cdp.ts) | Chrome DevTools connection and discovery |
| [model.ts](src/model.ts) | Dynamic operation/target heads and text generation |
| [questions.ts](src/questions.ts) | Model instructions |
| [demo.ts](src/demo.ts) | Local inspector |

## Evidence and limits

> [!NOTE]
> Every measurement, recording, and source hash below comes from the prior Python implementation (last Python commit [`1231850`](https://github.com/syncretic-cc/jev-ultrafast-typescript/tree/1231850)). They were not re-recorded for this TypeScript port. The raw evidence in [docs/](docs/) is unchanged, so its source hashes name Python files. The committed media ([demo.mp4](docs/demo.mp4), [demo.gif](docs/demo.gif), and [flights-result.png](docs/flights-result.png)) were rendered by the prior Python renderer and have not been re-rendered with `scripts/render_demo.ts`.

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, Python source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
deno task check
```

That runs `deno install --frozen`, `deno fmt --check`, `deno lint`, `deno check` (including `src/snapshot.js` and `src/static/app.js`), `deno test --allow-read`, and `deno publish --dry-run`.

Tests are offline and need no credentials. `deno task guards` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `deno task record artifacts/flights/<new-folder>` captures original browser timestamps; the folder must be new and under `artifacts/`, since tasks may only write there (the same applies to `deno task flights --output` and `deno task measure --output`); `deno task render <recording-folder>` renders that verified run at 1× and crops out the Google account strip (rendering needs `ffmpeg` on `PATH`). Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
