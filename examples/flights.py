"""Live Google Flights search. Calls TypeSafe; never selects or books a flight."""

import argparse
import base64
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jev_ultrafast import Agent

URL = "https://www.google.com/travel/flights?hl=en"
# A date far enough out to be bookable whenever this example is run.
DEPARTURE = date.today() + timedelta(days=21)
GOALS = (
    f"Find one-way flights from Zurich to London on {DEPARTURE:%B} {DEPARTURE.day}, "
    f"{DEPARTURE:%Y}, for one adult in economy. "
    "Stop when matching flight options are visible. Do not select or book a flight."
)


def verify(page):
    """Independent checks on the resulting page, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    encoded = parse_qs(parsed.query).get("tfs", [""])[0]
    try:
        committed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError):
        committed = b""
    # Google Flights publishes the itinerary it committed to inside tfs: the
    # departure date, and the trip type as protobuf field 19 (2 is one way).
    marker = committed.find(b"\x98\x01")
    one_way = marker != -1 and marker + 2 < len(committed) and committed[marker + 2] == 2
    flights = [a["label"] for a in page["actions"] if "Select flight" in a["label"]]
    checks = {
        "flights_site": parsed.hostname == "www.google.com"
        and parsed.path.startswith("/travel/flights"),
        "one_way": one_way,
        "origin": "Zürich" in page["text"],
        "destination": "London" in page["text"],
        "date": DEPARTURE.isoformat().encode() in committed,
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
