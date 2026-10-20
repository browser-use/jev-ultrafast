"""Text-generation failures stay diagnosable and never bypass validation. No model calls."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import model

CONTEXT = {"goal": "Search for a notebook", "field": {"label": "Search", "role": "textbox", "value": ""}}


def response(content='{"text":"notebook"}', finish="stop", **message):
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content, **message}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@pytest.fixture
def post(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://example.test/v1")
    mock = Mock()
    monkeypatch.setattr(model, "post_json", mock)
    return mock


@pytest.mark.parametrize(
    ("first", "reason"),
    [
        (response(content=""), "empty_content"),
        (response(content=None), "empty_content"),
        (response(content="```json\n{\"text\":\"notebook\"}\n```"), "invalid_json"),
        (response(content='{"text":"notebook","extra":true}'), "invalid_schema"),
        (response(content='{"text":123}'), "invalid_value"),
        (response(content='{"text":"   "}'), "invalid_value"),
        (response(content=json.dumps({"text": "x" * 2001})), "invalid_value"),
        (response(finish="length"), "truncated"),  # Even valid JSON must not bypass a truncation warning.
        ({"choices": []}, "invalid_response"),
        ({"choices": {"unexpected": "value"}}, "invalid_response"),
        ({"choices": [None]}, "invalid_response"),
        ({"choices": [{"message": None}]}, "invalid_response"),
        (None, "invalid_response"),
    ],
)
def test_one_regeneration_then_validated_value(post, first, reason):
    post.side_effect = [first, response()]

    value, metadata = model.field_text(CONTEXT)

    assert value == "notebook"
    assert post.call_count == 2
    assert [r["status"] for r in metadata["attempts"]] == [reason, "ok"]
    first_body, retry_body = [call.args[2] for call in post.call_args_list]
    assert first_body["max_tokens"] == 1024
    assert retry_body["max_tokens"] == (2048 if reason == "truncated" else 1024)
    assert first_body["messages"][1] == retry_body["messages"][1]
    assert json.loads(retry_body["messages"][1]["content"]) == CONTEXT
    assert len(retry_body["messages"]) == 2


def test_attempt_budget_and_diagnostics_exclude_failed_text(post):
    post.return_value = response(content="private invalid response", reasoning="private reasoning")

    with pytest.raises(model.TextHelperError, match="malformed JSON") as caught:
        model.field_text(CONTEXT)

    assert post.call_count == 2
    error = caught.value
    assert error.reason == "invalid_json"
    assert len(error.metadata["attempts"]) == 2
    assert error.metadata["usage"] == {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240}
    assert error.metadata["attempts"][0]["content_length"] == len("private invalid response")
    assert "private" not in str(error.metadata)
    assert "private" not in str(error)


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (response(content='{"text":null}'), "missing_value"),
        (response(content=None, refusal="private refusal"), "refused"),
        (response(content="", finish="content_filter"), "refused"),
    ],
)
def test_missing_information_and_refusal_do_not_retry(post, result, reason):
    post.return_value = result

    with pytest.raises(model.TextHelperError, match="nothing typed") as caught:
        model.field_text(CONTEXT)

    post.assert_called_once()
    assert caught.value.reason == reason
    assert caught.value.metadata["attempts"][0]["status"] == reason
    assert "private refusal" not in str(caught.value)


def test_successful_call_needs_no_retry_and_keeps_exact_field_value(post):
    post.return_value = response(content='{"text":"000000"}')

    value, metadata = model.field_text(CONTEXT)

    assert value == "000000"
    post.assert_called_once()
    assert metadata["usage"]["total_tokens"] == 120
    assert [r["status"] for r in metadata["attempts"]] == ["ok"]
