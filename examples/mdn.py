"""Live MDN Web docs navigation. Calls TypeSafe; never types or submits anything."""

import argparse
import json
from pathlib import Path

from jev_ultrafast import Agent

URL = "https://developer.mozilla.org/en-US/docs/Web"
GOALS = (
    "Open the HTML topic, then its CSS topic link, then its JavaScript topic link. "
    "Stop on JavaScript. Only click public documentation links; do not type or submit anything."
)
# Each waypoint is verified from observed URLs, not from the model's DONE choice.
PATH = (
    "https://developer.mozilla.org/en-US/docs/Web/HTML",
    "https://developer.mozilla.org/en-US/docs/Web/CSS",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
)


def verify(page, visited):
    """Independent checks on observed URLs, not the model's DONE answer."""
    first_seen = {}
    for index, url in enumerate(visited):
        for topic in PATH:
            if topic in url and topic not in first_seen:
                first_seen[topic] = index
    checks = {
        "each_topic_was_visited": all(topic in first_seen for topic in PATH),
        "topics_were_visited_in_order": all(
            first_seen.get(a, 10**9) < first_seen.get(b, -1) for a, b in zip(PATH, PATH[1:])
        ),
        "final_page": page["url"].startswith(PATH[-1]),
    }
    return {"passed": all(checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/mdn/latest")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    agent = Agent(URL, GOALS)
    visited = []
    try:
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
            if state["page"]["url"] not in visited:
                visited.append(state["page"]["url"])
    finally:
        state = agent.snapshot()
        state["verification"] = verify(state["page"], visited)
        (folder / "state.json").write_text(json.dumps(state, indent=2))
        if not args.keep_open:
            agent.close()
    print(json.dumps(state["verification"], indent=2))
    if not state["verification"]["passed"]:
        raise SystemExit("Run did not visit every topic in order and stop on JavaScript")


if __name__ == "__main__":
    main()