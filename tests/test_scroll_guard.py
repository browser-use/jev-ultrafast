"""Scroll validation must protect the wheel destination without depending on page copy."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import browser

GUARD = {
    "position": [123, "https://example.test", 0, 0, 1120, 780],
    "point": {"x": 550, "y": 650},
    "context": [],
    "can_scroll_up": False,
    "can_scroll_down": True,
}
ACTION = {"id": "scroll_down", "kind": "scroll", "delta": 560}


def test_scroll_ignores_full_page_marker():
    instance = browser.Browser.__new__(browser.Browser)
    instance.evaluate = Mock(return_value=deepcopy(GUARD))
    page = {"scroll_guard": GUARD, "marker": ["old text and old controls"]}

    assert instance.fresh(page, ACTION)
    assert instance.evaluate.call_args.args[0] != browser.MARKER


@pytest.mark.parametrize("changed", ["document", "url", "offset", "viewport", "context", "direction", "point"])
def test_scroll_rejects_changed_destination(changed):
    current = deepcopy(GUARD)
    if changed in {"document", "url", "offset", "viewport"}:
        index = {"document": 0, "url": 1, "offset": 3, "viewport": 5}[changed]
        current["position"][index] = "different"
    elif changed == "context":
        current["context"] = [99]
    elif changed == "direction":
        current["can_scroll_down"] = False
    else:
        current["point"]["y"] = 400
    instance = browser.Browser.__new__(browser.Browser)
    instance.evaluate = Mock(return_value=current)

    assert not instance.fresh({"scroll_guard": GUARD}, ACTION)


def test_scroll_rechecks_immediately_before_one_wheel_event(monkeypatch):
    cdp = Mock(return_value={"result": {"value": GUARD["point"]}})
    monkeypatch.setattr(browser, "cdp", cdp)

    browser.browser_operation({
        "operation": "act", "session": "test", "action": ACTION, "scroll_guard": GUARD,
    })

    assert cdp.call_count == 2
    assert cdp.call_args_list[0].args == ("Runtime.evaluate",)
    assert cdp.call_args_list[1].args == ("Input.dispatchMouseEvent",)
    assert cdp.call_args_list[1].kwargs == {
        "session_id": "test", "type": "mouseWheel", "x": 550, "y": 650, "deltaX": 0, "deltaY": 560,
    }


def test_scroll_change_between_validation_and_dispatch_prevents_input(monkeypatch):
    cdp = Mock(return_value={"result": {"value": None}})
    monkeypatch.setattr(browser, "cdp", cdp)

    with pytest.raises(browser.StalePage, match="wheel target changed"):
        browser.browser_operation({
            "operation": "act", "session": "test", "action": ACTION, "scroll_guard": GUARD,
        })

    cdp.assert_called_once()
    assert cdp.call_args.args == ("Runtime.evaluate",)


def test_scroll_without_observed_guard_dispatches_nothing(monkeypatch):
    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)

    with pytest.raises(browser.StalePage, match="No observed document scroll"):
        browser.browser_operation({"operation": "act", "session": "test", "action": ACTION})

    cdp.assert_not_called()
