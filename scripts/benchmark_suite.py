"""Task registry, independent validators, and expectation model for the final benchmark.

The policy under test never imports this module: it only supplies a URL and one
natural-language goal. Everything here is measurement scaffolding.

Three things live here:

* ``TASKS`` — the fixed task set (Example, Wikipedia, a real complex site, the
  deterministic local fixture, and the three focused Part B probes).
* ``validate`` — an independent, code-owned DOM read that decides PASS/FAIL.
  A ``DONE`` choice by the policy is never the pass criterion.
* ``expected_step`` — the canonical next operation/target for one decision
  point, derived from the page the policy actually observed and from the
  progress replayed out of the run's own executed actions. It is used to score
  operation judgment and target selection; it never changes an action.

Expectations are deliberately conservative. Steps that are genuinely ambiguous
are still scored strictly, and the run record additionally marks whether the
model picked an acceptable alternative, so the report can separate "wrong" from
"valid but not the canonical order".
"""

from __future__ import annotations

import base64
import http.server
import json
import re
import threading
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
REQUESTED_DESTINATION = "Zurich to London"
REQUESTED_CATEGORY = "flights"
REQUESTED_PRIORITY = "high"

# ---------------------------------------------------------------- validators

COMPLEX_VALIDATOR = """(() => {
  const b = document.body.dataset;
  const visible = (e) => !!e && !e.hidden && e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const success = document.getElementById('success');
  const checks = {
    destination: (document.getElementById('destination') || {}).value || '',
    category: (document.getElementById('category') || {}).value || '',
    priority: (document.querySelector('input[name="priority"]:checked') || {}).value || '',
    continued: b.continued === 'true',
    reviewed: b.reviewed === 'true',
    submitted: b.submitted === 'true',
    async_done: b.asyncDone === 'true',
    scrolled: b.scrolled === 'true',
    max_scroll: Number(b.maxScroll || 0),
    distractor_clicks: Number(b.distractorClicks || 0),
    search_distractor_value: (document.getElementById('search') || {}).value || '',
    success_visible: visible(success),
    success_text: success ? success.innerText.trim().replace(/\\s+/g, ' ') : ''
  };
  checks.pass = checks.destination === 'Zurich to London' && checks.category === 'flights' &&
    checks.priority === 'high' && checks.continued && checks.reviewed &&
    checks.submitted && checks.success_visible;
  return checks;
})()"""

SELECT_VALIDATOR = """(() => {
  const b = document.body.dataset;
  const confirmation = document.getElementById('confirmation');
  const checks = {
    carrier: (document.getElementById('carrier') || {}).value || '',
    packaging: (document.getElementById('packaging') || {}).value || '',
    confirmed: b.confirmed === 'true',
    confirmation_visible: !!confirmation && getComputedStyle(confirmation).display !== 'none'
  };
  checks.pass = checks.carrier === 'overnight' && checks.confirmed && checks.confirmation_visible;
  return checks;
})()"""

SCROLL_VALIDATOR = """(() => {
  const b = document.body.dataset;
  const code = document.getElementById('code');
  const checks = {
    scrolled: b.scrolled === 'true',
    revealed: b.revealed === 'true',
    max_scroll: Number(b.maxScroll || 0),
    changelog_clicks: Number(b.changelogClicks || 0),
    code_visible: !!code && !code.hidden,
    code_text: code ? code.innerText.trim() : ''
  };
  checks.pass = checks.scrolled && checks.revealed && checks.code_visible &&
    checks.code_text === 'CODE-4.2-READY';
  return checks;
})()"""

WAIT_VALIDATOR = """(() => {
  const b = document.body.dataset;
  const result = document.getElementById('result');
  const checks = {
    started: b.started === 'true',
    ready: b.ready === 'true',
    downloaded: b.downloaded === 'true',
    distractor_clicks: Number(b.distractorClicks || 0),
    result_visible: !!result && result.classList.contains('on')
  };
  checks.pass = checks.started && checks.ready && checks.downloaded && checks.result_visible;
  return checks;
})()"""

EXAMPLE_VALIDATOR = """(() => {
  const href = location.href;
  const text = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim();
  const checks = {
    url: href,
    title: document.title,
    on_iana: /(^|\\.)iana\\.org/.test(location.hostname),
    mentions_example: /example/i.test(text)
  };
  checks.pass = checks.on_iana && checks.mentions_example;
  return checks;
})()"""

