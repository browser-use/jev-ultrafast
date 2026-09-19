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


# --- review follow-ups -----------------------------------------------------------------------


def test_deepseek_reasoner_keeps_the_paired_defaults(monkeypatch):
    """Only `deepseek-chat` was exempted before, so `deepseek-reasoner` — equally first-party —
    started raising for a config that was never a wrong-vendor setup."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "deepseek-reasoner")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert post.call_args.args[0] == "https://api.deepseek.com/v1/chat/completions"
    assert _body(post)["model"] == "deepseek-reasoner"


def test_whitespace_only_base_url_is_treated_as_missing(monkeypatch):
    """`TEXT_MODEL_BASE_URL=" "` is truthy, so a bare `if not base` left the guard unreachable and
    the request went to " /chat/completions" as an opaque connection error."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "   ")
    monkeypatch.setenv("TEXT_MODEL", "inception/mercury-2.5")
    post = _capture(monkeypatch)
    with pytest.raises(ValueError, match="TEXT_MODEL_BASE_URL"):
        model.field_text({"goal": "Find a flight"})
    post.assert_not_called()


def test_reasoning_none_is_normalised_like_omit(monkeypatch):
    """`omit` accepted case and spaces while `none` did not, so " NONE " silently re-enabled
    reasoning instead of turning it off."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TEXT_MODEL_REASONING", " NONE ")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert _body(post)["reasoning"] == {"enabled": False}


def test_fireworks_gets_thinking_never_reasoning(monkeypatch):
    """Live: Fireworks 400s on `reasoning` ("Extra inputs are not permitted") but accepts
    `thinking`. The old host check only knew api.deepseek.com, so unset reasoning 400'd here."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.fireworks.ai/inference/v1")
    monkeypatch.setenv("TEXT_MODEL", "accounts/fireworks/models/deepseek-v4p1-flash")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert "reasoning" not in body
    assert body["thinking"] == {"type": "disabled"}


def test_gemini_default_sends_no_reasoning_field(monkeypatch):
    """Live: the Gemini OpenAI shim 400s on both `reasoning` and `thinking`. Unset reasoning used
    to send `reasoning`, so this endpoint needed `omit` to work at all."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("TEXT_MODEL", "gemini-2.5-flash-lite")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert "reasoning" not in body
    assert "thinking" not in body


def test_unknown_endpoint_sends_no_reasoning_field(monkeypatch):
    """An unrecognised OpenAI-compatible endpoint gets no reasoning key rather than a guess."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "http://vllm.internal:8000/v1")
    monkeypatch.setenv("TEXT_MODEL", "local-model")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert "reasoning" not in body
    assert "thinking" not in body


def test_unrecognised_reasoning_value_is_rejected(monkeypatch):
    """A typo must not silently fall back to the default payload."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "disabled")
    post = _capture(monkeypatch)
    with pytest.raises(ValueError, match="TEXT_MODEL_REASONING"):
        model.field_text({"goal": "Find a flight"})
    post.assert_not_called()


def test_openai_reasoning_model_gets_max_completion_tokens_and_reasoning_effort(monkeypatch):
    """Live: o4-mini/o3-mini 400 on `max_tokens` ("Use 'max_completion_tokens' instead") and on the
    `reasoning` object ("Unknown parameter: 'reasoning'"), and accept `reasoning_effort`."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("TEXT_MODEL", "o4-mini")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_completion_tokens"] == 1024
    assert "max_tokens" not in body
    assert body["reasoning_effort"] == "low"
    assert "reasoning" not in body and "thinking" not in body


def test_openai_non_reasoning_model_keeps_max_tokens_and_no_reasoning(monkeypatch):
    """Live: gpt-4o-mini accepts `max_tokens` and 400s on `reasoning_effort`
    ("Unrecognized request argument"), so only the reasoning families may get one."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("TEXT_MODEL", "gpt-4o-mini")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_tokens"] == 1024
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body and "reasoning" not in body


def _error_response(status, payload):
    class Response:
        status_code = status
        is_error = True

        @staticmethod
        def json():
            if isinstance(payload, Exception):
                raise payload
            return payload

    return Response()


def test_provider_error_message_is_surfaced(monkeypatch):
    """A bare "HTTP 400" is what made the unknown-field trap expensive to diagnose; the provider
    already explains itself in the body."""
    body = {"error": {"message": "Extra inputs are not permitted, field: 'reasoning'"}}
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=_error_response(400, body)))
    with pytest.raises(RuntimeError, match="Extra inputs are not permitted"):
        model.post_json("https://api.fireworks.ai/inference/v1/chat/completions", "test", {})


def test_provider_error_list_body_is_surfaced(monkeypatch):
    """The Gemini shim wraps its error in a one-element list."""
    body = [{"error": {"message": 'Unknown name "reasoning": Cannot find field.'}}]
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=_error_response(400, body)))
    with pytest.raises(RuntimeError, match="Unknown name"):
        model.post_json("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "test", {})


def test_non_json_error_body_falls_back_to_the_status(monkeypatch):
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=_error_response(400, ValueError("not json"))))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        model.post_json("https://api.example.com/v1/chat/completions", "test", {})


def test_mixed_case_openai_hostname_is_recognised(monkeypatch):
    """`https://API.OpenAI.com/v1` is a valid hostname; an exact substring test missed it, so
    o4-mini got `max_tokens` and no `reasoning_effort` — both of which OpenAI rejects."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://API.OpenAI.com/v1")
    monkeypatch.setenv("TEXT_MODEL", "o4-mini")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_completion_tokens"] == 1024
    assert body["reasoning_effort"] == "low"


def test_openai_reasoning_model_behind_a_proxy_gets_the_openai_fields(monkeypatch):
    """The vocabulary follows the model: an o-series model served by a gateway or Azure deployment
    needs `max_completion_tokens` and `reasoning_effort` just as much as one served by OpenAI."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://my-gateway.internal/v1")
    monkeypatch.setenv("TEXT_MODEL", "o4-mini")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_completion_tokens"] == 1024
    assert body["reasoning_effort"] == "low"
    assert "max_tokens" not in body


def test_vendor_prefixed_openai_reasoning_model_is_recognised(monkeypatch):
    """A router's `openai/o4-mini` spelling is the same model."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TEXT_MODEL", "openai/o4-mini")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_completion_tokens"] == 1024
    assert body["reasoning_effort"] == "low"


def test_groq_gpt_oss_is_not_mistaken_for_an_openai_reasoning_model(monkeypatch):
    """`openai/gpt-oss-20b` is served by Groq and must keep the omit dialect."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("TEXT_MODEL", "openai/gpt-oss-20b")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    body = _body(post)
    assert body["max_tokens"] == 1024
    assert "reasoning_effort" not in body and "reasoning" not in body and "thinking" not in body


def test_deepseek_host_still_speaks_thinking(monkeypatch):
    """The provider table still governs non-OpenAI reasoning models."""
    _clear(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://API.DeepSeek.com/v1")
    monkeypatch.setenv("TEXT_MODEL", "deepseek-chat")
    post = _capture(monkeypatch)
    model.field_text({"goal": "Find a flight"})
    assert _body(post)["thinking"] == {"type": "disabled"}
