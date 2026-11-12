"""Vercel AI Gateway adapter for TypeSafe requests. Offline; no model calls."""

from jev_ultrafast import model

BODY = {"model": "typesafe-ai/jev", "state": {"page": {}}, "questions": {"operation": {"type": "choice"}}}


def test_direct_typesafe_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    fake = lambda url, key, body, headers=None: calls.append((url, body, headers)) or {"answers": {}}  # noqa: E731
    monkeypatch.setattr(model, "post_json", fake)
    model.typesafe_request(BODY)
    assert calls == [("https://api.typesafe.ai/v1/systemone", BODY, None)]


def test_gateway_maps_protocol_and_renormalises(monkeypatch):
    calls = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://ai-gateway.vercel.sh/")
    reply = {
        "answers": {"operation": {"type": "choice", "choice": "CLICK", "probabilities": {"CLICK": 0.67, "DONE": 0.34}}},
        "usage": {"inputTokens": 12, "outputTokens": 3},
        "providerMetadata": {"typesafe": {"confidence": {"operation": 0.9}}},
    }
    fake = lambda url, key, body, headers=None: calls.append((url, body, headers)) or reply  # noqa: E731
    monkeypatch.setattr(model, "post_json", fake)
    result = model.typesafe_request(BODY)
    url, body, headers = calls[0]
    assert url == "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
    assert body == {"state": BODY["state"], "questions": BODY["questions"]}
    assert headers["ai-model-id"] == "typesafe-ai/jev"
    assert headers["ai-evaluation-model-specification-version"] == "4"
    answer = result["answers"]["operation"]
    assert abs(sum(answer["probabilities"].values()) - 1) < 1e-9
    assert answer["choice"] == "CLICK" and answer["confidence"] == 0.9
    assert result["model"] == "typesafe-ai/jev"
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert model.validate_choice(answer, ["CLICK", "DONE"]) is answer
