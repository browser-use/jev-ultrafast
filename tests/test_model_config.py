"""Configuration handling for the TYPE_TEXT helper. No network, no paid calls.

Covers two traps that cost real debugging time:

1. `reasoning` is a vendor-specific field. `none` sends `{"reasoning": {"enabled": false}}`, which
   several OpenAI-compatible endpoints reject outright, so there was no way to simply not send it:
   Fireworks -> "Extra inputs are not permitted", Groq -> "property 'reasoning' is unsupported",
   Gemini -> 'Unknown name "reasoning"'. `omit` is that way, and this pins it.
2. `TEXT_MODEL_BASE_URL` and `TEXT_MODEL` default to DeepSeek as a pair. Setting the model without
   the endpoint used to leave the key pointed at api.deepseek.com — but README.md says that key is
   an OpenRouter key, so the default silently shipped it to a different vendor.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from jev_ultrafast import model


def _capture(monkeypatch):
    """Patch post_json and return the call recorder. Content is a valid text-helper reply."""
    post = Mock(return_value={"choices": [{"message": {"content": '{"text": "Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    return post


def _clear(monkeypatch):
    for name in ("TEXT_MODEL", "TEXT_MODEL_BASE_URL", "TEXT_MODEL_REASONING"):
        monkeypatch.delenv(name, raising=False)


def _body(post):
    return post.call_args.args[2]


def test_reasoning_omitted_leaves_the_field_out(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("TEXT_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "omit")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert "reasoning" not in body
    assert "thinking" not in body
    # the request is otherwise unchanged
    assert body["model"] == "gemini-2.5-flash-lite"
    assert body["response_format"] == {"type": "json_object"}
    assert post.call_args.args[0] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def test_reasoning_none_still_sends_the_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert _body(post)["reasoning"] == {"enabled": False}


def test_reasoning_defaults_are_unchanged_for_non_deepseek(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert _body(post)["reasoning"] == {"effort": "low"}


def test_reasoning_omit_is_case_and_space_insensitive(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_REASONING", " OMIT ")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert "reasoning" not in _body(post)


def test_unconfigured_endpoint_and_model_keep_the_paired_deepseek_defaults(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert post.call_args.args[0] == "https://api.deepseek.com/v1/chat/completions"
    body = _body(post)
    assert body["model"] == "deepseek-chat"
    assert body["thinking"] == {"type": "disabled"}


def test_model_without_base_url_fails_instead_of_using_the_wrong_vendor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "inception/mercury-2.5")
    post = _capture(monkeypatch)
    with pytest.raises(ValueError, match="TEXT_MODEL_BASE_URL"):
        model.field_text({"goal": "Find a flight"})
    # and nothing was sent anywhere
    post.assert_not_called()


def test_explicit_deepseek_model_without_base_url_is_not_treated_as_misconfigured(monkeypatch):
    """The paired default is a legitimate config, so spelling it out must keep working."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "deepseek-chat")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert post.call_args.args[0] == "https://api.deepseek.com/v1/chat/completions"
