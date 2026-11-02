"""Contracts for MVP-6: streaming, local runtimes, profiles, and the run log."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_ultrafast import discovery, firefox, model, orchestrator, parameters, providers
from tests.test_firefox import FakeExtension


@pytest.fixture
def bridge():
    server = firefox.BridgeServer(port=0)
    server.start()
    yield server
    server.close()


# ── SSE streaming (providers.chat + model.post_stream) ───────────────────────


class FakeStream:
    """An httpx-like streaming response serving scripted SSE lines."""

    def __init__(self, lines, status_code=200):
        self.lines = lines
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._text = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._text.encode()

    def iter_lines(self):
        yield from self.lines


class FakeStreamingClient:
    def __init__(self, streams):
        self.streams = list(streams)
        self.requests = []

    def stream(self, method, url, json=None, headers=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return self.streams.pop(0)


def openai_sse(chunks, model="qwen3.5:4b", usage=None):
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps({"model": model, "choices": [{"delta": {"content": chunk}}]}))
    if usage:
        lines.append("data: " + json.dumps({"model": model, "choices": [{"delta": {}}], "usage": usage}))
    lines.append("data: [DONE]")
    return lines


@pytest.fixture
def planner_provider(monkeypatch):
    for name in ("PLANNER_PROVIDER", "PLANNER_MODEL", "PLANNER_API_KEY", "PLANNER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("PLANNER_MODEL", "qwen3.5:4b")
    yield


def test_post_stream_accumulates_openai_chunks(monkeypatch):
    client = FakeStreamingClient([FakeStream(openai_sse(['{"tool":', ' "list_fil', 'es"}'],
                                                    usage={"input_tokens": 7, "output_tokens": 3}))])
    monkeypatch.setattr(model, "CLIENT", client)
    seen = []
    text, usage, model_id = model.post_stream("http://x/v1/chat/completions", "k",
                                              {"stream": True}, on_delta=seen.append)
    assert text == '{"tool": "list_files"}'
    assert seen == ['{"tool":', ' "list_fil', 'es"}']
    assert usage == {"input_tokens": 7, "output_tokens": 3}
    assert model_id == "qwen3.5:4b"


def test_post_stream_handles_anthropic_events_and_reasoning(monkeypatch):
    lines = [
        "event: message_start",
        "data: " + json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 11}}}),
        "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "thinking", "thinking": "hmm..."}}),
        "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "final"}}),
        "data: " + json.dumps({"type": "message_delta", "usage": {"output_tokens": 4}}),
    ]
    client = FakeStreamingClient([FakeStream(lines)])
    monkeypatch.setattr(model, "CLIENT", client)
    seen = []
    text, usage, _model = model.post_stream("http://x/v1/messages", "k", {}, on_delta=seen.append)
    assert text == "final"  # thinking never becomes content
    assert "hmm..." in seen and "final" in seen
    assert usage == {"input_tokens": 11, "output_tokens": 4}


def test_post_stream_reasoning_content_deltas_stream_but_not_accumulate(monkeypatch):
    lines = [
        "data: " + json.dumps({"model": "m", "choices": [{"delta": {"reasoning_content": "thinking..."}}]}),
        "data: " + json.dumps({"model": "m", "choices": [{"delta": {"content": "answer"}}]}),
        "data: [DONE]",
    ]
    client = FakeStreamingClient([FakeStream(lines)])
    monkeypatch.setattr(model, "CLIENT", client)
    seen = []
    text, _usage, _model = model.post_stream("http://x", "k", {}, on_delta=seen.append)
    assert text == "answer"
    assert seen == ["thinking...", "answer"]


def test_post_stream_accepts_usage_nested_in_the_choice(monkeypatch):
    # some servers put usage inside choices[0] instead of the event top level
    lines = [
        "data: " + json.dumps({"model": "m", "choices": [
            {"delta": {"content": "hi"}, "usage": {"input_tokens": 5, "output_tokens": 2}}
        ]}),
        "data: [DONE]",
    ]
    client = FakeStreamingClient([FakeStream(lines)])
    monkeypatch.setattr(model, "CLIENT", client)
    text, usage, _model = model.post_stream("http://x", "k", {}, on_delta=None)
    assert text == "hi"
    assert usage == {"input_tokens": 5, "output_tokens": 2}


def test_post_stream_raises_with_provider_detail_on_http_error(monkeypatch):
    stream = FakeStream([], status_code=400)
    stream._text = '{"error": {"message": "stream is not supported by this endpoint"}}'
    client = FakeStreamingClient([stream])
    monkeypatch.setattr(model, "CLIENT", client)
    with pytest.raises(RuntimeError, match="HTTP 400.*stream is not supported"):
        model.post_stream("http://x", "k", {"stream": True})


def test_chat_streams_and_falls_back_when_stream_is_rejected(monkeypatch, planner_provider):
    provider = providers.resolve("planner")
    good = FakeStreamingClient([FakeStream(openai_sse(["hello", " world"], usage={"input_tokens": 1}))])
    monkeypatch.setattr(model, "CLIENT", good)
    seen = []
    text, meta = providers.chat(provider, "s", "u", on_delta=seen.append)
    assert text == "hello world"
    assert seen == ["hello", " world"]
    assert meta["usage"]["input_tokens"] == 1
    assert good.requests[0]["json"]["stream"] is True

    # the endpoint rejects streaming → one retry without stream, still delivered
    bad = FakeStreamingClient([FakeStream([], status_code=400)])
    bad.streams[0]._text = "streaming is not supported"
    non_stream = Mock(return_value={"model": "m", "choices": [{"message": {"content": "fallback"}}], "usage": {}})
    monkeypatch.setattr(model, "CLIENT", bad)
    monkeypatch.setattr(model, "post_json", non_stream)
    seen2 = []
    text2, meta2 = providers.chat(provider, "s", "u", on_delta=seen2.append)
    assert text2 == "fallback"
    assert non_stream.called
    assert bad.requests[0]["json"].get("stream") is True
    retry_body = non_stream.call_args[0][2]  # the fallback request dropped "stream"
    assert "stream" not in retry_body
    assert meta2["model"] == "m"


# ── local runtime presets (free / self-hosted) ───────────────────────────────


def test_local_presets_resolve_without_any_api_key(monkeypatch):
    for name in ("POLICY_PROVIDER", "POLICY_MODEL", "POLICY_API_KEY", "POLICY_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POLICY_PROVIDER", "ollama")
    monkeypatch.setenv("POLICY_MODEL", "qwen3.5:4b")
    provider = providers.resolve("policy")
    assert provider["base_url"] == "http://127.0.0.1:11434/v1"
    assert provider["dialect"] == "openai"
    assert provider["key"] == "local"
    assert provider["json_mode"] is False  # local servers vary; extract_json tolerates prose

    monkeypatch.setenv("POLICY_PROVIDER", "LM-Studio")
    provider = providers.resolve("policy")
    assert provider["name"] == "lmstudio"
    monkeypatch.setenv("POLICY_PROVIDER", "llama.cpp")
    assert providers.resolve("policy")["name"] == "llamacpp"


def test_local_base_urls_are_auto_detected(monkeypatch):
    for name in ("POLICY_PROVIDER", "POLICY_BASE_URL", "POLICY_API_KEY", "POLICY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POLICY_PROVIDER", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("POLICY_MODEL", "phi-4-mini")
    provider = providers.resolve("policy")
    assert provider["name"] == "lmstudio"  # detected from the URL, not "custom"


def test_discovery_lists_local_models_without_a_key(monkeypatch):
    monkeypatch.setattr(discovery, "REGISTRY_PATH", Path("/tmp/test-mvp6-registry.json"))
    response = Mock(status_code=200, is_error=False)
    response.json.return_value = {"data": [
        {"id": "qwen3.5:4b"},
        {"id": "nomic-embed-text"},  # embedding model: filtered out
    ]}
    client = Mock()
    client.get.return_value = response
    monkeypatch.setattr(model, "CLIENT", client)
    models = discovery.fetch_models("ollama")  # no OLLAMA key concept at all
    assert [m["id"] for m in models] == ["qwen3.5:4b"]


def test_discover_probes_local_runtimes_and_reports_refused_connections(monkeypatch):
    monkeypatch.setattr(discovery, "REGISTRY_PATH", Path("/tmp/test-mvp6-registry2.json"))
    calls = []

    def fake_fetch(name):
        calls.append(name)
        if name == "ollama":
            return [{"id": "qwen3.5:4b", "displayName": "Qwen3.5:4b", "provider": name, "capabilities": {}}]
        raise RuntimeError("Could not reach lmstudio: connection refused")

    monkeypatch.setattr(discovery, "fetch_models", fake_fetch)
    registry = discovery.discover(refresh=True)
    assert registry["providers"]["ollama"]["ok"] is True
    assert registry["providers"]["lmstudio"]["ok"] is False
    assert "connection refused" in registry["providers"]["lmstudio"]["error"]
    assert "nvidia" in calls or "ollama" in calls  # cloud providers skipped only when keyless


# ── named profiles (parameters + TaskRunner handler) ─────────────────────────


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(parameters, "CONFIG_PATH", tmp_path / "model-config.json")
    monkeypatch.setattr(firefox, "RUNS_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(discovery, "REGISTRY_PATH", tmp_path / "model-registry.json")
    for name in parameters.ROLE_MODEL_ENV.values():
        for env in name:
            monkeypatch.delenv(env, raising=False)
    for env in parameters.ROLE_PARAM_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("JEV_LAYA", "off")  # hermetic: keyword routing only
    monkeypatch.setattr(firefox, "check_providers", lambda: {})
    yield


def test_profiles_save_apply_delete_round_trip(isolated_config):
    import os

    parameters.apply_model("planner", "nvidia", "z-ai/glm-5.3")
    parameters.apply_params("planner", {"reasoning": "high"})
    parameters.apply_model("text", "groq", "openai/gpt-oss-20b")
    saved = parameters.save_profile("Research mode")
    assert saved["models"]["planner"] == {"provider": "nvidia", "model": "z-ai/glm-5.3"}
    assert saved["params"]["planner"] == {"reasoning": "high"}
    assert parameters.profile_names() == ["Research mode"]

    # change everything, then restore
    parameters.apply_model("planner", "ollama", "qwen3.5:4b")
    parameters.apply_params("planner", {"reasoning": "low"})
    parameters.apply_profile("Research mode")
    assert os.environ["PLANNER_PROVIDER"] == "nvidia"
    assert os.environ["PLANNER_MODEL"] == "z-ai/glm-5.3"
    assert os.environ["PLANNER_REASONING"] == "high"
    assert os.environ["TEXT_MODEL"] == "openai/gpt-oss-20b"

    parameters.delete_profile("Research mode")
    assert parameters.profile_names() == []
    with pytest.raises(ValueError, match="Unknown profile"):
        parameters.apply_profile("Research mode")
    with pytest.raises(ValueError, match="1-40"):
        parameters.save_profile("   ")


def test_task_runner_profile_handler(isolated_config):
    class MockBridge:
        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

        def broadcast(self, message):
            self.sent.append(message)

    runner = firefox.TaskRunner(MockBridge())
    runner.handle_profile("save", "coding")
    runner.handle_profile("apply", "coding")
    runner.handle_profile("delete", "coding")
    errors = [m for m in runner.bridge.sent if m.get("type") == "error"]
    assert not errors
    states = [m["state"] for m in runner.bridge.sent if m.get("type") == "state"]
    assert states[0]["profiles"] == ["coding"]

    runner.handle_profile("apply", "ghost")
    errors = [m for m in runner.bridge.sent if m.get("type") == "error"]
    assert errors and "Profile error" in errors[0]["message"]


# ── run history (observability) ──────────────────────────────────────────────


def test_orchestrated_run_appends_to_runs_log(bridge, monkeypatch, tmp_path, isolated_config):
    monkeypatch.setattr(firefox, "RUNS_LOG", tmp_path / "runs.jsonl")

    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()
    received = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                message = ext.recv(timeout=0.2)
            except (TimeoutError, OSError, ConnectionError):
                continue
            received.append(message)
            if "id" in message:
                ext.send({"id": message["id"], "ok": True, "result": {}})

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def fake_orchestration(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        on_step({"step": 1, "tool": "list_files", "args": {}, "result": "(empty)"})
        return {"final": "listed", "steps": [], "usage": {"input_tokens": 40, "output_tokens": 10}, "latency_ms": 55}

    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    runner = firefox.TaskRunner(bridge, workspace=tmp_path / "ws")
    bridge.runner = runner
    monkeypatch.setenv("PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("PLANNER_MODEL", "qwen3.5:4b")
    runner.start("research a topic and write a project", "https://example.com", 7)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (runner.orchestrated or {}).get("status") == "done":
        time.sleep(0.05)
    assert (runner.orchestrated or {}).get("status") == "done"
    stop.set()
    thread.join(timeout=2)

    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["mode"] == "orchestrated" and record["status"] == "done"
    assert record["steps"] == 1 and record["tokens"] == 50
    assert record["goal"].startswith("research a topic")


def test_delta_broadcasts_stream_to_the_extension(bridge, monkeypatch, tmp_path, isolated_config):
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()
    received = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                message = ext.recv(timeout=0.2)
            except (TimeoutError, OSError, ConnectionError):
                continue
            received.append(message)
            if "id" in message:
                ext.send({"id": message["id"], "ok": True, "result": {}})

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def fake_orchestration(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        for chunk in ["think ", "about ", "the ", "search", "…"]:
            on_delta(chunk)
        on_step({"step": 1, "tool": "web_search", "args": {"query": "x"}, "result": "ok"})
        return {"final": "done", "steps": [], "usage": {}, "latency_ms": 1}

    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    runner = firefox.TaskRunner(bridge, workspace=tmp_path / "ws")
    bridge.runner = runner
    monkeypatch.setenv("PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("PLANNER_MODEL", "qwen3.5:4b")
    runner.start("research something", "https://example.com", 7)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (runner.orchestrated or {}).get("status") == "done":
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=2)

    deltas = [m for m in received if m.get("type") == "delta"]
    assert deltas, "no delta broadcast reached the extension"
    # throttled: at most one per interval, and the buffer never exceeds the cap
    assert 1 <= len(deltas) <= 5
    assert all(len(m["text"]) <= 600 for m in deltas)
