"""Offline recovery contracts: fresh generation only, never repaired text or browser retries."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import agent, model


def reply(content, **extra):
    return {"choices": [{"message": {"content": content}}], **extra}


VALID = reply('{"text":"Provider value"}')
MALFORMED = [
    None, {}, [], {"choices": []}, {"choices": None}, {"choices": [None]},
    {"choices": [{}]}, {"choices": [{"message": []}]},
    reply(None), reply({"text": "not encoded"}), reply("Thinking: guessed value"),
    reply('```json\n{"text":"guessed value"}\n```'), reply('{"text":"truncated'),
    reply("null"), reply("[]"), reply('"string"'), reply("123"), reply("{}"),
    reply('{"text":null}'), reply('{"text":123}'), reply('{"text":true}'),
    reply('{"text":[]}'), reply('{"text":""}'), reply('{"text":" \\n\\t"}'),
    reply('{"text":"value","extra":true}'), reply(json.dumps({"text": "x" * 2001})),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "offline-test")
    monkeypatch.setattr(model.CLIENT, "post", Mock(side_effect=AssertionError("Network forbidden")))


@pytest.mark.parametrize("malformed", MALFORMED)
def test_one_malformed_reply_gets_one_identical_new_generation(monkeypatch, malformed):
    context = {"goal": 'Enter "not a fallback"', "page": {"text": "Observed"}}
    original = deepcopy(context)
    requests = []
    responses = iter([malformed, VALID])

    def post(*args):
        requests.append(deepcopy(args))
        return next(responses)

    monkeypatch.setattr(model, "post_json", post)
    value, metadata = model.field_text(context)
    assert value == "Provider value"
    assert len(requests) == metadata["attempts"] == 2
    assert requests[0] == requests[1]
    assert json.loads(requests[0][2]["messages"][1]["content"]) == original
    assert context == original


@pytest.mark.parametrize("malformed", MALFORMED)
def test_second_malformed_reply_fails_closed_without_third_request(monkeypatch, malformed):
    post = Mock(side_effect=[reply("invalid first"), malformed, VALID])
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": 'Enter "never guess this"'})
    assert post.call_count == 2


@pytest.mark.parametrize("value", ["Provider value", " x ", "x" * 2000, "São Paulo 🏙"])
def test_successful_fast_path_is_one_request_preserving_exact_value(monkeypatch, value):
    usage = {"total_tokens": 12}
    post = Mock(return_value=reply(json.dumps({"text": value}), usage=usage))
    monkeypatch.setattr(model, "post_json", post)
    result, metadata = model.field_text({"goal": "Enter a value"})
    assert result == value
    assert post.call_count == metadata["attempts"] == 1
    assert metadata["usage"] == usage


def test_recovery_latency_includes_both_requests_and_keeps_each_usage(monkeypatch):
    clock = [100.0]
    usages = [{"total_tokens": 10}, {"total_tokens": 20}]
    responses = iter([reply("invalid", usage=usages[0]), reply('{"text":"valid"}', usage=usages[1])])

    def post(*_args):
        clock[0] += 0.125
        return next(responses)

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setattr(model.time, "perf_counter", lambda: clock[0])
    _, metadata = model.field_text({})
    assert metadata["attempts"] == 2
    assert metadata["latency_ms"] == 250
    assert metadata["usage"] == usages[-1]  # Preserve existing successful-response usage semantics.
    assert metadata["usage_by_attempt"] == usages


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_errors_never_regenerate(monkeypatch, status):
    post = Mock(return_value=httpx.Response(status, request=httpx.Request("POST", "https://example.test")))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        model.field_text({})
    assert post.call_count == 1


@pytest.mark.parametrize("error", [ValueError("bug"), TypeError("bug"), KeyError("bug"),
                                  IndexError("bug"), RuntimeError("bug"), AssertionError("bug")])
@pytest.mark.parametrize("after_malformed", [False, True])
def test_provider_call_bugs_are_not_treated_as_malformed_payloads(monkeypatch, error, after_malformed):
    replies = [reply("invalid"), error] if after_malformed else [error]
    post = Mock(side_effect=replies)
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(type(error)) as raised:
        model.field_text({})
    assert raised.value is error
    assert post.call_count == len(replies)


@pytest.mark.parametrize("recover", [True, False])
def test_invalid_json_response_envelope_has_same_bound(monkeypatch, recover):
    malformed = httpx.Response(200, content=b"not JSON")
    valid = httpx.Response(200, json=VALID)
    post = Mock(side_effect=[malformed, valid if recover else malformed, valid])
    monkeypatch.setattr(model.CLIENT, "post", post)
    if recover:
        value, metadata = model.field_text({})
        assert value == "Provider value"
        assert metadata["attempts"] == 2
    else:
        with pytest.raises(ValueError, match="nothing typed"):
            model.field_text({})
    assert post.call_count == 2


@pytest.mark.parametrize("recover", [True, False])
def test_agent_never_mutates_browser_during_text_recovery(monkeypatch, recover):
    action = {"id": "e1", "kind": "fill", "label": "Field", "node": 1}
    page = {"fingerprint": "observed", "title": "Form", "text": "Field",
            "url": "https://example.test", "actions": [action]}
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page))
    runner = agent.Agent.__new__(agent.Agent)
    runner.pending_text = None
    runner.screenshots = False
    runner.state = dict(browser=browser, page=page, goal="Enter a value", history=[], decisions=[],
                        text_calls=[], status="predicted", started_at=time.perf_counter(), record=False,
                        decision={"choice": "e1", "operation": "TYPE_TEXT", "target": "1",
                                  "confidence": 1, "probabilities": {"e1": 1}, "latency_ms": 0, "usage": {}})
    responses = iter([reply("invalid"), VALID if recover else reply("invalid again")])

    def post(*_args):
        browser.act.assert_not_called()
        browser.observe.assert_not_called()
        return next(responses)

    helper = Mock(side_effect=post)
    monkeypatch.setattr(model, "post_json", helper)
    if recover:
        runner.command("act", {"fingerprint": "observed"})
        browser.act.assert_called_once_with(action, page, text="Provider value")
        assert runner.state["text_calls"][0]["attempts"] == 2
    else:
        with pytest.raises(ValueError, match="nothing typed"):
            runner.command("act", {"fingerprint": "observed"})
        browser.act.assert_not_called()
        assert runner.pending_text is None
        assert runner.state["history"] == []
    assert helper.call_count == 2
    assert runner.state["decision"] is None
