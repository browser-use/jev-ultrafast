# Jev Ultrafast — Agent Directives

> **MANDATORY CORE DIRECTIVE:**
> **YOU WILL ONLY USE JEV FOR BROWSING IN CHROME. NOTHING ELSE. EXACTLY JEV FOR BROWSING.**
> All navigation, action selection, interaction decisions, and evaluation must be driven strictly through TypeSafe Jev. Do not use generic LLMs, fallback models, or alternative automation tools for browsing decisions.

---

## 1. Credentials & Environment
- **Tokens in `.env` / `.env.local`:** All API credentials (`TYPESAFE_API_KEY`, etc.) are kept server-side in `.env` and `.env.local`, loaded via `load_environment()`.
- **Never expose or leak credentials.** Keep `.env` and `.env.local` git-ignored at all times.
- Offline tests must never make paid API calls.

---

## 2. Chrome Browsing with Jev
- **The Core Loop:** `page observation` → `indexed element table` → `Jev System 1 operation + target choice` → `CDP execution`.
- **Chrome Automation:** Chrome runs through CDP (remote debugging port 9333 / Browser Harness) directly controlled by the Jev agent loop.
- **Single Natural-Language Goal:** The input is one goal. Do not inject hardcoded site plans, scripts, or hardcoded form values.
- **TypeSafe Jev Decision Engine:** TypeSafe Jev (`TYPESAFE_API_KEY`, model `jev-latest`) drives all action selections in a single network request using speculative heads (`operation`, `click_target`, `type_text_target`, `select_target`).
- **Target Constraint:** Targets must map to real observed elements and supported operations (`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, `BLOCKED`). Never allow model-generated selectors or arbitrary JavaScript code.
- **Field Text Generation:** When `TYPE_TEXT` is selected, text generation helper produces the value. A cached retry value is reused only if the entire field context is identical.
- **Mutations & Freshness:** Never blindly retry a browser mutation. Re-check page freshness before each interaction. Log actions before observing post-action state.

---

## 3. Decision Standard (`JEV_SYSTEM_SPEC.md`)
- Strictly follow `JEV_SYSTEM_SPEC.md` for all decisions, evaluations, classifications, and rankings.
- Use Jev System 1 primitives only:
  - **`choice`**: Categorical classification and action selection with calibrated probability vectors.
  - **`score`**: Quantitative metrics on bounded scales.
  - **`null`**: Boolean probability flags (0.0 to 1.0).
- Never replace Jev with generic conversational LLMs for decision logic.

---

## 4. Verification & Constraints
- Independent verification: A `DONE` choice from the model is not proof of success; always verify the resulting page state independently.
- Do not commit or push to git unless explicitly requested by the user.

---

## 5. Verification Commands
```bash
python -m uv run ruff check jev_ultrafast tests
python -m uv run pytest
node --check jev_ultrafast/static/app.js
python -m uv build
```
