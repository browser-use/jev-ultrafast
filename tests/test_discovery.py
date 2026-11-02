"""Contracts for the model catalogue and registry (MVP-1)."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import discovery, model


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "REGISTRY_PATH", tmp_path / "model-registry.json")
    for name in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield


def fake_client(payload, status=200):
    response = Mock(status_code=status, is_error=status >= 400)
    response.json.return_value = payload
    client = Mock()
    client.get.return_value = response
    return client


def test_chat_model_filtering():
    assert discovery._is_chat_model("z-ai/glm-5.3")
    assert discovery._is_chat_model("openai/gpt-oss-20b")
    assert not discovery._is_chat_model("nvidia/nv-embedqa-e5-v5")  # embed
    assert not discovery._is_chat_model("nvidia/nemoretriever-parse")  # retriever
    assert not discovery._is_chat_model("openai/whisper-1")  # audio
    assert not discovery._is_chat_model("stability/stable-diffusion-xl")  # image


def test_capabilities_inferred_from_the_id():
    caps = discovery._capabilities("qwen/qwen2.5-vl-72b-instruct")
    assert caps["vision"] and not caps["reasoning"] and caps["inferred"]
    caps = discovery._capabilities("z-ai/glm-5.3")
    assert caps["reasoning"] and not caps["vision"]


def test_fetch_models_normalizes_and_dedupes(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(model, "CLIENT", fake_client({"data": [
        {"id": "openai/gpt-oss-20b"},
        {"id": "openai/gpt-oss-20b"},  # duplicate
        {"id": "nvidia/nv-embedqa-e5-v5"},  # not chat
        {"id": "meta-llama/llama-3.3-70b-versatile"},
    ]}))
    models = discovery.fetch_models("groq")
    # sorted by display name: "GPT OSS 20b" < "LLAMA 3.3 70b Versatile"
    assert [m["id"] for m in models] == ["openai/gpt-oss-20b", "meta-llama/llama-3.3-70b-versatile"]
    assert models[0]["displayName"] == "GPT OSS 20b"
    assert models[0]["capabilities"]["text"] is True


def test_fetch_models_without_a_key_names_the_env(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        discovery.fetch_models("groq")


def test_fetch_models_rejected_key_points_to_keys_url(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-dead")
    monkeypatch.setattr(model, "CLIENT", fake_client({}, status=403))
    with pytest.raises(RuntimeError, match="build.nvidia.com"):
        discovery.fetch_models("nvidia")


def test_discover_caches_for_24h(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    calls = []

    def fake_fetch(name):
        calls.append(name)
        return [{"id": f"{name}/model-a", "displayName": "Model A", "provider": name, "capabilities": {}}]

    monkeypatch.setattr(discovery, "fetch_models", fake_fetch)
    registry = discovery.discover()
    # the two keyed cloud providers plus the four keyless local runtimes
    assert {"groq", "nvidia", "ollama", "lmstudio", "llamacpp", "jan"} <= set(registry["providers"])
    assert discovery.REGISTRY_PATH.exists()
    assert len(calls) == 6

    second = discovery.discover()  # served from the cache, no refetch
    assert second == registry
    assert len(calls) == 6

    discovery.discover(refresh=True)  # forced refresh
    assert len(calls) == 12


def test_discover_reports_failures_without_breaking(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(discovery, "DISCOVERABLE", ("groq", "nvidia"))  # focus: cloud failures

    def fake_fetch(name):
        if name == "groq":
            return []
        raise RuntimeError("boom")

    monkeypatch.setattr(discovery, "fetch_models", fake_fetch)
    registry = discovery.discover(refresh=True)
    assert registry["providers"]["groq"]["ok"] is True
    assert registry["providers"]["nvidia"]["ok"] is False
    assert "boom" in registry["providers"]["nvidia"]["error"]
    assert json.loads(discovery.REGISTRY_PATH.read_text())["providers"]["groq"]["ok"] is True


def test_stale_registry_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    stale = {"fetchedAt": 0, "providers": {"groq": {"ok": True, "models": []}}}
    discovery.REGISTRY_PATH.write_text(json.dumps(stale))
    monkeypatch.setattr(discovery, "fetch_models", lambda name: [])
    fresh = discovery.discover()
    assert fresh["fetchedAt"] > 0
    assert "nvidia" not in fresh["providers"] or fresh["providers"]["nvidia"]["ok"] is False
