"""A read-only observation run: agent search, code-owned scrolling, candidate cards for review.

uv run --env-file .env python examples/observe.py \
  --url https://catalog.example.com/ \
  --allow catalog.example.com \
  --goal 'Search for "classic widget 120 x 240" and stop when result listings are visible.
          Read only: no sign-in, cart, enquiry, or contact actions.'
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from jev_ultrafast import Agent
from jev_ultrafast.browser import StalePage

# Read-only: visible anchors with their nearest listing-like surrounding text. No page mutation.
EXTRACT = """(() => {
  const cards = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const raw = a.getAttribute('href');
    if (!raw || raw.startsWith('javascript:')) continue;
    const r = a.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom <= 0 || r.top >= innerHeight) continue;
    const scope = a.closest('article, li, tr, [role="listitem"]') || a.parentElement;
    let text = (scope?.innerText || '').trim();
    if (text.length > 4000) text = a.innerText.trim();
    if (text.length < 20) continue;
    cards.push({ href: a.href, text: text.slice(0, 1200) });
  }
  return cards;
})()"""

PRICE = re.compile(
    r"(?:USD|EUR|GBP|CHF|AED|SAR|JPY|INR|AUD|CAD|[$£€¥])\s?\d[\d.,]*"
    r"|\d[\d.,]*\s?(?:USD|EUR|GBP|CHF|AED|SAR|JPY|INR|AUD|CAD)\b"
)

# One executed control with a label like these fails the run's read-only check. A deny list of
# common commerce/contact labels, not an exhaustive one; an unrecognized label is a reviewer question.
PROHIBITED = re.compile(
    r"sign (?:in|up)|log ?in|log ?on|create (?:an )?account|register|"
    r"add to cart|basket|cart\b|checkout|place order|order now|buy now|book now|"
    r"enquir|inquir|(?:request|get) (?:a )?quote|contact|chat\b|subscribe|newsletter|wishlist|follow",
    re.IGNORECASE,
)


def card_candidates(cards, price_pattern):
    """Keep visible anchors whose surrounding text shows a price. Every hit is only a review candidate."""
    candidates = []
    for card in cards:
        text = " ".join((card.get("text") or "").split())
        match = price_pattern.search(text)
        if not match or len(text.split()) < 3:
            continue
        line = next((s for s in card["text"].splitlines() if price_pattern.search(s)), card["text"])
        candidates.append({**card, "price_raw": match.group(0), "evidence_text": line.strip()[:400]})
    return candidates


def observation(card, agent_status):
    """A raw observation is evidence for a human reviewer, never a verified fact."""
    return {
        "source_url": card["href"],
        "observed_at": card["observed_at"],
        "description_raw": " ".join(card["text"].split())[:1200],
        "price_raw": card["price_raw"],
        "evidence_text": card["evidence_text"],
        "agent_status": agent_status,
        "match_confidence": "candidate",
        "review_status": "pending",
    }


def on_approved_host(url, allowed):
    host = (urlparse(url).hostname or "").lower()
    return any(host == a or host.endswith("." + a) for a in allowed)


def verify(state, candidates, allowed):
    """Independent checks on the finished run, not the model's DONE answer."""
    labels = [entry.get("action") or "" for entry in state.get("history", [])]
    checks = {
        "agent_finished": state.get("status") in {"done", "blocked"},
        "approved_host": on_approved_host(state["page"]["url"], allowed),
        "priced_cards": bool(candidates),
        "read_only_history": not any(PROHIBITED.search(label) for label in labels),
    }
    return {"passed": all(checks.values()), "checks": checks}


def collect_cards(agent, price_pattern, max_scrolls, settle):
    """Harvest visible cards, then scroll the loaded results. One observed page per scroll."""
    browser = agent.browser
    cards = {}
    page = agent.state["page"]
    for _ in range(max_scrolls + 1):
        # Stamp each read: a visible price is evidence about a page at a timestamp.
        stamp = datetime.now(timezone.utc).isoformat()
        try:
            visible = browser.evaluate(EXTRACT) or []
        except StalePage:
            visible = []
        for card in visible:
            cards.setdefault(card["href"] + "\n" + card["text"][:120], {**card, "observed_at": stamp})
        # Scroll with the action this observed page offers; the snapshot includes scroll_down
        # only while more content sits below the fold, so its absence ends the walk.
        scroll = next((a for a in page["actions"] if a["id"] == "scroll_down"), None)
        if scroll is None:
            break
        try:
            # Act only on a fresh observation; a stale page is re-read, never re-scrolled blind.
            if browser.fresh(page):
                browser.act(scroll, page)
                time.sleep(settle)
        except StalePage:
            pass
        page = browser.observe(screenshot=False)
    agent.state["page"] = page
    agent.state["cards"] = card_candidates(list(cards.values()), price_pattern)
    return agent.snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Approved catalogue or search page to start from.")
    parser.add_argument("--goal", required=True, help="One bounded, read-only goal.")
    parser.add_argument("--allow", action="append", required=True, help="Approved hostname; repeat to allow more.")
    parser.add_argument("--price", default=PRICE.pattern, help="Regex a visible price must match.")
    parser.add_argument("--max-scrolls", type=int, default=6, help="Scroll steps after the agent stops.")
    parser.add_argument("--settle", type=float, default=0.6, help="Seconds to let results render after a scroll.")
    parser.add_argument("--output", default="artifacts/observations/latest")
    args = parser.parse_args()

    allowed = [a.lstrip(".").lower() for a in args.allow]
    if not on_approved_host(args.url, allowed):
        raise SystemExit("Start URL is outside the approved hostnames")
    price_pattern = re.compile(args.price)
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = "OBS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    agent = Agent(args.url, args.goal)
    try:
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
        state = collect_cards(agent, price_pattern, args.max_scrolls, args.settle)
        agent.state["verification"] = verify(state, state["cards"], allowed)
    finally:
        state = agent.snapshot()
        candidates = state.get("cards", [])
        verification = state.get("verification") or {"passed": False, "checks": {"completed": False}}
        folder = Path(args.output)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "observations.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "start_url": args.url,
                    "started_at": started_at,
                    "agent_status": state["status"],
                    "verification": verification,
                    "observations": [observation(card, state["status"]) for card in candidates],
                },
                indent=2,
            )
        )
        (folder / "state.json").write_text(json.dumps(state, indent=2))
        agent.close()
    print(json.dumps(state["verification"], indent=2))
    if not state["verification"]["passed"]:
        raise SystemExit("Run did not satisfy the read-only observation checks")


if __name__ == "__main__":
    main()
