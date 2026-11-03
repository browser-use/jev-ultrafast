---
description: Run one Jev Ultrafast browser task from a natural-language goal against the local automation Chrome
argument-hint: <goal> [starting url]
---

Run the [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) browser agent for this goal:

> $ARGUMENTS

Steps:

1. Locate the project checkout: use `$JEV_ULTRAFAST_HOME` if set, else `~/jev-ultrafast`, else ask
   the user for the path. Never invent a path; never clone into a directory the user did not name.
2. Check prerequisites and report only *whether* values are set, never their contents:
   `uv` on PATH, and a non-empty `TYPESAFE_API_KEY` and `TEXT_MODEL_API_KEY` in the project `.env`.
   If either key is missing, say what to fill in and stop — do not substitute another key.
3. Start the browser (idempotent): `./scripts/start-automation-chrome.sh` (or `.cmd` on Windows).
   Skip this if the project `.env` sets `BU_CDP_URL` to a browser the user already runs.
4. Run the task from the project directory:

   ```bash
   uv run --env-file .env python examples/run.py --url "<starting url>" --goal "<goal>"
   ```

5. Report what the run printed (final URL, elapsed time, step count). State plainly that `DONE` is
   the model's judgement, not proof: verify the requested outcome on the page before claiming the
   task succeeded.

Never ask the user to paste a key into the chat, never echo a key, and never write one outside the
project's gitignored `.env`.
