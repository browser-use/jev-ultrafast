"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import httpx
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


@pytest.fixture
def text_http(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://gateway.test/custom/v1")
    monkeypatch.setenv("TEXT_MODEL", "test-model")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    monkeypatch.setattr(model, "REASONING_CONTROLS", {})
    monkeypatch.setattr(model.time, "sleep", lambda _: None)
    calls = []
    replies = []

    def handle(request):
        calls.append((str(request.url), json.loads(request.content)))
        assert replies, "Unexpected HTTP attempt"
        status, body = replies.pop(0)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        yield calls, replies
    assert not replies, "Expected HTTP attempts were not made"


def text_reply():
    return 200, {"choices": [{"message": {"content": '{"text":"Zurich"}'}}]}


def rejection(field, status=400):
    return status, {"error": {"param": field, "code": "unknown_parameter", "message": "Rejected"}}


@pytest.mark.parametrize("accepted", range(4))
def test_reasoning_fallback_and_cached_control(text_http, accepted):
    calls, replies = text_http
    expected = [
        {"reasoning": {"enabled": False}},
        {"reasoning_effort": "none"},
        {"thinking": {"type": "disabled"}},
        {},
    ]
    replies.extend(rejection(next(iter(control))) for control in expected[:accepted])
    replies.extend([text_reply(), text_reply()])
    if accepted == 3:
        with pytest.warns(RuntimeWarning, match="Provider defaults apply"):
            value, info = model.field_text({"goal": "Fly to Zurich"})
        with pytest.warns(RuntimeWarning, match="Provider defaults apply"):
            model.field_text({"goal": "Fly to London"})
    else:
        value, info = model.field_text({"goal": "Fly to Zurich"})
        model.field_text({"goal": "Fly to London"})
    assert value == "Zurich"
    assert info["reasoning_attempts"] == accepted + 1
    assert info["reasoning_control"] == expected[accepted]
    assert len(calls) == accepted + 2
    for (_, body), control in zip(calls, [*expected[:accepted + 1], expected[accepted]], strict=True):
        assert {key: body[key] for key in ("reasoning", "reasoning_effort", "thinking") if key in body} == control
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 1024
    initial = {k: v for k, v in calls[0][1].items() if k != "reasoning"}
    for _, body in calls[:accepted + 1]:
        assert {k: v for k, v in body.items() if k not in {"reasoning", "reasoning_effort", "thinking"}} == initial


@pytest.mark.parametrize("setting", [None, "low", "medium", "high"])
def test_effort_fallback_preserves_setting(text_http, monkeypatch, setting):
    calls, replies = text_http
    if setting is None:
        monkeypatch.delenv("TEXT_MODEL_REASONING")
    else:
        monkeypatch.setenv("TEXT_MODEL_REASONING", setting)
    replies.extend([rejection("reasoning"), rejection("reasoning_effort"), text_reply()])
    with pytest.warns(RuntimeWarning, match=f"reasoning={setting or 'low'}"):
        model.field_text({})
    assert calls[0][1]["reasoning"] == {"effort": setting or "low"}
    assert calls[1][1]["reasoning_effort"] == (setting or "low")
    assert all("thinking" not in body for _, body in calls)
    assert not {"reasoning", "reasoning_effort", "thinking"} & calls[2][1].keys()


@pytest.mark.parametrize("detail", [
    {"message": "Unknown parameter: 'reasoning'."},
    {"message": "Unsupported value: 'reasoning.enabled' does not support false."},
    {"message": "'reasoning' is not supported with this model."},
    {"param": "reasoning.enabled", "code": "unsupported_value"},
    # PIN: verbatim api.openai.com rejection of a top-level reasoning_effort, seen
    # 2026-09-18 with gpt-4.1-mini; param and code are null, so only the message identifies it.
    {"message": "Unrecognized request argument supplied: reasoning", "param": None, "code": None},
])
def test_reasoning_validation_errors_allow_fallback(text_http, detail):
    calls, replies = text_http
    replies.extend([(422, {"error": detail}), text_reply()])
    model.field_text({})
    assert len(calls) == 2
    assert calls[1][1]["reasoning_effort"] == "none"


@pytest.mark.parametrize("status,body", [
    (401, {"error": {"message": "Unknown parameter: 'reasoning'."}}),
    (403, {"error": {"param": "reasoning", "code": "unsupported_parameter"}}),
    (404, {"error": {"code": "model_not_found"}}),
    (400, {"error": {"param": "max_tokens", "message": "Unknown parameter: 'reasoning'."}}),
    (400, {"error": {"message": "max_tokens is too small for reasoning"}}),
    (400, {"error": {"message": "Unknown parameter: 'reasoning_content'."}}),
    (400, {"error": {"message": "Context length exceeded", "code": "context_length_exceeded"}}),
    (400, {"error": {"param": "reasoning", "message": "Internal processing failed"}}),
    (400, {"error": {"message": None}}),
    (400, {"error": "Invalid request"}),
    (400, []),
    (400, "<html>Bad request</html>"),
    (500, {"error": {"param": "reasoning", "code": "unknown_parameter"}}),
])
def test_unrelated_or_ambiguous_errors_do_not_negotiate(text_http, status, body):
    calls, replies = text_http
    replies.append((status, body))
    with pytest.raises(model.ModelHTTPError) as error:
        model.field_text({})
    assert error.value.status == status
    assert len(calls) == 1
    assert not model.REASONING_CONTROLS


@pytest.mark.parametrize("status", [429, 503, 529])
def test_transient_retries_do_not_change_reasoning(text_http, status):
    calls, replies = text_http
    replies.extend([rejection("reasoning", status)] * 3)
    with pytest.raises(model.ModelHTTPError):
        model.field_text({})
    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]


