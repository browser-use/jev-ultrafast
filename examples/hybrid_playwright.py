"""Playwright and Jev sharing one Chrome over CDP.

uv run --with playwright --env-file .env python examples/hybrid_playwright.py
uv run --with playwright --env-file .env python examples/hybrid_playwright.py --handoff-only

Playwright owns the deterministic setup and the final check; Jev owns the goal-driven middle.
--handoff-only skips the model and checks the shared session without paid API calls.
"""

import argparse
import os

from playwright.sync_api import sync_playwright

from jev_ultrafast import Agent

URL = "https://en.wikipedia.org/wiki/Main_Page"
GOAL = "Find and open the Wikipedia article about Gödel’s incompleteness theorems."
HEADING = "Gödel's incompleteness theorems"
COOKIE = {"name": "jev_hybrid", "value": "shared", "domain": ".wikipedia.org", "path": "/"}


def target_id(context, page):
    session = context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def jev_page(context, agent, waiter):
    """Playwright's handle on the tab Jev opened, matched by CDP target id."""
    # Playwright learns about Jev's tab from an attach event, which arrives after Target.createTarget returns.
    for _ in range(50):
        for page in context.pages:
            if target_id(context, page) == agent.browser.target:
                return page
        waiter.wait_for_timeout(100)
    raise RuntimeError("Playwright does not see Jev's tab; are both attached to the same browser?")


parser = argparse.ArgumentParser()
parser.add_argument("--url", default=URL)
parser.add_argument("--goal", default=GOAL)
parser.add_argument("--heading", default=HEADING, help="Expected #firstHeading text after the goal.")
parser.add_argument("--cdp-url", default=os.environ.get("BU_CDP_URL", "http://127.0.0.1:9223"))
parser.add_argument("--handoff-only", action="store_true")
args = parser.parse_args()

with sync_playwright() as p:
    # contexts[0] is the context Jev opens its tabs in; browser.new_context() would not share cookies.
    browser = p.chromium.connect_over_cdp(args.cdp_url, no_defaults=True)
    context = browser.contexts[0]
    setup = context.new_page()
    context.add_cookies([COOKIE])
    setup.goto(args.url)

    with Agent(setup.url, args.goal) as agent:
        tab = jev_page(context, agent, setup)
        shared = "jev_hybrid=shared" in agent.browser.evaluate("document.cookie")
        print(f"Playwright sees Jev's tab: {tab.url}")
        print(f"Cookie set by Playwright visible to Jev: {shared}")
        if not shared:
            raise SystemExit(1)

        if not args.handoff_only:
            for state in agent.run():
                print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
            heading = tab.locator("#firstHeading").inner_text()
            passed = heading == args.heading
            print(f"Playwright check: heading={heading!r} {'PASS' if passed else 'FAIL'}")
            if not passed:
                raise SystemExit(1)

    setup.close()
