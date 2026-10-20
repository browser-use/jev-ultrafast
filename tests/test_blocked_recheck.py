"""Delayed controls get bounded read-only rechecks; all models and browser I/O are mocked."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast.browser import StalePage, fingerprint


def page(*, text="Loading", url="https://example.test/product", document=1, scroll=0):
    state = {
        "url": url,
        "title": "Product",
        "text": text,
        "scroll": {"y": scroll},
        "page_key": [document, url],
        "actions": [{"id": "e1", "kind": "click", "label": "Continue", "role": "button", "node": 1}],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def decision(choice="BLOCKED"):
    return {
        "choice": choice,
        "operation": "CLICK" if choice == "e1" else choice,
        "target": "1" if choice == "e1" else None,
        "confidence": 1.0,
        "probabilities": {choice: 1.0},
        "latency_ms": 1,
        "usage": {},
    }


@pytest.fixture
def run(monkeypatch):
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page()))
    choose = Mock(return_value=decision())
    sleep = Mock()
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    monkeypatch.setattr(loop, "choose", choose)
    monkeypatch.setattr(loop.time, "sleep", sleep)
    agent = loop.Agent("https://example.test/product", "Open the product and continue.")
    return agent, browser, choose, sleep


def test_permanently_blocked_page_stops_after_two_rechecks(run):
    agent, browser, choose, sleep = run

    assert agent.command("tick")["status"] == "rechecking"
    assert agent.command("tick")["status"] == "rechecking"
    assert agent.command("tick")["status"] == "blocked"

    assert choose.call_count == 3
    assert sleep.call_count == 2
    sleep.assert_called_with(1.5)
    browser.act.assert_not_called()
    assert agent.state["history"] == []
    assert len(agent.state["decisions"]) == 3
    assert [r["attempt"] for r in agent.state["blocked_rechecks"]] == [1, 2]
    assert all(r["page_changed"] is False for r in agent.state["blocked_rechecks"])
    assert all("elapsed_ms" in r for r in agent.state["blocked_rechecks"])


def test_delayed_content_is_used_for_the_next_decision_without_replaying_input(run):
    agent, browser, choose, sleep = run
    loaded = page(text="Continue is ready")
    browser.observe.return_value = loaded
    choose.side_effect = [decision(), decision("e1"), decision("DONE")]

    assert agent.command("tick")["status"] == "rechecking"
    browser.act.assert_not_called()
    assert agent.state["decision"] is None
    assert agent.state["blocked_rechecks"][0]["page_changed"] is True

    assert agent.command("tick")["status"] == "ready"
    assert choose.call_args.args[0] is loaded
    browser.act.assert_called_once_with(loaded["actions"][0], loaded, text=None)
    assert len(agent.state["history"]) == 1
    assert agent.command("tick")["status"] == "done"
    assert agent.state["plan_index"] == 1
    sleep.assert_called_once_with(1.5)


def test_loading_changes_cannot_skip_the_blocked_wait(run):
    agent, browser, choose, sleep = run
    loading = page(text="Header loaded")
    ready = page(text="Continue is ready")
    browser.observe.side_effect = [loading, ready, ready]
    # Prediction can refresh; terminal freshness would fail while the page is still loading.
    browser.fresh.return_value = True

    def predict_during_loading(*_args):
        browser.fresh.return_value = False
        return decision()

    choose.side_effect = predict_during_loading
    first = agent.command("tick")
    assert first["status"] == "rechecking"
    assert first["page"] is loading
    assert first["rejections"] == []
    browser.act.assert_not_called()
    sleep.assert_called_once_with(1.5)

    browser.fresh.return_value = True
    second = agent.command("tick")
    assert second["status"] == "rechecking"
    assert second["page"] is ready
    assert second["consecutive_rejections"] == 0
    assert sleep.call_count == 2
    assert len(second["blocked_rechecks"]) == 2

    browser.fresh.return_value = True
    choose.side_effect = None
    choose.return_value = decision("e1")
    result = agent.command("tick")
    assert result["status"] == "ready"
    assert choose.call_args.args[0] is ready
    browser.act.assert_called_once_with(ready["actions"][0], ready, text=None)
    assert len(result["history"]) == 1


def test_stale_terminal_decision_is_never_accepted_after_wait_allowance(run):
    agent, browser, _, sleep = run
    agent.command("tick")
    agent.command("tick")
    agent.command("predict")
    browser.fresh.return_value = False

    with pytest.raises(StalePage, match="Page changed since the decision"):
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})

    assert agent.state["status"] == "ready"
    assert agent.state["decision"] is None
    assert sleep.call_count == 2
    browser.act.assert_not_called()


def test_stale_done_remains_unconfirmed_without_blocked_wait(run):
    agent, browser, choose, sleep = run
    choose.return_value = decision("DONE")
    agent.command("predict")
    browser.fresh.return_value = False

    with pytest.raises(StalePage):
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})

    assert agent.state["status"] == "ready"
    assert agent.state["plan_index"] == 0
    sleep.assert_not_called()
    browser.act.assert_not_called()


@pytest.mark.parametrize("change", ["scroll", "content"])
def test_scroll_or_dom_updates_do_not_renew_the_allowance(run, change):
    agent, browser, _, sleep = run
    for i in range(3):
        updated = page(scroll=i * 100) if change == "scroll" else page(text=f"Loading {i}")
        browser.observe.return_value = updated
        agent.state["page"] = updated
        result = agent.command("tick")

    assert result["status"] == "blocked"
    assert sleep.call_count == 2
    browser.act.assert_not_called()


@pytest.mark.parametrize(
    "next_page",
    [page(url="https://example.test/cart"), page(document=2)],
)
def test_navigation_or_reload_gets_a_new_allowance(run, next_page):
    agent, browser, _, sleep = run
    agent.command("tick")
    agent.command("tick")
    agent.state["page"] = deepcopy(next_page)
    browser.observe.return_value = deepcopy(next_page)

    result = agent.command("tick")

    assert result["status"] == "rechecking"
    assert sleep.call_count == 3
    assert result["blocked_rechecks"][-1]["attempt"] == 1
    browser.act.assert_not_called()


def test_done_is_not_delayed(run):
    agent, browser, choose, sleep = run
    choose.return_value = decision("DONE")

    assert agent.command("tick")["status"] == "done"

    sleep.assert_not_called()
    browser.act.assert_not_called()
    assert agent.state["blocked_rechecks"] == []


def test_model_call_budget_is_not_extended(run, monkeypatch):
    agent, browser, choose, sleep = run
    monkeypatch.setattr(loop, "MAX_STEPS", 1)

    assert agent.command("tick")["status"] == "rechecking"
    assert agent.command("tick")["status"] == "blocked"

    assert len(agent.state["decisions"]) == 2
    assert choose.call_count == 2
    sleep.assert_called_once_with(1.5)
    browser.act.assert_not_called()


def test_no_progress_stop_does_not_retry_browser_actions(run):
    agent, browser, choose, sleep = run
    choose.return_value = decision("e1")

    for _ in range(3):
        result = agent.command("tick")

    assert result["status"] == "blocked"
    assert browser.act.call_count == 3
    sleep.assert_not_called()
    assert agent.state["blocked_rechecks"] == []


def test_interrupted_reobservation_still_consumes_one_recheck(run):
    agent, browser, _, sleep = run
    browser.observe.side_effect = [StalePage("Navigation interrupted observation"), page(), page()]

    assert agent.command("tick")["status"] == "ready"
    assert agent.command("tick")["status"] == "rechecking"
    assert agent.command("tick")["status"] == "blocked"

    assert len(agent.state["blocked_rechecks"]) == 2
    assert sleep.call_count == 2
    browser.act.assert_not_called()


def test_manual_mode_does_not_make_an_extra_prediction_inside_act(run):
    agent, browser, choose, sleep = run
    agent.command("predict")
    result = agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})

    assert result["status"] == "rechecking"
    assert result["decision"] is None
    choose.assert_called_once()
    sleep.assert_called_once_with(1.5)
    browser.act.assert_not_called()
