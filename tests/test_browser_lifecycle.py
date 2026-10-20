"""Offline regression checks for resetting a task whose tab was closed."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import browser, demo


def owned_browser():
    instance = browser.Browser.__new__(browser.Browser)
    instance.target = "owned-tab"
    return instance


def test_close_can_be_called_twice(monkeypatch):
    cdp = Mock(return_value={"success": True})
    monkeypatch.setattr(browser, "cdp", cdp)
    instance = owned_browser()

    instance.close()
    instance.close()

    assert instance.target is None
    cdp.assert_called_once_with("Target.closeTarget", targetId="owned-tab")


MISSING_TARGET = {"code": -32602, "message": "No target with given id found"}


@pytest.mark.parametrize("detail", [MISSING_TARGET, str(MISSING_TARGET), json.dumps(MISSING_TARGET)])
def test_close_forgets_an_already_closed_tab(monkeypatch, detail):
    cdp = Mock(side_effect=RuntimeError(detail))
    monkeypatch.setattr(browser, "cdp", cdp)
    instance = owned_browser()

    instance.close()
    instance.close()

    assert instance.target is None
    cdp.assert_called_once_with("Target.closeTarget", targetId="owned-tab")


@pytest.mark.parametrize(
    "detail",
    [
        {"code": -32602, "message": "Invalid parameters"},
        {"code": -32000, "message": "No target with given id found"},
        "Browser connection lost",
        "{'code': -32602, 'message': 'Invalid parameters'}",
        "{'code': -32000, 'message': 'No target with given id found'}",
        "{'code': -32602, 'message':",
        "['No target with given id found']",
    ],
)
def test_close_preserves_unexpected_errors(monkeypatch, detail):
    cdp = Mock(side_effect=RuntimeError(detail))
    monkeypatch.setattr(browser, "cdp", cdp)
    instance = owned_browser()

    with pytest.raises(RuntimeError) as error:
        instance.close()

    assert error.value.args == (detail,)
    assert instance.target == "owned-tab"
    cdp.assert_called_once()


@pytest.mark.parametrize("detail", [MISSING_TARGET, str(MISSING_TARGET)])
def test_new_task_starts_after_previous_tab_was_closed(monkeypatch, detail):
    monkeypatch.setattr(
        browser,
        "cdp",
        Mock(side_effect=RuntimeError(detail)),
    )
    previous_browser = owned_browser()
    previous_agent = Mock(close=previous_browser.close)
    next_agent = Mock(state={}, snapshot=Mock(return_value={"status": "ready"}))
    create_agent = Mock(return_value=next_agent)
    monkeypatch.setattr(demo, "AGENT", previous_agent)
    monkeypatch.setattr(demo, "Agent", create_agent)

    result = demo.command("reset", {"scenario": "research", "goal": "Open the requested article."})

    assert result["status"] == "ready"
    assert previous_browser.target is None
    assert demo.AGENT is next_agent
    create_agent.assert_called_once_with(
        f"{demo.ORIGIN}/fixture.html?scenario=research",
        "Open the requested article.",
        screenshots=True,
        record_dir=None,
    )
    assert next_agent.state["scenario"] == "research"


def test_observation_waits_for_document_longer_than_the_old_poll_window(monkeypatch):
    instance = owned_browser()
    instance.session = "test"
    clock = [0.0]
    monkeypatch.setattr(browser.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    page = {"title": "Loaded"}
    observe = Mock(side_effect=[browser.StalePage("Document is navigating")] * 12 + [page])
    monkeypatch.setattr(browser, "browser_operation", observe)

    assert instance.observe(screenshot=False) is page
    assert clock[0] > 0.5
    assert observe.call_count == 13
    assert all(call.args[0] == {"operation": "observe", "session": "test", "screenshot": False}
               for call in observe.call_args_list)


def test_unfinished_navigation_has_a_bounded_wait_and_clear_error(monkeypatch):
    instance = owned_browser()
    instance.session = "test"
    clock = [0.0]
    monkeypatch.setattr(browser.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    observe = Mock(side_effect=browser.StalePage("Document is navigating"))
    monkeypatch.setattr(browser, "browser_operation", observe)

    with pytest.raises(RuntimeError, match="continue the current task"):
        instance.observe(screenshot=False)

    assert clock[0] == browser.OBSERVATION_TIMEOUT
    assert all(call.args[0]["operation"] == "observe" for call in observe.call_args_list)


def test_observation_does_not_hide_a_lost_browser_connection(monkeypatch):
    instance = owned_browser()
    instance.session = "test"
    observe = Mock(side_effect=RuntimeError("Browser connection lost"))
    monkeypatch.setattr(browser, "browser_operation", observe)

    with pytest.raises(RuntimeError, match="Browser connection lost"):
        instance.observe(screenshot=False)

    observe.assert_called_once()
