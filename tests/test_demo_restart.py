"""A finished demo session restarts with the same goal; a running one is left alone."""

import jev_ultrafast.demo as demo


class FakeAgent:
    def __init__(self, status):
        self.state = {"status": status, "scenario": "flights", "goal": "G", "record": False}
        self.commands = []

    def command(self, name, body=None):
        self.commands.append(name)
        self.state["status"] = "ready"

    def snapshot(self):
        return {"status": self.state["status"], "history": []}


def test_finished_session_restarts_with_same_goal(monkeypatch):
    finished, fresh = FakeAgent("done"), FakeAgent("ready")
    seen = {}

    def fake_build(scenario, goal, record):
        seen["args"] = (scenario, goal, record)
        return fresh

    monkeypatch.setattr(demo, "AGENT", finished)
    monkeypatch.setattr(demo, "build_agent", fake_build)
    monkeypatch.setattr(demo, "close_browser", lambda: seen.setdefault("closed", True))

    demo.command("tick", {})

    assert seen["args"] == ("flights", "G", False), "restart must carry the same session settings"
    assert seen.get("closed"), "the finished session must be closed"
    assert demo.AGENT is fresh and fresh.commands == ["tick"]
    assert finished.commands == []


def test_running_session_is_not_rebuilt(monkeypatch):
    running = FakeAgent("ready")
    monkeypatch.setattr(demo, "AGENT", running)

    def fail_build(*_args):
        raise AssertionError("a running session must not be rebuilt")

    monkeypatch.setattr(demo, "build_agent", fail_build)

    demo.command("tick", {})

    assert demo.AGENT is running and running.commands == ["tick"]
