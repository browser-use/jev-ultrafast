"""Tests for pluggable decision engines: TypeSafe, Laya-Remote, and Laya-Local."""

import os
from unittest.mock import Mock, patch
import pytest
import httpx

from jev_ultrafast.engines import (
    BaseEngine,
    TypeSafeEngine,
    LayaRemoteEngine,
    LayaLocalEngine,
    get_decision_engine,
)
from jev_ultrafast import model


def sample_state():
    return {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search the web",
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Submit", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }


def test_engine_resolution_cascade(monkeypatch):
    # 1. Default without keys -> typesafe
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("LAYA_ENDPOINT", raising=False)
    monkeypatch.delenv("DECISION_API_URL", raising=False)
    monkeypatch.delenv("DECISION_ENGINE", raising=False)
    monkeypatch.delenv("USE_LAYA", raising=False)

    engine = get_decision_engine()
    assert isinstance(engine, TypeSafeEngine)

    # 2. When LAYA_ENDPOINT is set -> LayaRemoteEngine
    monkeypatch.setenv("LAYA_ENDPOINT", "http://colab.remote:8000")
    engine = get_decision_engine()
    assert isinstance(engine, LayaRemoteEngine)
    assert engine.endpoint_url == "http://colab.remote:8000"

    # 3. Explicit override with DECISION_ENGINE
    monkeypatch.setenv("DECISION_ENGINE", "typesafe")
    engine = get_decision_engine()
    assert isinstance(engine, TypeSafeEngine)

    # 4. Explicit override to laya-local
    monkeypatch.setenv("DECISION_ENGINE", "laya-local")
    engine = get_decision_engine()
    assert isinstance(engine, LayaLocalEngine)


def test_laya_remote_engine_prediction(monkeypatch):
    fake_prediction = {
        "model": "laya-multilingual",
        "answers": {
            "operation": {
                "choice": "CLICK",
                "confidence": 0.98,
                "probabilities": {"CLICK": 0.98, "TYPE_TEXT": 0.02},
            },
            "click_target": {
                "choice": "2",
                "confidence": 0.95,
                "probabilities": {"2": 0.95},
            },
        },
    }

    class MockResponse:
        status_code = 200
        is_error = False

        def json(self):
            return fake_prediction

    client_mock = Mock()
    client_mock.post.return_value = MockResponse()

    engine = LayaRemoteEngine(endpoint_url="http://mock-colab:8000")
    engine._client = client_mock

    elements, targets, controls = model.action_space(sample_state()["actions"])
    decision = engine.choose(sample_state(), "Search for python", [], elements, targets, controls)

    assert decision["operation"] == "CLICK"
    assert decision["target"] == "2"
    assert decision["choice"] == "e2"
    assert decision["engine"] == "laya-remote"
    assert decision["confidence"] == 0.98


def test_laya_remote_engine_connection_error(monkeypatch):
    class ErrorClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("Connection refused")

    engine = LayaRemoteEngine(endpoint_url="http://unreachable-host:9999")
    engine._client = ErrorClient()

    elements, targets, controls = model.action_space(sample_state()["actions"])
    with pytest.raises(RuntimeError, match="Failed to connect to remote Laya engine"):
        engine.choose(sample_state(), "Search for python", [], elements, targets, controls)
