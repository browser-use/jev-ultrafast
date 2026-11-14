"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


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


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid SemIf"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_semif_two_stage_decision_and_single_candidate_bypass(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        options = [opt["id"] for opt in body["options"]]
        return {
            "model": "semif-test",
            "decision": "TYPE_TEXT",
            "confidence": 0.99,
            "probabilities": {opt: (0.99 if opt == "TYPE_TEXT" else 0.01 / (len(options) - 1)) for opt in options},
        }

    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    # Since TYPE_TEXT has only 1 target ("1"), it bypasses second model call
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert d["confidence"] == 0.99
    # Provenance assertions
    assert d["backend"] == "semif"
    assert d["model"] == "semif-test"
    # Combined decision uses model for operation and deterministic bypass for target -> mixed
    assert d["decision_source"] == "mixed"
    assert isinstance(d["question_spec_hash"], str) and len(d["question_spec_hash"]) == 16
    assert d["provenance"]["stages"]["target"]["decision_source"] == "deterministic"
    assert d["provenance"]["stages"]["target"]["model_version"] is None
    assert d["provenance"]["stages"]["operation"]["decision_source"] == "model"


def test_semif_two_stage_decision_calls_second_stage_for_multiple_targets(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        options = [opt["id"] for opt in body["options"]]
        if len(calls) == 1:
            # Operation decision
            return {
                "model": "semif-test",
                "version": "v1.0",
                "decision": "CLICK",
                "confidence": 0.95,
                "probabilities": {opt: (0.95 if opt == "CLICK" else 0.05 / (len(options) - 1)) for opt in options},
            }
        else:
            # Target decision (options: ["1", "2"])
            return {
                "model": {"name": "semif-test-target", "version": "v2.1"},
                "decision": "2",
                "confidence": 0.92,
                "probabilities": {"1": 0.08, "2": 0.92},
            }

    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 2
    assert d["operation"] == "CLICK" and d["target"] == "2" and d["choice"] == "e3"
    assert d["choice"] == "e3"
    assert d["backend"] == "semif"
    assert d["decision_source"] == "model"
    assert d["provenance"]["stages"]["target"]["decision_source"] == "model"
    assert d["provenance"]["stages"]["target"]["model"] == "semif-test-target"
    assert d["provenance"]["stages"]["target"]["model_version"] == "v2.1"
    assert d["provenance"]["stages"]["operation"]["model_version"] == "v1.0"


def test_click_rejects_invalid_target_decision(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        if len(calls) == 1:
            options = [opt["id"] for opt in body["options"]]
            return {
                "model": "semif-test",
                "decision": "CLICK",
                "confidence": 1.0,
                "probabilities": {opt: float(opt == "CLICK") for opt in options},
            }
        return {
            "model": "semif-test",
            "decision": "999",
            "confidence": 1.0,
            "probabilities": {"1": 0.0, "2": 0.0, "999": 1.0},
        }

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid SemIf"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        if len(calls) == 1:
            options = [opt["id"] for opt in body["options"]]
            return {
                "model": "semif-test",
                "decision": "CLICK",
                "confidence": 1.0,
                "probabilities": {opt: float(opt == "CLICK") for opt in options},
            }
        # Check target request body
        target_state = body["state"]
        first_elem = target_state["candidate_elements"][0]
        assert first_elem["checked"] == "true"
        assert first_elem["selected"] is False
        assert "Rules:" in body["question"]
        return {
            "model": "semif-test",
            "decision": "3",
            "confidence": 1.0,
            "probabilities": {"1": 0.0, "2": 0.0, "3": 1.0},
        }

    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_mcp_neutral_tool_names_and_provenance(monkeypatch):
    import scripts.jev_mcp as mcp

    # 1. tools/list exposes neutral tool names
    list_res = mcp.handle_request({"method": "tools/list", "id": 1})
    tool_names = [t["name"] for t in list_res["result"]["tools"]]
    assert "open_browse" in tool_names
    assert "open_decide" in tool_names
    assert "jev_browse" in tool_names
    assert "jev_decide" in tool_names

    # Mock model.post_json for decide
    def post(_url, _key, body):
        options = [opt["id"] for opt in body["options"]]
        return {
            "model": "semif-local-test",
            "decision": "TYPE_TEXT",
            "confidence": 0.99,
            "probabilities": {opt: (0.99 if opt == "TYPE_TEXT" else 0.01 / (len(options) - 1)) for opt in options},
        }

    monkeypatch.setattr(model, "post_json", post)

    # 2. Call via neutral tool name open_decide
    decide_call = {
        "method": "tools/call",
        "id": 2,
        "params": {
            "name": "open_decide",
            "arguments": {"page": page(), "goal": "Find a book"},
        },
    }
    resp = mcp.handle_request(decide_call)
    assert "result" in resp and not resp["result"].get("isError")
    content = json.loads(resp["result"]["content"][0]["text"])
    assert content["backend"] == "semif"
    assert content["model"] == "semif-local-test"
    assert content["decision_source"] == "mixed"
    assert content["provenance"]["stages"]["target"]["decision_source"] == "deterministic"
    assert "question_spec_hash" in content
    assert content["calibration_surface"] == "semif_local"

    # 3. Call via backwards-compatible name jev_decide
    decide_call_legacy = {
        "method": "tools/call",
        "id": 3,
        "params": {
            "name": "jev_decide",
            "arguments": {"page": page(), "goal": "Find a book"},
        },
    }
    resp_legacy = mcp.handle_request(decide_call_legacy)
    assert "result" in resp_legacy and not resp_legacy["result"].get("isError")
    content_legacy = json.loads(resp_legacy["result"]["content"][0]["text"])
    assert content_legacy["backend"] == "semif"


def test_mcp_browse_provenance_and_decision_source(monkeypatch):
    import scripts.jev_mcp as mcp

    # Mock Agent in scripts.jev_mcp
    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self):
            yield {
                "status": "done",
                "elapsed_ms": 120,
                "page": {"url": "https://example.test", "title": "Done", "text": "OK"},
                "history": [
                    {
                        "step": 1,
                        "action": "Search",
                        "operation": "TYPE_TEXT",
                        "choice": "e1",
                        "text": "test",
                        "confidence": 0.99,
                        "elapsed_ms": 120,
                        "backend": "semif",
                        "model": "semif-model",
                        "question_spec_hash": "abcd1234ef567890",
                        "decision_source": "deterministic",
                        "provenance": {"backend": "semif", "decision_source": "deterministic"},
                    }
                ],
            }

    monkeypatch.setattr(mcp, "Agent", FakeAgent)

    res = mcp.execute_browse("https://example.test", "Do something", max_steps=5)
    assert res["status"] == "done"
    assert res["backend"] == "semif"
    assert res["calibration_surface"] == "semif_local"
    assert res["provenance"]["backend"] == "semif"
    assert len(res["history"]) == 1
    assert res["history"][0]["backend"] == "semif"
    assert res["history"][0]["decision_source"] == "deterministic"
    assert res["history"][0]["question_spec_hash"] == "abcd1234ef567890"