WIKIPEDIA_VALIDATOR = """(() => {
  let href = location.href;
  try { href = decodeURIComponent(href); } catch (error) { /* keep the raw href */ }
  const heading = (document.querySelector('#firstHeading') || {}).innerText || '';
  const checks = {
    url: href,
    title: document.title,
    heading: heading.trim(),
    article_open: /Godel|G\\u00f6del/i.test(href) && /incompleteness_theorems/i.test(href),
    heading_matches: /incompleteness theorems/i.test(heading)
  };
  checks.pass = checks.article_open && checks.heading_matches;
  return checks;
})()"""

FLIGHTS_VALIDATOR = """(() => {
  const text = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ');
  const raw_tfs = new URLSearchParams(location.search).get('tfs') || '';
  let committed = '';
  try {
    const padded = raw_tfs.replace(/-/g, '+').replace(/_/g, '/');
    committed = atob(padded + '='.repeat((4 - padded.length % 4) % 4));
  } catch (error) {
    committed = '';
  }
  // Google Flights encodes the itinerary it has committed to in tfs: a
  // departure date string and the trip-type field (protobuf field 19, varint).
  // The empty search form already advertises "cheap flights from" and
  // promotional "from $158" prices, so only these committed markers count.
  const field19 = () => {
    for (let i = 0; i + 2 < committed.length; i++) {
      if (committed.charCodeAt(i) === 0x98 && committed.charCodeAt(i + 1) === 0x01) {
        return committed.charCodeAt(i + 2);
      }
    }
    return 0;
  };
  const checks = {
    url: location.href,
    title: document.title,
    origin: /(Zurich|Z\\u00fcrich)/i.test(text),
    destination: /London/i.test(text),
    one_way: field19() === 2,
    dated: /\\d{4}-\\d{2}-\\d{2}/.test(committed),
    results_list: /(Sort by|Departing flights|Best departing|Top departing flights)/i.test(text),
    prices_visible: /(one way price|from \\$\\d)/i.test(text),
    captcha: /(unusual traffic|not a robot|CAPTCHA|Sorry\\.\\.\\.)/i.test(text),
    sample: text.slice(0, 160)
  };
  checks.pass = checks.origin && checks.destination && checks.one_way &&
    checks.dated && !checks.captcha;
  return checks;
})()"""


# ------------------------------------------------------- expectation helpers

def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def page_labels(page):
    """Human labels of the targets the policy could actually choose from."""
    return [_norm(action.get("label")) for action in (page or {}).get("actions", []) if action.get("label")]


def page_has_label(page, label):
    return _norm(label) in page_labels(page)


def page_url_title(page):
    return str((page or {}).get("url") or ""), str((page or {}).get("title") or "")


class Progress:
    """Replay of what the run has actually executed so far."""

    def __init__(self):
        self.destination_filled = False
        self.category_set = False
        self.category_option = None
        self.priority_set = False
        self.waited = 0
        self.continued = False
        self.scrolled = False
        self.reviewed = False
        self.submitted = False
        self.distractor_steps = []
        self.steps = 0
        self.failures = 0

    def observe_action(self, entry):
        """Update from one executed history entry."""
        kind = str(entry.get("kind") or "")
        choice = str(entry.get("choice") or "")
        label = _norm(entry.get("action"))
        self.steps += 1
        if kind in {"done", "blocked"}:
            return
        if kind == "wait":
            self.waited += 1
            return
        if kind == "scroll":
            if choice == "scroll_down" or (isinstance(entry.get("delta"), int) and entry["delta"] > 0):
                self.scrolled = True
            return
        if kind == "fill" and label.startswith("destination city"):
            self.destination_filled = True
            return
        if kind == "select" and label.startswith("category"):
            self.category_set = True
            marker = str(entry.get("action") or "").rfind(" → ")
            self.category_option = str(entry.get("action"))[marker + 3:] if marker >= 0 else None
            return
        if kind == "click":
            if label == "high":
                self.priority_set = True
            elif label == "continue":
                self.continued = True
            elif label == "final review":
                self.reviewed = True
            elif label == "submit request":
                self.submitted = True
            else:
                self.distractor_steps.append(label)
            return
        self.distractor_steps.append(label)


def _step(operation, target=None, alternatives=None, goal_satisfied=False):
    return {
        "operation": operation,
        "target": target,
        "alternatives": alternatives or [],
        "goal_satisfied": goal_satisfied,
    }


