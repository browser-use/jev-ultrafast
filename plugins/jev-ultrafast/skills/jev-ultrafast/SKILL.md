---
name: jev-ultrafast
description: Drive a real Chrome browser from one natural-language goal using Jev Ultrafast (Browser Use x TypeSafe's Jev "System One" policy). Use when the user asks to run jev / jev-ultrafast, to automate a browser task such as searching flights, filling a form, or clicking through a page with operation+target decisions, or to set up, configure, or debug the jev-ultrafast project.
---

# Jev Ultrafast

A browser agent with a dynamic, indexed action space. Per decision cycle, TypeSafe's Jev returns an
operation and an operation-specific target element in one request; a small OpenAI-compatible model
writes text only when the operation is `TYPE_TEXT`. No screenshots are consumed by the default loop.

Operations: `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, `BLOCKED`.

## Prerequisites

1. A checkout of the project (`$JEV_ULTRAFAST_HOME`, commonly `~/jev-ultrafast`), with `uv sync` run
   once. Python >= 3.12 and [uv](https://docs.astral.sh/uv/) must be on PATH.
2. Credentials in the project's `.env` (upstream `.gitignore` covers it):
   - `TYPESAFE_API_KEY` — the Jev decision model.
   - `TEXT_MODEL_API_KEY` — an OpenAI-compatible text model, used only by `TYPE_TEXT`.

   Report only whether these are set; never echo, log, or commit their values.
3. A browser, see below.

## Serving Jev without a TypeSafe account

The decision endpoint is configurable, so Jev can be served through OpenRouter with one key:

```dotenv
TYPESAFE_API_KEY=<your OpenRouter key>
TYPESAFE_ENDPOINT=https://openrouter.ai/api/alpha/decisions
TYPESAFE_MODEL=~typesafe/jev-latest
```

The request and response shape is identical to TypeSafe's own `/v1/systemone`, so the same policy
code runs on either. Notes: `~typesafe/...` is an organization-scoped model that does not appear in
`/api/v1/models`; the `/api/alpha/` path is an alpha endpoint that can change; a decisions model
cannot be called through `/chat/completions`. To go back to TypeSafe directly, set
`TYPESAFE_ENDPOINT=https://api.typesafe.ai/v1/systemone` and `TYPESAFE_MODEL=jev-latest` with a key
from `https://console.typesafe.ai/settings/keys`.

## Running tasks

```bash
cd "$JEV_ULTRAFAST_HOME"

# browser: start (or reuse) the dedicated automation Chrome — idempotent
./scripts/start-automation-chrome.sh        # Windows: scripts\start-automation-chrome.cmd

# one-shot CLI task (repeat --goal for an ordered list)
uv run --env-file .env python examples/run.py \
  --url "https://en.wikipedia.org/wiki/Main_Page" \
  --goal "Find and open the article about Godel's incompleteness theorems."

# local inspector UI on http://127.0.0.1:8766 (reads .env from the current directory)
uv run jev

# offline checks: no paid API calls
uv run pytest -q
uv run browser-harness --doctor
```

Library use:

```python
from jev_ultrafast import Agent

with Agent("https://www.google.com/travel/flights?hl=en",
           "Find one-way flights from Zurich to London on September 20, 2026, "
           "for one adult in economy. Stop when matching flight options are visible.") as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

## Browser options

- **Dedicated automation Chrome (recommended).** `scripts/start-automation-chrome.*` launches Chrome
  with `--remote-debugging-port` and its own `--user-data-dir`, so nothing is enabled on the user's
  daily profile. Point the project at it with `BU_CDP_URL=http://127.0.0.1:9222` in `.env`.
- **Your own Chrome profile.** Leave `BU_CDP_URL` unset and enable
  `chrome://inspect/#remote-debugging` in Chrome. Only a human can tick that box, and Chrome shows a
  per-connection "Allow remote debugging?" prompt afterwards.

## Caveats

- Real money per run: each decision is one paid request (roughly $0.0002 with Jev on OpenRouter),
  plus a small text-model call per `TYPE_TEXT`. Say so before launching long tasks.
- `DONE` is the model's judgement, not proof of success — verify the requested outcome on the page.
- Never retry a browser mutation; `examples/flights.py` shows the independent-verification pattern.
- Keep `.env` at LF line endings: a CRLF file leaves a trailing `\r` in values.
- In the observed snapshot the element table lives under `actions`, not `elements`.
- The browser harness allows ~5 s per IPC request, so screenshot capture can time out while the
  automation window is occluded; the default agent loop does not use screenshots.
- Outside the MVP: shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling.
