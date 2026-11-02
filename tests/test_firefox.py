"""Offline contracts for the Firefox extension bridge. No browser, no paid APIs."""

import base64
import json
import os
import secrets
import socket
import struct
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_ultrafast import firefox, model
from jev_ultrafast.browser import StalePage

ROOT = Path(__file__).parent.parent


class FakeExtension:
    """A raw-socket WebSocket client that plays the extension side of the bridge."""

    def __init__(self, port, origin="moz-extension://test"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall(
            (
                "GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n"
            ).encode()
        )
        status = self._read_line()
        if b" 101 " not in status:
            raise ConnectionError(f"Handshake rejected: {status.decode().strip()}")
        while self._read_line().strip():
            pass

    def _read_line(self):
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("closed")
            line += chunk
        return line

    def send(self, message):
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        self.sock.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def recv(self, timeout=5):
        self.sock.settimeout(timeout)
        opcode, payload = firefox._read_frame(self.sock)
        if opcode == 8:
            raise ConnectionError("closed")
        return json.loads(payload.decode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def bridge():
    server = firefox.BridgeServer(port=0)
    server.start()
    yield server
    server.close()


def page_state(url, text):
    return {
        "url": url,
        "title": "Search",
        "w": 1120,
        "h": 780,
        "text": text,
        "scroll": {"y": 0, "height": 800},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
        "marker": [1.0, url, 0, 0, 1120, 780, "Search", text, [], 7],
        "page_key": [1.0, url, 0, 0, 1120, 780, [[10, "", None, None, False, False]]],
        "guards": {
            "10": [10, "textbox", "Search", "", None, None, None, False, None, None, None, None, None, ""],
            "20": [20, "button", "Go", "", None, None, None, False, None, None, None, None, None, ""],
        },
        "omitted_actions": 0,
    }


def serve(ext, handler, stop):
    """Answer host commands until stopped; records every command it served."""

    def loop():
        while not stop.is_set():
            try:
                message = ext.recv(timeout=0.2)
            except (socket.timeout, TimeoutError):
                continue
            except (ConnectionError, OSError):
                return
            if "id" not in message:
                continue
            try:
                ext.send({"id": message["id"], "ok": True, "result": handler(message)})
            except Exception as error:  # noqa: BLE001 - forwarded to the host as an error reply
                ext.send({"id": message["id"], "ok": False, "error": str(error)})

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def connect_fake_extension(bridge_instance, handler):
    ext = FakeExtension(bridge_instance.port)
    ext.send({"type": "hello"})
    welcome = ext.recv()
    assert welcome["type"] == "welcome" and welcome["ok"] is True
    stop = threading.Event()
    serve(ext, handler, stop)
    return ext, stop


def test_extension_snapshot_copy_stays_in_sync():
    assert (ROOT / "extension" / "snapshot.js").read_text() == (ROOT / "jev_ultrafast" / "snapshot.js").read_text()


def test_handshake_welcomes_a_moz_extension_origin(bridge):
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    welcome = ext.recv()
    assert welcome["ok"] is True
    ext.close()


def test_foreign_origin_is_rejected(bridge):
    with pytest.raises(ConnectionError, match="Handshake rejected"):
        FakeExtension(bridge.port, origin="https://evil.example")


def test_bridge_token_is_enforced():
    server = firefox.BridgeServer(port=0, token="secret")
    server.start()
    try:
        ext = FakeExtension(server.port)
        ext.send({"type": "hello", "token": "wrong"})
        welcome = ext.recv()
        assert welcome["ok"] is False
        ext.send({"type": "hello", "token": "secret"})
        assert ext.recv()["ok"] is True
        ext.close()
    finally:
        server.close()


def test_command_round_trip(bridge):
    ext, stop = connect_fake_extension(bridge, lambda message: {"echo": message["type"]})
    try:
        assert bridge.command("observe", tabId=3, screenshot=False) == {"echo": "observe"}
        assert bridge.command("act", tabId=3, action={"id": "e1"}) == {"echo": "act"}
    finally:
        stop.set()
        ext.close()


def test_command_error_is_raised(bridge):
    def handler(message):
        raise ValueError("boom")

    ext, stop = connect_fake_extension(bridge, handler)
    try:
        with pytest.raises(firefox.BridgeError, match="boom"):
            bridge.command("observe", tabId=1)
    finally:
        stop.set()
        ext.close()


def test_command_without_extension_has_instructions(bridge):
    with pytest.raises(firefox.BridgeError, match="jev-firefox"):
        bridge.command("observe", tabId=1)


def test_agent_runs_over_the_bridge(bridge, monkeypatch):
    for name in ("TYPESAFE_API_KEY", "PLANNER_PROVIDER", "PLANNER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POLICY_PROVIDER", "custom")
    monkeypatch.setenv("POLICY_BASE_URL", "https://gateway.test/v1")
    monkeypatch.setenv("POLICY_API_KEY", "test-key")
    monkeypatch.setenv("POLICY_MODEL", "test-model")
    replies = [{"operation": "CLICK", "target": "2"}, {"operation": "DONE"}]
    served = {"n": 0}

    def fake_post(_url, _key, _body):
        answer = replies[min(served["n"], len(replies) - 1)]
        served["n"] += 1
        return {"model": "test-model", "choices": [{"message": {"content": json.dumps(answer)}}]}

    monkeypatch.setattr(model, "post_json", fake_post)

    current = {"state": page_state("https://example.test/", "Search")}
    acted = []

    def handler(message):
        kind = message["type"]
        if kind == "observe":
            state = dict(current["state"])
            if message.get("screenshot"):
                state["screenshot"] = "c2hvdA=="
            return state
        if kind == "fresh":
            if "node" in message:
                return [current["state"]["page_key"], current["state"]["guards"][str(message["node"])]]
            return current["state"]["marker"]
        if kind == "act":
            acted.append((message["action"]["id"], message["tabId"]))
            current["state"] = page_state("https://example.test/results", "Results")
            return {"executed": message["action"]["id"]}
        raise AssertionError(kind)

    ext, stop = connect_fake_extension(bridge, handler)
    try:
        from jev_ultrafast import agent as loop

        browser = firefox.FirefoxBrowser("https://example.test/", tab_id=7, bridge=bridge)
        agent = loop.Agent("https://example.test/", "Find a book", screenshots=True, browser=browser)
        assert agent.state["page"]["url"] == "https://example.test/"
        agent.command("predict", {})
        assert agent.state["decision"]["choice"] == "e3"
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
        assert agent.state["history"][-1]["action"] == "Go"
        assert agent.state["history"][-1]["page_changed"] is True
        assert agent.state["page"]["url"] == "https://example.test/results"
        agent.command("predict", {})
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
        assert agent.state["status"] == "done"
        assert acted == [("e3", 7)]
    finally:
        stop.set()
        ext.close()


def test_stale_target_over_the_bridge_raises_stale_page(bridge):
    state = page_state("https://example.test/", "Search")

    def handler(message):
        if message["type"] == "fresh" and "node" in message:
            return None  # the tab reports a new document: no cache, no guard
        if message["type"] == "observe":
            return dict(state)
        return {}

    ext, stop = connect_fake_extension(bridge, handler)
    try:
        browser = firefox.FirefoxBrowser("https://example.test/", tab_id=1, bridge=bridge)
        page = browser.observe(screenshot=False)
        with pytest.raises(StalePage):
            browser.act(page["actions"][2], page)
    finally:
        stop.set()
        ext.close()


def test_runner_validates_goals_and_rejects_concurrency(bridge):
    bridge.runner = firefox.TaskRunner(bridge)
    with pytest.raises(ValueError, match="1–2,000"):
        bridge.runner.start("", "https://example.test/", 1)
    assert bridge.runner.current_state()["status"] == "idle"
    bridge.runner._lock.acquire()  # simulate an active run
    with pytest.raises(ValueError, match="already running"):
        bridge.runner.start("Do something", "https://example.test/", 1)
    bridge.runner._lock.release()


def test_open_command_creates_a_tab(bridge):
    ext, stop = connect_fake_extension(bridge, lambda message: {"tabId": 42} if message["type"] == "open" else {})
    try:
        browser = firefox.FirefoxBrowser("https://example.test/", bridge=bridge)
        assert browser.tab_id == 42
    finally:
        stop.set()
        ext.close()


# ── Self-test, persistent errors, and navigation resilience ──────────────────


def test_check_providers_reports_each_role(monkeypatch):
    from jev_ultrafast import providers as provider_layer

    for name in ("POLICY_PROVIDER", "PLANNER_PROVIDER", "TEXT_MODEL_PROVIDER", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi")
    monkeypatch.setenv("POLICY_PROVIDER", "groq")
    monkeypatch.setenv("POLICY_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("PLANNER_PROVIDER", "nvidia")
    monkeypatch.setenv("PLANNER_MODEL", "z-ai/glm-5.3")
    monkeypatch.setenv("TEXT_MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("TEXT_MODEL", "z-ai/glm-5.3-flash")
    seen = []

    def fake_chat(provider, system, user, max_tokens=1024):
        seen.append(provider["model"])
        if provider["model"] == "z-ai/glm-5.3":
            raise RuntimeError("Model provider returned HTTP 404: model not found; no action executed.")
        return "{}", {"model": provider["model"], "usage": {}, "provider": provider["name"]}

    monkeypatch.setattr(provider_layer, "chat", fake_chat)
    results = firefox.check_providers()
    assert set(results) == {"planner", "policy", "text"}
    assert results["policy"]["ok"] is True and results["policy"]["latency_ms"] is not None
    assert results["text"]["ok"] is True
    assert results["planner"]["ok"] is False
    assert "check the exact model id" in results["planner"]["detail"]
    assert results["planner"]["model"] == "nvidia:z-ai/glm-5.3"


def test_check_providers_marks_planner_optional_when_unset(monkeypatch):
    for name in ("PLANNER_PROVIDER", "PLANNER_BASE_URL", "POLICY_PROVIDER", "TEXT_MODEL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    from jev_ultrafast import providers as provider_layer

    monkeypatch.setattr(provider_layer, "chat", Mock(return_value=("{}", {})))
    results = firefox.check_providers()
    assert set(results) == {"policy", "text"}


def test_runner_carries_the_last_error_until_the_next_run(monkeypatch):
    from jev_ultrafast import agent as loop

    bridge = Mock(broadcast=Mock())
    runner = firefox.TaskRunner(bridge)
    assert runner.current_state()["error"] is None

    class FailingAgent:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("Model provider returned HTTP 401: invalid key")

        def run(self):
            raise NotImplementedError

    monkeypatch.setattr(loop, "Agent", FailingAgent)
    runner._lock.acquire()  # start() holds this lock while _run executes
    runner._run("goal", "https://example.test/", 1)
    state = runner.current_state()
    assert state["status"] == "idle"
    assert "HTTP 401" in state["error"]


def test_lost_content_context_becomes_a_stale_page(bridge):
    state = page_state("https://example.test/", "Search")

    def handler(message):
        if message["type"] == "fresh" and "node" in message:
            raise RuntimeError("Could not establish connection. Receiving end does not exist.")
        if message["type"] == "observe":
            return dict(state)
        return {}

    ext, stop = connect_fake_extension(bridge, handler)
    try:
        browser = firefox.FirefoxBrowser("https://example.test/", tab_id=1, bridge=bridge)
        page = browser.observe(screenshot=False)
        with pytest.raises(StalePage):
            browser.act(page["actions"][2], page)
    finally:
        stop.set()
        ext.close()


def test_hello_triggers_a_provider_check_broadcast(monkeypatch):
    from jev_ultrafast import providers as provider_layer

    for name in ("PLANNER_PROVIDER", "PLANNER_BASE_URL", "POLICY_PROVIDER", "TEXT_MODEL_PROVIDER", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    monkeypatch.setenv("POLICY_PROVIDER", "groq")
    monkeypatch.setenv("POLICY_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(provider_layer, "chat", Mock(return_value=("{}", {})))

    server = firefox.BridgeServer(port=0)
    server.runner = firefox.TaskRunner(server)
    server.start()
    try:
        ext = FakeExtension(server.port)
        ext.send({"type": "hello"})
        assert ext.recv()["ok"] is True
        message = ext.recv(timeout=5)
        while message.get("type") != "state" or not message.get("state", {}).get("providers"):
            message = ext.recv(timeout=5)
        providers_report = message["state"]["providers"]
        assert providers_report["policy"]["ok"] is True
        assert providers_report["policy"]["model"] == "groq:openai/gpt-oss-20b"
        ext.close()
    finally:
        server.close()


# ── Key-rejection guidance and .env paste hardening ──────────────────────────


def test_check_providers_explains_a_rejected_key(monkeypatch):
    from jev_ultrafast import providers as provider_layer

    for name in ("PLANNER_PROVIDER", "POLICY_PROVIDER", "TEXT_MODEL_PROVIDER", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-bad")
    monkeypatch.setenv("POLICY_PROVIDER", "nvidia")
    monkeypatch.setenv("POLICY_MODEL", "z-ai/glm-5.3")

    def fake_chat(provider, system, user, max_tokens=1024):
        raise RuntimeError(
            'Model provider returned HTTP 403: {"status":403,"detail":"Authorization failed"}; no action executed.'
        )

    monkeypatch.setattr(provider_layer, "chat", fake_chat)
    results = firefox.check_providers()
    detail = results["policy"]["detail"]
    assert results["policy"]["ok"] is False
    assert "API-key problem" in detail
    assert "https://build.nvidia.com" in detail
    assert "without quotes" in detail


def test_env_loader_strips_quotes_and_bom(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_bytes(
        b"\xef\xbb\xbf# comment\n"
        + b'JEV_TEST_QUOTED_KEY="nvapi-secret"\n'
        + b"JEV_TEST_SINGLE_KEY='gsk-single'\n"
        + b"JEV_TEST_BARE_KEY=gsk-bare\n"
    )
    monkeypatch.chdir(tmp_path)
    try:
        firefox.load_environment()
        assert os.environ["JEV_TEST_QUOTED_KEY"] == "nvapi-secret"
        assert os.environ["JEV_TEST_SINGLE_KEY"] == "gsk-single"
        assert os.environ["JEV_TEST_BARE_KEY"] == "gsk-bare"
    finally:
        for name in ("JEV_TEST_QUOTED_KEY", "JEV_TEST_SINGLE_KEY", "JEV_TEST_BARE_KEY"):
            os.environ.pop(name, None)