def expected_complex(page, progress):
    """Canonical next step for the deterministic fixture."""
    if not progress.destination_filled:
        return _step("TYPE_TEXT", "Destination city")
    if not progress.category_set:
        return _step("SELECT", "Category → Flights")
    if not progress.priority_set:
        # The availability check is already pending, so waiting here is a valid
        # alternative; the stated order makes clicking the radio canonical.
        alternatives = [] if progress.waited else [{"operation": "WAIT", "target": None}]
        return _step("CLICK", "High", alternatives)
    if not progress.continued:
        if page_has_label(page, "Continue"):
            return _step("CLICK", "Continue")
        return _step("WAIT")
    if not progress.reviewed:
        if page_has_label(page, "Final Review"):
            return _step("CLICK", "Final Review")
        return _step("SCROLL_DOWN")
    if not progress.submitted:
        return _step("CLICK", "Submit request")
    return _step("DONE", goal_satisfied=progress.submitted)


def expected_example(page, progress):
    """Canonical next step for the Example Domain task."""
    url, title = page_url_title(page)
    if "iana.org" in url.casefold():
        return _step("DONE", goal_satisfied=True)
    # The link's accessible name on the live page ("Learn more"), not the
    # historical "More information..." that the goal text used to quote.
    return _step("CLICK", "Learn more")


def _query_typed(page):
    for action in (page or {}).get("actions", []):
        if action.get("kind") == "fill" and action.get("value"):
            return str(action["value"]).strip()
    return ""


def expected_wikipedia(page, progress):
    """Canonical next step for the Wikipedia task, derived from page state."""
    url, title = page_url_title(page)
    haystack = f"{url} {title}".casefold()
    if "incompleteness_theorems" in haystack:
        return _step("DONE", goal_satisfied=True)
    if _query_typed(page):
        # A query is in the field, so the next useful move is choosing a result
        # or suggestion rather than typing again.
        return _step("CLICK", None, alternatives=[{"operation": "TYPE_TEXT", "target": None}])
    return _step("TYPE_TEXT", "Search Wikipedia", alternatives=[{"operation": "CLICK", "target": None}])


