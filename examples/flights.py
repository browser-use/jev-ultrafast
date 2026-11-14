"""Live Google Flights search. Calls TypeSafe; never selects or books a flight."""

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jev_ultrafast import Agent
from jev_ultrafast.flight_date import flight_departure

URL = "https://www.google.com/travel/flights?hl=en"
DEPARTURE = flight_departure()
GOALS = DEPARTURE.goal_text


def verify(page):
    """Independent checks on the resulting page, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    encoded = parse_qs(parsed.query).get("tfs", [""])[0]
    try:
        date_in_url = DEPARTURE.iso.encode() in base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError:
        date_in_url = False
    actions = page["actions"]
    values = {a["label"].strip(): a.get("value") for a in actions}
    flights = [a["label"] for a in actions if "Select flight" in a["label"]]
    checks = {
        "search_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/search",
        "one_way": values.get("Change ticket type. One way") == "One way",
        "origin": values.get("Where from?") == "Zürich",
        "destination": values.get("Where to?") == "London",
        "date": values.get("Departure") == DEPARTURE.short_weekday,
        "year": date_in_url or f"departing {DEPARTURE.iso}" in page["text"],
        "results": bool(flights) and all(DEPARTURE.long_weekday in f for f in flights),
    }
    return {"passed": all(checks.values()), "checks": checks, "visible_flights": flights}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/flights/latest")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    agent = Agent(URL, GOALS)
    try:
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
    finally:
        state = agent.snapshot()
        state["verification"] = verify(state["page"])
        (folder / "state.json").write_text(json.dumps(state, indent=2))
        (folder / "session.json").write_text(
            json.dumps({"target": agent.browser.target, "session": agent.browser.session})
        )
        if not args.keep_open:
            agent.close()
    print(json.dumps(state["verification"], indent=2))
    if not state["verification"]["passed"]:
        raise SystemExit("Final page did not satisfy the route/date checks")


if __name__ == "__main__":
    main()
