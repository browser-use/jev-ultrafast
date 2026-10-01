import base64
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import jev_ultrafast

SCRIPT = Path(__file__).parents[1] / "scripts" / "record_flights.py"


class FakeBrowser:
    target = "target"
    session = "session"

    def __init__(self):
        self.calls = []

    def call(self, method, **kwargs):
        self.calls.append(method)
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"jpeg").decode()}
        return {}

    def observe(self, screenshot=False):
        return {"screenshot": screenshot}


class FakeAgent:
    def __init__(self, _url, _goal, *, run_error=None, snapshot_error=None):
        self.browser = FakeBrowser()
        self.run_error = run_error
        self.snapshot_error = snapshot_error
        self.closed = 0

    def run(self):
        if self.run_error:
            raise self.run_error
        yield {"history": [], "elapsed_ms": 1, "status": "done"}

    def snapshot(self):
        if self.snapshot_error:
            raise self.snapshot_error
        return {"history": []}

    def close(self):
        self.closed += 1


class FakeThread:
    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        pass

    def join(self, timeout):
        pass


def load_script():
    spec = importlib.util.spec_from_file_location("record_flights_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("run_error", [None, RuntimeError("run failed")])
def test_recording_closes_agent_after_success_or_run_failure(monkeypatch, tmp_path, run_error):
    module = load_script()
    agent = FakeAgent("url", "goal", run_error=run_error)
    monkeypatch.setattr(jev_ultrafast, "Agent", lambda _url, _goal: agent)
    monkeypatch.setattr("examples.flights.verify", lambda _page: {"passed": True})
    monkeypatch.setattr(
        module,
        "threading",
        SimpleNamespace(Event=threading.Event, Thread=FakeThread),
    )

    if run_error:
        with pytest.raises(RuntimeError, match="run failed"):
            module.main([str(tmp_path / "recording")])
    else:
        module.main([str(tmp_path / "recording")])

    assert agent.closed == 1
    assert agent.browser.calls[-1] == "Page.stopScreencast"


def test_recording_closes_agent_when_finalization_fails(monkeypatch, tmp_path):
    module = load_script()
    agent = FakeAgent("url", "goal", snapshot_error=RuntimeError("snapshot failed"))
    monkeypatch.setattr(jev_ultrafast, "Agent", lambda _url, _goal: agent)
    monkeypatch.setattr("examples.flights.verify", lambda _page: {"passed": True})
    monkeypatch.setattr(
        module,
        "threading",
        SimpleNamespace(Event=threading.Event, Thread=FakeThread),
    )

    with pytest.raises(RuntimeError, match="snapshot failed"):
        module.main([str(tmp_path / "recording")])

    assert agent.closed == 1


def test_recording_closes_agent_when_initial_capture_fails(monkeypatch, tmp_path):
    module = load_script()
    agent = FakeAgent("url", "goal")

    def fail_capture(_method, **_kwargs):
        raise RuntimeError("capture failed")

    agent.browser.call = fail_capture
    monkeypatch.setattr(jev_ultrafast, "Agent", lambda _url, _goal: agent)

    with pytest.raises(RuntimeError, match="capture failed"):
        module.main([str(tmp_path / "recording")])

    assert agent.closed == 1
