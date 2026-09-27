"""Offline contract: a popup that marks the page behind it ``aria-hidden`` removes that page from the
next snapshot -- by design, because the reader honours ARIA. Written after Google Flights' ticket-type
menu was measured live (2026-09-27): 61 painted controls, 61 of them under an ``aria-hidden`` ancestor,
and the next observation carried 2 synthetic actions instead of 31.

This is the state the policy then answers BLOCKED about, so it is pinned here rather than rediscovered:
the hidden subtree must go, the popup must stay, and a control outside the hidden subtree must survive
(the last one is what separates "honoured aria-hidden" from "the snapshot broke").

Needs the local debug browser only (no paid API).
"""

import os
import socket

import pytest

from jev_ultrafast.browser import Browser


def debug_port_up():
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", int(os.environ.get("BU_CDP_PORT", "9223")))) == 0


pytestmark = pytest.mark.skipif(
    not debug_port_up(), reason="no CDP browser on 9223 -- start `python jev_browser.py` first"
)

PAGE = """<!doctype html><title>Popup hides the page</title>
<div id="app">
  <button aria-label="Departure city">Zurich</button>
  <button id="trigger" aria-label="Change ticket type">Round trip</button>
</div>
<button id="outside" aria-label="Always visible help">?</button>
<script>
  document.getElementById('trigger').addEventListener('click', () => {
    const list = document.createElement('ul');
    list.setAttribute('role', 'listbox');
    for (const text of ['One way', 'Multi-city']) {
      const item = document.createElement('li');
      item.setAttribute('role', 'option');
      item.style.cssText = 'padding:12px;border:1px solid #888';
      item.textContent = text;
      list.append(item);
    }
    document.body.append(list);
    document.getElementById('app').setAttribute('aria-hidden', 'true');
  });
</script>
"""


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    path = tmp_path_factory.mktemp("popup") / "page.html"
    path.write_text(PAGE, encoding="utf-8")
    browser = Browser(path.as_uri().replace("\\", "/"))
    try:
        before = browser.observe(screenshot=False)
        trigger = next(a for a in before["actions"] if a["label"] == "Change ticket type")
        browser.act(trigger, before)
        return before, browser.observe(screenshot=False)
    finally:
        browser.close()


def labels(state):
    return {a["label"] for a in state["actions"]}


ALL_HIDDEN = """<!doctype html><title>Whole page hidden</title>
<div id="app" aria-hidden="true">
  <button aria-label="Departure city">Zurich</button>
  <button aria-label="Arrival city">London</button>
</div>
"""

ALL_INERT = """<!doctype html><title>Whole page inert</title>
<div id="app" inert>
  <button aria-label="Departure city">Zurich</button>
</div>
"""


def opened(tmp_path_factory, name, html):
    path = tmp_path_factory.mktemp(name) / "page.html"
    path.write_text(html, encoding="utf-8")
    browser = Browser(path.as_uri().replace("\\", "/"))
    try:
        return browser, browser.observe(screenshot=False)
    except Exception:
        browser.close()
        raise


@pytest.fixture(scope="module")
def hidden_page(tmp_path_factory):
    browser, state = opened(tmp_path_factory, "all_hidden", ALL_HIDDEN)
    try:
        yield browser, state
    finally:
        browser.close()


@pytest.fixture(scope="module")
def inert_page(tmp_path_factory):
    browser, state = opened(tmp_path_factory, "all_inert", ALL_INERT)
    try:
        yield browser, state
    finally:
        browser.close()


def test_a_fully_hidden_page_still_yields_controls(hidden_page):
    """Nothing but aria-hidden is painted here, so the strict pass would report an empty page."""
    _, state = hidden_page
    assert {"Departure city", "Arrival city"} <= labels(state), labels(state)
    assert "Zurich" in state["text"], "the fallback must bring the text back with the controls"


def test_inert_is_still_honoured_when_it_is_all_there_is(inert_page):
    """inert blocks input, so re-reading the subtree cannot make its controls addressable."""
    _, state = inert_page
    assert [a for a in state["actions"] if "node" in a] == [], "only the synthetic actions may remain"


def test_a_revealed_control_is_actually_executable(hidden_page):
    """A control the fallback offers but the executor refuses would be a trap, not a recovery."""
    browser, state = hidden_page
    action = next(a for a in state["actions"] if a["label"] == "Departure city")
    assert browser.act(action, state) == {"executed": action["id"]}


def test_escape_is_offered_once_and_owns_no_node(hidden_page):
    """The only test here that reads the real reader, so the synthetic action belongs to it."""
    _, state = hidden_page
    escape = [a for a in state["actions"] if a["kind"] == "escape"]
    assert [a["id"] for a in escape] == ["escape"], escape
    assert "node" not in escape[0], "a key press must not be pinned to an element that can go stale"


def test_controls_behind_the_popup_leave_the_table(pair):
    before, after = pair
    assert {"Departure city", "Change ticket type"} <= labels(before)
    assert labels(after).isdisjoint({"Departure city", "Change ticket type"}), labels(after)


def test_the_popup_itself_stays_addressable(pair):
    _, after = pair
    assert {"One way", "Multi-city"} <= labels(after), labels(after)


def test_a_control_outside_the_hidden_subtree_survives(pair):
    before, after = pair
    assert "Always visible help" in labels(before)
    assert "Always visible help" in labels(after), "the whole page vanished, not just the hidden subtree"


def test_hidden_text_leaves_the_page_text(pair):
    before, after = pair
    assert "Zurich" in before["text"]
    assert "Zurich" not in after["text"]
    assert "One way" in after["text"]
