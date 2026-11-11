"""One-shot TEXT_MODEL compatibility probe (run from the repo root).

Sends the field context through the real field_text() path, built by field_context() itself,
so the exact request body (response_format/reasoning fields) is tested rather than a hand-made
copy of it. Makes one paid call to the text helper and never prints credentials.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import field_context, field_text

GOAL = "Find one-way flights from Zurich to London on the 20th of next month."
ACTION = {"label": "Departure", "role": "combobox", "value": ""}
PAGE = {"title": "Google Flights", "text": "One-way\nDeparture\nArrival\nWhere from?"}

CONTEXT = field_context(GOAL, ACTION, PAGE, [])


def main():
    load_environment()
    if not os.environ.get("TEXT_MODEL_API_KEY"):
        print("SKIP: TEXT_MODEL_API_KEY is empty. Fill it in .env first.")
        return 2
    try:
        value, meta = field_text(CONTEXT)
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {str(error)[:300]}")
        return 1
    print(f"OK  model={meta['model']} latency={meta['latency_ms']}ms")
    print(f"    typed value: {value[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