@pytest.mark.parametrize("name,value", [
    ("TEXT_MODEL_BASE_URL", "https://another.test/proxy"),
    ("TEXT_MODEL", "another-model"),
    ("TEXT_MODEL_REASONING", "high"),
])
def test_reasoning_cache_isolated_by_configuration(text_http, monkeypatch, name, value):
    calls, replies = text_http
    replies.extend([rejection("reasoning"), text_reply(), text_reply()])
    model.field_text({})
    monkeypatch.setenv(name, value)
    model.field_text({})
    assert len(calls) == 3
    assert calls[-1][1]["reasoning"] == ({"effort": "high"} if name == "TEXT_MODEL_REASONING" else {"enabled": False})


def test_cached_control_can_be_rejected_later(text_http):
    calls, replies = text_http
    replies.extend([rejection("reasoning"), text_reply(), rejection("reasoning_effort"), text_reply()])
    model.field_text({})
    model.field_text({})
    assert len(calls) == 4
    assert calls[-1][1]["thinking"] == {"type": "disabled"}


def test_omission_failure_propagates(text_http):
    calls, replies = text_http
    replies.extend(rejection(field) for field in ("reasoning", "reasoning_effort", "thinking", "max_tokens"))
    with pytest.warns(RuntimeWarning, match="omitting the control"):
        with pytest.raises(model.ModelHTTPError):
            model.field_text({})
    assert len(calls) == 4
    assert not model.REASONING_CONTROLS


def test_invalid_json_output_does_not_negotiate_or_cache(text_http):
    calls, replies = text_http
    replies.extend([rejection("reasoning"), (200, {"choices": [{"message": {"content": "not JSON"}}]})])
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({})
    assert len(calls) == 2
    assert not model.REASONING_CONTROLS


def test_unknown_reasoning_setting_fails_before_the_request(text_http, monkeypatch):
    calls, _ = text_http
    monkeypatch.setenv("TEXT_MODEL_REASONING", "disabled")
    with pytest.raises(ValueError, match="TEXT_MODEL_REASONING"):
        model.field_text({})
    assert not calls


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
