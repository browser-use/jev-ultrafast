# TypeSafe Jev & Gemini 3.8 Architecture & Operator Guide

This document is the single reference guide for TypeSafe Jev, the runtime loop, credential handling, and Gemini 3.8 Flash text model integration.

---

## 1. What is Jev?

**Jev** is a high-speed System One browser decision model developed by [TypeSafe](https://typesafe.ai).

### The Core Problem It Solves
Traditional browser agents ask an LLM to generate complex JSON, tool calls, DOM selectors, code, or coordinates for every action. This creates multiple problems:
- **High latency:** 2–5 seconds per step just to emit tokens.
- **Fragile outputs:** Model hallucinates selectors, clicks outside viewport, or writes invalid syntax.
- **Excessive cost & token waste:** Sending raw DOM text and reasoning traces over dozens of steps.

### How Jev Works: Dynamic Indexed Action Space
1. **Atomic DOM Snapshot (`snapshot.js`):** Reads visible, interactive elements on the page in a single pass and assigns sequential IDs:
   ```text
   [1] button    Change ticket type · Round trip
   [2] combobox  Where from?        · Miami
   [3] combobox  Where to?          · empty
   [4] textbox   Departure          · empty
   ...
   ```
2. **Speculative Fan-Out Request:** In **one single HTTP request** to `https://api.typesafe.ai/v1/systemone` using model `jev-latest`, Jev simultaneously answers:
   - What `operation` to perform: `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, `BLOCKED`.
   - What `target` to execute for each possible operation (`click_target`, `type_text_target`, `select_target`).
3. **Execution:** The agent consumes *only* the target matching the chosen operation. If `CLICK` was selected, `click_target` is executed via CDP. No selectors or code are ever emitted by the model.

---

## 2. Division of Labor: Jev vs. Text LLM (Gemini 3.8)

| Component | Provider | Model | Role |
| :--- | :--- | :--- | :--- |
| **Action & Routing Policy** | TypeSafe | `jev-latest` | Decides *what* to click, *where* to type, *when* to scroll, and when the goal is `DONE`. |
| **Field Text Generator** | Google GenAI | `gemini-3.8-flash` | Generates the literal text string to type into a field (activated **only** on `TYPE_TEXT`). |
| **Browser Runner** | Browser Harness / CDP | Chrome | Connects to local Chrome on `--remote-debugging-port=9333` and executes actions. |

---

## 3. Environment & Credentials

Credentials live in `.env.local` (and `.env`), ignored by git:

```env
# TypeSafe API Configuration
TYPESAFE_API_KEY=apikey_...
TYPESAFE_MODEL=jev-latest

# Gemini Text Model Configuration
TEXT_MODEL_API_KEY=AQ....
TEXT_MODEL=gemini-3.8-flash
TEXT_MODEL_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
TEXT_MODEL_REASONING=none

# Alias recognized by load_environment()
GEMINI_API_KEY=AQ....
```

### Environment Loader
[`load_environment()`](file:///c:/Users/oelhaddaji/Documents/GitHub/jev-ultrafast/jev_ultrafast/demo.py#L23-L33) checks `.env.local` first, then `.env`.
If `GEMINI_API_KEY` is present and `TEXT_MODEL_API_KEY` is not, it automatically copies the key over and configures the default Google endpoint.

---

## 4. Google GenAI (Gemini 3.8 Flash) Integration

For `TYPE_TEXT` operations, Jev delegates to Google's official `google-genai` SDK using the **Interactions API**:

```python
from google import genai

client = genai.Client(api_key=os.environ.get("TEXT_MODEL_API_KEY"))
interaction = client.interactions.create(
    model="gemini-3.8-flash",
    input=f"{TEXT_VALUE}\n\nContext:\n{json.dumps(context)}"
)
# Returns JSON: {"text": "field value to enter"}
```

- **Rate Limits & Backoff:** Free Tier on Gemini 3.8 Flash has rate limits. Automatic backoff retries (up to 4 attempts) are built into `jev_ultrafast/model.py`.
- **Reasoning Parameter:** Google's OpenAI-compatible endpoint rejects the `reasoning` parameter; it is stripped when talking to `generativelanguage.googleapis.com`.

---

## 5. Network & System Gotchas

### Local DNS Fallback for `api.typesafe.ai`
Certain corporate/local DNS configurations fail to resolve `.ai` top-level domains, producing:
`httpx.ConnectError: [Errno 11001] getaddrinfo failed`

`jev_ultrafast/model.py` includes an automatic fallback that catches `socket.gaierror` specifically for `api.typesafe.ai` and routes to its verified endpoint IPs (`44.227.31.201` / `100.20.85.248`), preserving valid SSL/TLS SNI certificates.

---

## 6. How to Run (Quick Commands)

Run commands using `python -m uv` from the repository root:

### Start the Local Web Inspector
```powershell
python -m uv run jev
```
Opens the interactive UI at **http://127.0.0.1:8766**.

### Run an Automated Task via CLI
```powershell
python -m uv run python examples/run.py --url "https://www.google.com/travel/flights?hl=en" --goal "Find one-way flights from Miami to Bahamas on November 20, 2026."
```

### Run Google Flights Verification Test
```powershell
python -m uv run python examples/flights.py
```

### Run Test Suite & Linter
```powershell
python -m uv run ruff check jev_ultrafast tests
python -m uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
```
