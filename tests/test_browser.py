"""Offline contracts for the Browser Harness freshness gate.

These pin the frozen behaviour of ``Browser.fresh`` as the live guard check
(``scripts/check_guards.py``) exercises it: a targeted decision compares the
whole recorded guard, including the nearby visible text it was decided on, and
a document-level decision compares the page marker.
"""

from __future__ import annotations

from jev_ultrafast.browser import MARKER, Browser


class FakeBrowser(Browser):
    """A Browser whose in-page reads are scripted instead of sent over CDP."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.expressions = []

    def evaluate(self, expression):
        self.expressions.append(expression)
        return self.answers.pop(0)


EPOCH = 1700000000.5


def _page():
    guard = [7, "textbox", "Destination city", "", None, None, None, False, None, None, None, None, None, "old scope"]
    return {
        "url": "https://example.test/form",
        "epoch": EPOCH,
        "marker": "marker-a",
        "page_key": [EPOCH, "https://example.test/form", 0, 0, 1200, 800, []],
        "guards": {"7": guard},
    }


def _action(kind="click", node=7):
    return {"id": "e1", "kind": kind, "label": "Destination city", "node": node, "ref": "@7"}


def test_a_click_is_fresh_while_its_target_guard_is_unchanged():
    page = _page()
    browser = FakeBrowser([[page["page_key"], page["guards"]["7"]]])
    for kind in ("click", "select"):
        page["guards"]["7"] = list(page["guards"]["7"])
        browser = FakeBrowser([[page["page_key"], page["guards"]["7"]]])
        assert browser.fresh(page, _action(kind)) is True


def test_nearby_text_churn_invalidates_a_click_decision():
    """The guard's trailing entry is the nearby visible text the decision saw."""
    page = _page()
    churned = page["guards"]["7"][:-1] + ["totally different text nearby"]
    browser = FakeBrowser([[page["page_key"], churned]])
    assert browser.fresh(page, _action("click")) is False


def test_a_replaced_target_invalidates_a_click_decision():
    page = _page()
    replaced = list(page["guards"]["7"])
    replaced[2] = "Another field"
    browser = FakeBrowser([[page["page_key"], replaced]])
    assert browser.fresh(page, _action("click")) is False


def test_a_detached_or_unaddressable_target_is_never_fresh():
    page = _page()
    assert FakeBrowser([[page["page_key"], None]]).fresh(page, _action("click")) is False
    assert FakeBrowser([[page["page_key"], page["guards"]["7"]]]).fresh(page, _action("click", node=None)) is False


def test_a_fill_is_guarded_by_the_page_marker_not_a_target_guard():
    """HEAD's contract: only click and select compare a target guard."""
    page = _page()
    assert FakeBrowser(["marker-a"]).fresh(page, _action("fill")) is True
    assert FakeBrowser(["marker-b"]).fresh(page, _action("fill")) is False


def test_a_document_decision_compares_the_page_marker():
    page = _page()
    browser = FakeBrowser(["marker-a"])
    assert browser.fresh(page) is True
    assert browser.expressions == [MARKER]
    assert FakeBrowser(["marker-b"]).fresh(page) is False
    assert FakeBrowser([None]).fresh(page) is False