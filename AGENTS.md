# Jev Ultrafast

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- TypeSafe chooses an operation and operation-specific target heads in one request. Consume only the selected operation's target.
- The policy backend is pluggable: Jev when TYPESAFE_API_KEY is set, otherwise any configured OpenAI-compatible or Anthropic provider (providers.py, docs/providers.md). Both backends obey the same contracts: one request per decision cycle, targets limited to the observed action space, no model-generated selectors.
- An optional planner (PLANNER_*) decomposes the mission into a checklist; the plan is guidance text only, a DONE advances one step, and replans are bounded. Without PLANNER_* the loop stays single-goal.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text LLM. Cache a stale retry's value only while its entire helper input is identical.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep examples, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check jev_ultrafast/static/app.js, uv build.
