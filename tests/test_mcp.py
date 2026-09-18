"""Offline contracts for the goal-level MCP server. No paid APIs, no real browser."""

import asyncio
import json
import time
from unittest.mock import Mock

import pytest

pytest.importorskip("mcp", reason="MCP server needs the optional 'mcp' extra")

from jev_ultrafast import agent as loop  # noqa: E402
from jev_ultrafast import mcp_server as server  # noqa: E402
from jev_ultrafast.browser import fingerprint  # noqa: E402
from jev_ultrafast.questions import MAX_STEPS  # noqa: E402

# Anything that would let a caller name a control instead of an outcome.
FORBIDDEN = {"selector", "css", "xpath", "index", "element", "element_index", "node",
             "target", "x", "y", "coordinate", "script", "js", "javascript", "code", "expression"}


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def build(url, goal, *, max_actions=None, budget_ms=None):
    """A real Agent with a mocked browser, so summary/run/command stay under test."""
    agent = loop.Agent.__new__(loop.Agent)
    agent.screenshots = False
    agent.pending_text = None
    agent.record_dir = None
    agent.browser = Mock(
        fresh=Mock(return_value=True), observe=Mock(side_effect=lambda **_kwargs: page()), close=Mock()
    )
    agent.state = dict(
        browser=agent.browser, goal=goal, page=page(), decision=None, history=[], status="ready",
        plan=[goal], plan_index=0, decisions=[], text_calls=[], elapsed_ms=0, started_at=None,
        record=False, max_actions=max_actions or MAX_STEPS, budget_ms=budget_ms, stopped_reason=None,
    )
    return agent


def decides(choice):
    """A stand-in policy that always returns the same valid choice."""
    return lambda *_args: {
        "choice": choice, "operation": "CLICK", "target": "1", "confidence": 1.0,
        "probabilities": {choice: 1.0}, "latency_ms": 1, "usage": {},
    }


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Every test starts with credentials present and no run open."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(server, "AGENT", None)
    yield
    monkeypatch.setattr(server, "AGENT", None)


def call(tool, **kwargs):
    return json.loads(tool(**kwargs))


def schemas():
    return {t.name: t.input_schema for t in asyncio.run(server.SERVER.list_tools())}


def test_every_tool_is_registered():
    assert set(schemas()) == {"jev_run", "jev_start", "jev_step", "jev_observe", "jev_close"}


def test_no_tool_accepts_a_selector_index_or_coordinate():
    """The library's core guarantee has to survive the MCP boundary."""
    for name, schema in schemas().items():
        named = set(schema.get("properties", {}))
        assert not named & FORBIDDEN, f"{name} exposes control-level parameters: {named & FORBIDDEN}"


def test_advancing_a_run_takes_no_arguments():
    """A caller cannot steer a step; only Jev picks the operation and the target."""
    for name in ("jev_step", "jev_observe", "jev_close"):
        assert schemas()[name].get("properties", {}) == {}


def test_goal_tools_require_only_a_url_and_a_goal():
    for name in ("jev_run", "jev_start"):
        assert schemas()[name]["required"] == ["url", "goal"]


def test_missing_credential_fails_before_opening_a_browser(monkeypatch):
    built = Mock()
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(server, "Agent", built)
    assert "TYPESAFE_API_KEY" in call(server.jev_run, url="https://example.test/", goal="Find a book")["error"]
    built.assert_not_called()


def test_a_failure_is_returned_as_json_not_raised(monkeypatch):
    monkeypatch.setattr(server, "Agent", Mock(side_effect=RuntimeError("Chrome is not connected")))
    assert call(server.jev_run, url="https://example.test/", goal="Find a book")["error"] == "Chrome is not connected"


def test_step_without_a_run_explains_itself():
    assert "No run is open" in call(server.jev_step)["error"]


def test_a_second_concurrent_call_is_refused_not_queued():
    """One browser serves one run; a client must not stack mutations behind each other."""
    assert server.LOCK.acquire(blocking=False)
    try:
        assert "already running" in call(server.jev_step)["error"]
    finally:
        server.LOCK.release()


def test_run_reports_its_stopping_condition_and_never_claims_success(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("DONE"))
    result = call(server.jev_run, url="https://example.test/", goal="Find a book")
    assert result["status"] == "done"
    assert result["stopped_reason"] == "model_done"
    # DONE is the model's choice. Only an independent check of `page` can call it a success.
    assert result["verified"] is None
    assert result["error"] is None
    assert result["page"]["url"] == "https://example.test/"


def test_run_keeps_the_steps_it_executed_when_it_fails(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("e3"))
    result = call(server.jev_run, url="https://example.test/", goal="Find a book", max_actions=2)
    assert result["error"] and "2-action" in result["error"]
    assert result["actions"] == 2 and len(result["steps"]) == 2


