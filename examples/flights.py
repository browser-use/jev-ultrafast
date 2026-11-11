"""Live Google Flights search. Calls TypeSafe; never selects or books a flight."""

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jev_ultrafast import Agent

URL = "https://www.google.com/travel/flights?hl=en"
GOALS = (
    "Find round-trip flights from Senai International Airport (JHB) in Johor Bahru to Kota Kinabalu (BKI) in Sabah, "
    "departing October 16, 2026 and returning October 21, 2026, for one adult in economy. "
    "Stop when matching flight options are visible. Do not select or book a flight."
)


def verify(page):
    """Independent checks on the resulting page, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    encoded = parse_qs(parsed.query).get("tfs", [""])[0]
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        dates_in_url = b"2026-10-16" in decoded and b"2026-10-21" in decoded
    except ValueError:
        dates_in_url = False
    actions = page["actions"]

    def value(prefix):
        # Result-page labels carry the value too ("Where from? Johor Bahru JHB"), so match by prefix.
        return next((a.get("value") for a in actions if a["label"].strip().startswith(prefix)), None)

    flights = [a["label"] for a in actions if "Select flight" in a["label"]]
    checks = {
        "search_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/search",
        "round_trip": value("Change ticket type") == "Round trip",
        "origin": value("Where from?") == "Johor Bahru",
        "destination": value("Where to?") == "Kota Kinabalu",
        "departure": value("Departure") == "Fri, Oct 16",
        "return": value("Return") == "Wed, Oct 21",
        "year": dates_in_url or ("2026-10-16" in page["text"] and "2026-10-21" in page["text"]),
        "results": bool(flights) and all("Friday, October 16" in f for f in flights),
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
