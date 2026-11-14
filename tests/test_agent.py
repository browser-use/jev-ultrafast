"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model, providers
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


class FakeDecisionProvider:
    model = "fake-decision"

    def __init__(self, response=None, error=None):
        self.requests = []
        self.response = response
        self.error = error

    def decide(self, request):
        self.requests.append(deepcopy(request))
        if self.error:
            raise self.error
        if self.response:
            return self.response(request)
        questions = request["questions"]
        return {
            "model": self.model,
            "usage": {"input_tokens": 1},
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(questions["type_text_target"]["criteria"], "1"),
            },
        }


class FakeTextProvider:
    model = "fake-text"

    def __init__(self, content='{"text":"book"}', error=None):
        self.requests = []
        self.content = content
        self.error = error

    def generate(self, request):
        self.requests.append(deepcopy(request))
        if self.error:
            raise self.error
        return {
            "model": self.model,
            "usage": {"input_tokens": 1},
            "choices": [{"message": {"content": self.content}}],
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
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
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


def test_injected_providers_need_no_credentials_or_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(model, "post_json", Mock(side_effect=AssertionError("network called")))
    monkeypatch.setattr(providers, "post_json", Mock(side_effect=AssertionError("network called")))
    decisions = FakeDecisionProvider()
    text = FakeTextProvider()

    actual = model.choose(page(), "Find a book", [], provider=decisions)
    value, helper = model.field_text({"goal": "Find a book"}, provider=text)

    assert actual["choice"] == "e1" and actual["model"] == "fake-decision"
    assert actual["usage"] == {"input_tokens": 1}
    assert value == "book" and helper["model"] == "fake-text"
    assert helper["usage"] == {"input_tokens": 1}
    assert decisions.requests[0]["model"] == "fake-decision"
    assert text.requests[0]["model"] == "fake-text"


def test_injected_decision_still_uses_core_validation(monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(side_effect=AssertionError("network called")))
    monkeypatch.setattr(providers, "post_json", Mock(side_effect=AssertionError("network called")))

    def invalid(request):
        return {
            "model": "fake",
            "answers": {
                "operation": choice(request["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": choice(["1", "2", "invented"], "invented"),
            },
        }

    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [], provider=FakeDecisionProvider(invalid))


def test_injected_text_still_uses_core_validation(monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(side_effect=AssertionError("network called")))
    monkeypatch.setattr(providers, "post_json", Mock(side_effect=AssertionError("network called")))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a book"}, provider=FakeTextProvider('{"text":null}'))


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


def test_agent_routes_both_injected_providers_without_network(runner, monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(side_effect=AssertionError("network called")))
    runner.decision_provider = FakeDecisionProvider()
    runner.text_provider = FakeTextProvider()
    runner.state["decision"] = None
    runner.state["status"] = "ready"

    runner.command("predict")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    runner.state["browser"].act.assert_called_once()
    assert runner.state["browser"].act.call_args.kwargs["text"] == "book"
    assert runner.state["decisions"][0]["model"] == "fake-decision"
    assert runner.state["text_calls"][0]["model"] == "fake-text"


def test_agent_can_inject_only_decisions_and_use_default_text(runner, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"book"}'}}]})
    provider_post = Mock(side_effect=AssertionError("network called"))
    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setattr(providers, "post_json", provider_post)
    runner.decision_provider = FakeDecisionProvider()
    runner.state["decision"] = None
    runner.state["status"] = "ready"

    runner.command("predict")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert post.call_count == 1
    assert post.call_args.args[0].endswith("/chat/completions")
    provider_post.assert_not_called()
    assert runner.state["browser"].act.call_args.kwargs["text"] == "book"


def test_agent_can_inject_only_text_and_use_default_decisions(runner, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")

    def post(_url, _key, body):
        questions = body["questions"]
        return {
            "model": "default-decision",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(questions["type_text_target"]["criteria"], "1"),
            },
        }

    model_post = Mock(side_effect=post)
    provider_post = Mock(side_effect=AssertionError("network called"))
    monkeypatch.setattr(model, "post_json", model_post)
    monkeypatch.setattr(providers, "post_json", provider_post)
    runner.text_provider = FakeTextProvider()
    runner.state["decision"] = None
    runner.state["status"] = "ready"

    runner.command("predict")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert model_post.call_count == 1
    provider_post.assert_not_called()
    assert runner.state["decisions"][0]["model"] == "default-decision"
    assert runner.state["browser"].act.call_args.kwargs["text"] == "book"


@pytest.mark.parametrize("provider_name", ["decision_provider", "text_provider"])
def test_agent_constructor_keeps_provider_injection_independent(monkeypatch, provider_name):
    browser = Mock(observe=Mock(return_value=page()))
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    provider = FakeDecisionProvider() if provider_name == "decision_provider" else FakeTextProvider()

    agent = loop.Agent("https://example.test", "Find a book", **{provider_name: provider})

    assert getattr(agent, provider_name) is provider
    other = "text_provider" if provider_name == "decision_provider" else "decision_provider"
    assert getattr(agent, other) is None
    agent.close()


@pytest.mark.parametrize("kind", ["decision", "text"])
def test_provider_errors_stop_before_browser_mutation(runner, kind):
    error = RuntimeError("provider stopped")
    if kind == "decision":
        runner.decision_provider = FakeDecisionProvider(error=error)
        runner.state["decision"] = None
        runner.state["status"] = "ready"
    else:
        runner.text_provider = FakeTextProvider(error=error)

    with pytest.raises(RuntimeError, match="provider stopped"):
        if kind == "decision":
            runner.command("predict")
        else:
            runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()


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