def test_step_advances_exactly_one_decision(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("e3"))
    call(server.jev_start, url="https://example.test/", goal="Find a book")
    first = call(server.jev_step)
    assert [step["action"] for step in first["executed"]] == ["Go"]
    assert first["actions"] == 1
    assert call(server.jev_step)["actions"] == 2


def test_step_after_a_run_stops_does_not_act_again(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("DONE"))
    call(server.jev_run, url="https://example.test/", goal="Find a book")
    result = call(server.jev_step)
    assert result["executed"] == [] and "stopped" in result["note"]


def test_observe_reads_without_deciding(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    chooser = Mock(side_effect=AssertionError("observe must not ask for a decision"))
    call(server.jev_start, url="https://example.test/", goal="Find a book")
    monkeypatch.setattr(loop, "choose", chooser)
    result = call(server.jev_observe)
    assert result["actions"] == 0 and result["decisions"] == 0
    assert [e["label"] for e in result["page"]["elements"]] == ["Search", "Go"]
    chooser.assert_not_called()


def test_close_returns_the_final_summary_and_releases_the_tab(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    call(server.jev_start, url="https://example.test/", goal="Find a book")
    browser = server.AGENT.browser
    assert call(server.jev_close)["closed"] is True
    browser.close.assert_called_once()
    assert server.AGENT is None
    assert call(server.jev_close)["closed"] is False


def test_tools_never_write_to_stdout(monkeypatch, capsys):
    """Stray stdout corrupts the JSON-RPC stream, so the server redirects it to stderr."""

    def noisy(*_args, **_kwargs):
        print("connecting to Chrome")
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(server, "Agent", noisy)
    assert call(server.jev_run, url="https://example.test/", goal="Find a book")["error"] == "daemon unavailable"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "connecting to Chrome" in captured.err


def test_page_text_is_truncated(monkeypatch):
    long_page = page()
    long_page["text"] = "x" * (server.TEXT_LIMIT + 500)
    monkeypatch.setattr(server, "Agent", build)
    call(server.jev_start, url="https://example.test/", goal="Find a book")
    server.AGENT.browser.observe = Mock(return_value=long_page)
    assert len(call(server.jev_observe)["page"]["text"]) == server.TEXT_LIMIT


def test_a_forgotten_budget_still_ends_the_run(monkeypatch):
    seen = {}

    def record(url, goal, *, max_actions=None, budget_ms=None):
        seen.update(max_actions=max_actions, budget_ms=budget_ms)
        return build(url, goal, max_actions=max_actions, budget_ms=budget_ms)

    monkeypatch.setattr(server, "Agent", record)
    monkeypatch.setattr(loop, "choose", decides("DONE"))
    call(server.jev_run, url="https://example.test/", goal="Find a book")
    assert seen["budget_ms"] == server.DEFAULT_BUDGET_MS


def test_time_budget_stops_a_run_without_spending_a_model_call(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    chooser = Mock(side_effect=AssertionError("the budget must be checked before the model call"))
    monkeypatch.setattr(loop, "choose", chooser)
    agent = build("https://example.test/", "Find a book", budget_ms=5)
    agent.state["started_at"] = time.perf_counter() - 1
    monkeypatch.setattr(server, "AGENT", agent)
    result = call(server.jev_step)
    assert result["status"] == "blocked" and result["stopped_reason"] == "time_budget"
    assert result["actions"] == 0
    chooser.assert_not_called()


def test_an_explicit_zero_timeout_is_not_replaced_by_the_default():
    """`timeout_ms or DEFAULT` would turn a client's explicit 0 into two minutes."""
    assert server.budget(None) == server.DEFAULT_BUDGET_MS
    assert server.budget(0) == 0
    assert server.budget(5000) == 5000


def test_a_zero_timeout_reaches_the_agent_and_is_refused(monkeypatch):
    monkeypatch.setattr(server, "Agent", loop.Agent)
    monkeypatch.setattr(loop, "Browser", Mock(side_effect=AssertionError("must not open a tab")))
    result = call(server.jev_run, url="https://example.test/", goal="Find a book", timeout_ms=0)
    assert "budget_ms" in result["error"]


def test_step_reports_a_budget_stop_with_the_full_summary(monkeypatch):
    """A budget stop is an outcome, not a lost call: jev_step keeps what jev_run would return."""
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("e3"))
    call(server.jev_start, url="https://example.test/", goal="Find a book", max_actions=1)
    call(server.jev_step)
    stopped = call(server.jev_step)
    assert "1-action" in stopped["error"]
    assert stopped["status"] == "blocked" and stopped["stopped_reason"] == "action_budget"
    assert stopped["actions"] == 1 and stopped["page"]["url"] == "https://example.test/"


def test_a_successful_step_reports_no_error(monkeypatch):
    monkeypatch.setattr(server, "Agent", build)
    monkeypatch.setattr(loop, "choose", decides("e3"))
    call(server.jev_start, url="https://example.test/", goal="Find a book")
    assert call(server.jev_step)["error"] is None

