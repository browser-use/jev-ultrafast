"""Offline contract: an unlabelled <select> must not make the decision request quadratic.

Needs the local debug browser only (no paid API). Written after lite.duckduckgo.com returned
HTTP 400 max_tokens_exceeded: 67 country options, each labelled with the select's own
name-from-content (all 67 texts), made one question head 156KB.
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

OPTIONS = [f"Country {i}" for i in range(200)]
PAGE = (
    "<!doctype html><title>Select shapes</title>"
    "<select name='country'>" + "".join(f"<option>{o}</option>" for o in OPTIONS) + "</select>"
    "<label for='cat'>Category</label><select id='cat'><option>Design</option>"
    "<option>Nature</option></select>"
)


@pytest.fixture(scope="module")
def observed(tmp_path_factory):
    path = tmp_path_factory.mktemp("select") / "page.html"
    path.write_text(PAGE, encoding="utf-8")
    browser = Browser(path.as_uri().replace("\\", "/"))
    try:
        return browser.observe(screenshot=False)
    finally:
        browser.close()


def select_actions(state):
    return [a for a in state["actions"] if a["kind"] == "select"]


def test_labels_do_not_carry_the_option_list(observed):
    sample = max(select_actions(observed), key=lambda a: len(a["label"]))
    size, text = len(sample["label"]), sample["label"][:160]
    assert size < 120, f"{size} char label carries the option list: {text!r}"


def test_unlabelled_select_names_its_control_and_value(observed):
    unlabelled = [a for a in select_actions(observed) if a["current_value"] == OPTIONS[0]]
    assert unlabelled, "the unlabelled select disappeared"
    assert all(a["label"].startswith("country (") for a in unlabelled), unlabelled[0]["label"]


def test_labelled_select_keeps_its_real_name(observed):
    labelled = {a["label"] for a in select_actions(observed) if a["current_value"] == "Design"}
    assert labelled == {"Category → Nature"}, labelled


def test_every_selectable_option_stays_addressable(observed):
    listed = {a["label"].split(" → ")[-1] for a in select_actions(observed)}
    # The selected option is not a candidate by design (snapshot.js skips it); everything else must be.
    assert set(OPTIONS[1:]) <= listed, f"{len(OPTIONS[1:]) - len(set(OPTIONS[1:]) & listed)} options missing"
