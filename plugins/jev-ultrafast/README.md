# jev-ultrafast (plugin)

Drives a real Chrome browser from a single natural-language goal.

[Jev Ultrafast](https://github.com/browser-use/jev-ultrafast) is a browser agent with a dynamic,
indexed action space: per decision cycle TypeSafe's Jev returns an operation
(`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, `BLOCKED`) and an
operation-specific target element, in one request. A small OpenAI-compatible model writes text only
when the operation is `TYPE_TEXT`.

## What this plugin adds

- `skills/jev-ultrafast/` — setup and run guidance for the agent (prerequisites, commands, caveats).
- `commands/jev.md` — `/jev-ultrafast:jev <goal> [starting url]` to run one task.
- `scripts/start-automation-chrome.sh` / `.cmd` — start (or reuse) a dedicated automation Chrome
  with a separate profile, so the agent never needs remote debugging enabled on your daily browser.

## Install

Add this repository as a marketplace, then install the plugin:

- ZCode / Claude Code: **Settings → Plugin Management → Discover → `+`** and point it at the local
  checkout (a directory is accepted), then install **jev-ultrafast**; or add
  `https://github.com/browser-use/jev-ultrafast` once this lands upstream.

## Keys are never stored here

The plugin holds no credentials. `TYPESAFE_API_KEY` and `TEXT_MODEL_API_KEY` live in the
`jev-ultrafast` project's own `.env`, which upstream already gitignores. Do not add keys to this
plugin directory, to `plugin.json`, or to any file that is committed.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Google Chrome (or Chromium) and a local checkout of the `jev-ultrafast` project
- API credentials: TypeSafe Jev (`https://docs.typesafe.ai`), or an OpenRouter key for both the
  decisions model (`~typesafe/jev-latest` via `/api/alpha/decisions`) and the text model

See the skill for the full command list, browser options, and caveats.