def _tfs_bytes(url):
    """The itinerary Google Flights has committed into the URL, if any."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query)
    raw = (query.get("tfs") or [""])[0]
    if not raw:
        return b""
    padded = raw.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    try:
        return base64.b64decode(padded)
    except (ValueError, TypeError):
        return b""


def _committed_itinerary(url):
    """(date committed, one way committed) read from the tfs protobuf.

    Field 19 is the trip type: 1 is round trip, 2 is one way. The empty search
    form publishes promotional prices and a "cheap flights from" blurb, so the
    committed payload is the only trustworthy success signal on this site.
    """
    payload = _tfs_bytes(url)
    dated = bool(re.search(rb"\d{4}-\d{2}-\d{2}", payload))
    marker = payload.find(b"\x98\x01")
    one_way = marker != -1 and marker + 2 < len(payload) and payload[marker + 2] == 2
    return dated, one_way


def _dated_tfs(url):
    """True when Google Flights has committed a departure date into the URL."""
    return _committed_itinerary(url)[0]


def expected_flights(page, progress):
    """Canonical next step for the real complex site.

    The site is a live third party with several valid paths, so this model only
    claims the states that are unambiguous from the observed page alone: the
    itinerary is committed, or the ticket type is still round trip while the goal
    asks for one way. Everything else is recorded as an open step rather than
    guessed at, and the sample counts are reported with the score.
    """
    url, _title = page_url_title(page)
    text = _norm((page or {}).get("text"))
    if _committed_itinerary(url) == (True, True) or any(
        marker in text for marker in ("sort by", "departing flights", "best departing")
    ):
        return _step("DONE", goal_satisfied=True)
    labels = page_labels(page)
    # The ticket-type menu is open: the next step is the option the goal asks for.
    # Without this state the expectation kept demanding the control that was
    # already used, so a correct "One way" click was scored as a wrong target.
    if any(label == "one way" for label in labels):
        return _step("CLICK", "One way")
    trip = next((label for label in labels if "ticket type" in label), "")
    if trip and "one way" not in trip:
        return _step("CLICK", trip)
    return None


def expected_accepts_anything(page, progress):
    """The Part B probes: only the goal state is asserted, not a step script."""
    return None


# ----------------------------------------------------------------- task set

def _static_page(html):
    """Serve one fixture inline for the fixtures that are not the main page."""
    return html


TASKS = [
    {
        "key": "example",
        "label": "example",
        "name": "Task A — Simple",
        "url": "https://example.com/",
        "goal": (
            "Click the link on this page that explains the example domain, so the "
            "IANA page about example domains is open."
        ),
        "runs": 3,
        "validator": EXAMPLE_VALIDATOR,
        "expect": expected_example,
        "suites": ("ab",),
    },
    {
        "key": "wikipedia",
        "label": "wikipedia",
        "name": "Task B — Medium",
        "url": "https://en.wikipedia.org/wiki/Main_Page",
        "goal": (
            "Find and open the Wikipedia article about Gödel's incompleteness "
            "theorems. Stop when the requested article is visibly open."
        ),
        "runs": 3,
        "validator": WIKIPEDIA_VALIDATOR,
        "expect": expected_wikipedia,
        "suites": ("ab", "reliability"),
    },
    {
        "key": "flights",
        "label": "flights",
        "name": "Task C — Complex real website",
        "url": "https://www.google.com/travel/flights?hl=en",
        "goal": (
            "Use Google Flights to set up a one-way flight search from Zurich to London "
            "for one adult in economy, and pick the first departure date the calendar "
            "offers. Stop once the search is committed for that route and date."
        ),
        "runs": 3,
        "validator": FLIGHTS_VALIDATOR,
        "expect": expected_flights,
        "suites": ("ab",),
    },
    {
        "key": "complex",
        "label": "complex",
        "name": "Task D — Complex deterministic Jev benchmark",
        "fixture": "jev_complex_benchmark.html",
        "goal": (
            "Create a travel research request: enter \"Zurich to London\" as the "
            "destination, set the category to \"Flights\", then choose \"High\" "
            "priority, then wait until the Continue control appears, continue, "
            "scroll down to the review section, open the final review, and submit "
            "only when the summary shows the requested destination, category, and "
            "priority. Stop when the success panel is visible."
        ),
        "runs": 5,
        "validator": COMPLEX_VALIDATOR,
        "expect": expected_complex,
        "suites": ("ab",),
    },
    {
        "key": "select",
        "label": "probe-select",
        "name": "Part B — SELECT",
        "fixture": "jev_select_probe.html",
        "goal": (
            "Set the shipping carrier speed to \"Overnight (next morning)\". "
            "Stop when the page confirms the carrier was set to Overnight."
        ),
        "runs": 1,
        "validator": SELECT_VALIDATOR,
        "expect": expected_accepts_anything,
        "suites": ("probes",),
    },
    {
        "key": "scroll",
        "label": "probe-scroll",
        "name": "Part B — SCROLL",
        "fixture": "jev_scroll_probe.html",
        "goal": (
            "Scroll down to the Release section at the bottom of this page and "
            "reveal the release code. Stop when the release code is visible."
        ),
        "runs": 1,
        "validator": SCROLL_VALIDATOR,
        "expect": expected_accepts_anything,
        "suites": ("probes",),
    },
    {
        "key": "wait",
        "label": "probe-wait",
        "name": "Part B — WAIT",
        "fixture": "jev_wait_probe.html",
        "goal": (
            "Generate the quarterly report, then download it once the build has "
            "finished. Stop when the page shows that the report is ready and "
            "downloaded."
        ),
        "runs": 1,
        "validator": WAIT_VALIDATOR,
        "expect": expected_accepts_anything,
        "suites": ("probes",),
    },
]


def task_by_key(key):
    for task in TASKS:
        if task["key"] == key:
            return task
    raise KeyError(key)


def tasks_for_suite(suite):
    return [task for task in TASKS if suite in task["suites"]]


def fixture_url(port, task):
    name = task.get("fixture")
    return f"http://127.0.0.1:{port}/{name}" if name else task["url"]


@contextmanager
def fixture_server(directory=None):
    """Serve tests/fixtures on a loopback port for the deterministic pages."""
    root = str(directory or FIXTURES)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def validate(backend, expression):
    """One code-owned DOM read through whichever backend is under test."""
    reader = getattr(backend, "evaluate_script", None)
    if callable(reader):
        return reader(expression)
    return backend.evaluate(expression)


def validator_passed(result):
    return bool(isinstance(result, dict) and result.get("pass") is True)


def summarize_validation(result):
    if not isinstance(result, dict):
        return {"pass": False, "error": f"validator returned {type(result).__name__}"}
    checks = {key: value for key, value in result.items() if key != "pass"}
    return {"pass": bool(result.get("pass")), "checks": checks}


def load_validator(name):
    return globals()[name]


def json_dumps(payload):
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"