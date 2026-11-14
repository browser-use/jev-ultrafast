"""Offline contracts for the public provider implementations."""

from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import DecisionProvider, OpenAITextProvider, TextProvider, TypeSafeProvider, providers


def test_provider_protocols_and_defaults_are_public():
    assert DecisionProvider.__name__ == "DecisionProvider"
    assert TextProvider.__name__ == "TextProvider"
    assert TypeSafeProvider.__name__ == "TypeSafeProvider"
    assert OpenAITextProvider.__name__ == "OpenAITextProvider"


def test_typesafe_explicit_configuration_overrides_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-key")
    monkeypatch.setenv("TYPESAFE_MODEL", "environment-model")
    post = Mock(return_value={"model": "explicit-model", "answers": {}})
    provider = TypeSafeProvider(api_key="explicit-key", model="explicit-model")._use_transport(post)
    request = {"model": "explicit-model", "state": {}, "questions": {}}

    assert provider.model == "explicit-model"
    assert provider.decide(request)["model"] == "explicit-model"
    post.assert_called_once_with(TypeSafeProvider.endpoint, "explicit-key", request)


def test_typesafe_missing_api_key_preserves_key_error(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    post = Mock(side_effect=AssertionError("network called"))
    provider = TypeSafeProvider()._use_transport(post)

    with pytest.raises(KeyError, match="TYPESAFE_API_KEY"):
        provider.decide({"model": "jev-latest", "state": {}, "questions": {}})

    post.assert_not_called()


def test_openai_explicit_configuration_overrides_environment(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "environment-key")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://environment.test/v1")
    monkeypatch.setenv("TEXT_MODEL", "environment-model")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "low")
    post = Mock(return_value={"choices": []})
    provider = OpenAITextProvider(
        api_key="explicit-key",
        base_url="https://gateway.test/v1/",
        model="explicit-model",
        reasoning="none",
        headers={"X-Gateway": "test"},
    )._use_transport(post)
    request = {"model": "explicit-model", "messages": []}

    assert provider.model == "explicit-model"
    provider.generate(request)

    post.assert_called_once_with(
        "https://gateway.test/v1/chat/completions",
        "explicit-key",
        {**request, "reasoning": {"enabled": False}},
        {"X-Gateway": "test"},
    )


def test_openai_mapping_reasoning_is_forwarded_without_mutating_request():
    post = Mock(return_value={"choices": []})
    reasoning = {"thinking": {"type": "disabled"}}
    provider = OpenAITextProvider(
        api_key="test",
        model="test",
        reasoning=reasoning,
    )._use_transport(post)
    request = {"model": "test", "messages": []}

    provider.generate(request)

    assert request == {"model": "test", "messages": []}
    assert post.call_args.args[2] == {**request, **reasoning}


def test_post_json_preserves_auth_headers_and_retry_policy(monkeypatch):
    retry_429 = Mock(status_code=429)
    retry_503 = Mock(status_code=503)
    success = Mock(status_code=200, is_error=False, json=Mock(return_value={"ok": True}))
    post = Mock(side_effect=[retry_429, retry_503, success])
    sleep = Mock()
    monkeypatch.setattr(providers.CLIENT, "post", post)
    monkeypatch.setattr(providers.time, "sleep", sleep)

    result = providers.post_json("https://provider.test", "key", {"input": True}, {"X-Gateway": "test"})

    assert result == {"ok": True}
    assert post.call_count == 3
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer key", "X-Gateway": "test"}
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]


def test_post_json_connection_error_is_not_retried(monkeypatch):
    post = Mock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(providers.CLIENT, "post", post)

    with pytest.raises(RuntimeError, match="connection failed"):
        providers.post_json("https://provider.test", "key", {})

    post.assert_called_once()
