import json
import time
from pathlib import Path
from jev_ultrafast.demo import load_environment
from jev_ultrafast import Agent

load_environment()

URL = "https://www.google.com/travel/flights?hl=en"
GOAL = (
    "Find one-way flights from Miami to Bahamas on November 20, 2026, for one adult in economy. "
    "Stop when matching flight options are visible. Do not select or book a flight."
)

print(f"Goal: {GOAL}")
print(f"URL: {URL}")
print("Connecting to Chrome and TypeSafe / Gemini...")

with Agent(URL, GOAL) as agent:
    print(f"Initial Page: {agent.state['page']['title']} ({agent.state['page']['url']})")
    for state in agent.run():
        last = state["history"][-1] if state["history"] else {}
        dec = state["decisions"][-1] if state["decisions"] else {}
        elapsed = state["elapsed_ms"]
        status = state["status"]
        op_str = str(dec.get("operation") or "")
        target_str = str(dec.get("target") or "")
        action_str = str(last.get("action") or "")
        text_str = str(last.get("text") or "")
        print(f"[{elapsed:>5} ms] status={status:<8} op={op_str:<10} target={target_str:<3} action='{action_str}' text='{text_str}'", flush=True)

    final_state = agent.snapshot()
    print("\n--- Completed ---")
    print(f"Final status: {final_state['status']}")
    print(f"Total actions: {len(final_state['history'])}")
    print(f"Total time: {final_state['elapsed_ms']} ms")
    print(f"Final URL: {final_state['page']['url']}")
