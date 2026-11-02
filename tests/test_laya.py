"""Contracts for the optional Laya integration (offline; the engine is faked).

The real weights are exercised on CI (scripts/check_laya.py) — these tests pin
the decision logic, the confidence gate, and the keyword fallback.
"""

import pytest

from jev_ultrafast import laya_local, orchestrator, skills


class FakeLaya:
    """Stands in for the laya Agent: scripted decisions, records every call."""

    def __init__(self, decisions=None, error=None):
        self.decisions = decisions or {}
        self.error = error
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        answers = {
            question_id: {"choice": choice, "confidence": confidence}
            for question_id, (choice, confidence) in self.decisions.items()
        }
        return {"answers": answers}


@pytest.fixture(autouse=True)
def isolated_laya(monkeypatch):
    monkeypatch.delenv("JEV_LAYA", raising=False)
    monkeypatch.delenv("LAYA_CHECKPOINT", raising=False)
    monkeypatch.setattr(laya_local, "_engine", None)
    monkeypatch.setattr(laya_local, "_load_error", None)
    yield


def test_status_reports_each_state(monkeypatch):
    monkeypatch.setenv("JEV_LAYA", "off")
    assert laya_local.status() == "off (JEV_LAYA=off)"
    monkeypatch.setenv("JEV_LAYA", "on")
    monkeypatch.setattr(laya_local, "_engine", FakeLaya())
    assert laya_local.status().startswith("ready")
    # installed but the weights failed to load → the load error wins
    monkeypatch.setattr(laya_local, "_engine", None)
    monkeypatch.setattr(laya_local, "_load_error", "laya unavailable: no network")
    assert "no network" in laya_local.status()
    # not installed at all → the friendly install hint
    monkeypatch.setattr(laya_local, "_load_error", None)
    monkeypatch.setattr(laya_local, "available", lambda: False)
    assert "not installed" in laya_local.status()
    # installed, no error, not yet loaded
    monkeypatch.setattr(laya_local, "available", lambda: True)
    assert laya_local.status() == "not loaded yet"


def test_route_task_uses_laya_when_confident():
    fake = FakeLaya({"route": ("orchestrated", 0.91)})
    laya_local._engine = fake
    assert orchestrator.route_task("Recherchiere Flüge und erstelle eine Datei") == "orchestrated"
    state, questions = fake.calls[0]
    assert "Recherchiere" in state["mission"]
    assert set(questions["route"]["criteria"]) == {"browser", "orchestrated"}


def test_route_task_falls_back_below_the_confidence_gate():
    laya_local._engine = FakeLaya({"route": ("orchestrated", 0.41)})  # below 0.55
    # German mission with no keyword match → the keyword router says browser
    assert orchestrator.route_task("Recherchiere Flüge und erstelle eine Datei") == "browser"


def test_route_task_falls_back_when_laya_is_off_or_missing(monkeypatch):
    laya_local._engine = FakeLaya({"route": ("orchestrated", 0.99)})
    monkeypatch.setenv("JEV_LAYA", "off")
    assert laya_local.enabled() is False
    assert orchestrator.route_task("Download the PDF report and summarize it") == "orchestrated"  # keywords
    monkeypatch.setenv("JEV_LAYA", "on")
    monkeypatch.setattr(laya_local, "_engine", None)  # not installed / not loaded
    assert orchestrator.route_task("Find flights on this page") == "browser"


def test_route_task_survives_a_crashing_engine():
    laya_local._engine = FakeLaya(error=RuntimeError("weights exploded"))
    assert orchestrator.route_task("create a python script and run it") == "orchestrated"  # keywords


def test_select_skills_uses_laya_semantically():
    fake = FakeLaya({"skill": ("documents", 0.82)})
    laya_local._engine = fake
    # German goal: the keyword lists (es/en) find nothing; Laya picks documents
    selected = skills.select_skills("Fasse dieses PDF-Dokument zusammen", available_tools={"parse_document"})
    assert [s["id"] for s in selected] == ["documents"]
    _, questions = fake.calls[0]
    assert "parse_document" in str(questions) or questions["skill"]["criteria"]["documents"]
    assert "none" in questions["skill"]["criteria"]


def test_select_skills_none_answer_keeps_keywords():
    laya_local._engine = FakeLaya({"skill": ("none", 0.9)})
    selected = skills.select_skills("Analyze the PDF document", available_tools={"parse_document"})
    assert [s["id"] for s in selected] == ["documents"]  # keyword fallback


def test_select_skills_with_no_eligible_skills():
    laya_local._engine = FakeLaya({"skill": ("documents", 0.9)})
    assert skills.select_skills("Analyze the PDF document", available_tools={"web_search"}) == []


def test_pick_skills_single_eligible_still_asks():
    """One eligible skill is offered together with 'none' — the model confirms it fits."""
    fake = FakeLaya({"skill": ("browser", 0.9)})
    laya_local._engine = fake
    only_browser = [{"id": "browser", "name": "Browser", "description": "d", "tools": ["browser_task"]}]
    assert laya_local.pick_skills("anything", only_browser, available_tools={"browser_task"}) == only_browser
    assert list(fake.calls[0][1]["skill"]["criteria"]) == ["browser", "none"]

    fake = FakeLaya({"skill": ("none", 0.9)})  # rejected: not a fit
    laya_local._engine = fake
    assert laya_local.pick_skills("anything", only_browser, available_tools={"browser_task"}) is None


def test_engine_load_failure_is_remembered(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "laya":
            raise ImportError("no laya")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    assert laya_local.available() is False
    assert laya_local.engine() is None


def test_firefox_runner_routes_through_laya(monkeypatch):
    """TaskRunner.start honours a confident Laya routing for a keyword-less goal."""
    from jev_ultrafast import firefox

    calls = []
    monkeypatch.setattr(firefox.TaskRunner, "_run", lambda *args: calls.append(("browser", *args)))
    monkeypatch.setattr(
        firefox.TaskRunner, "_run_orchestrated", lambda self, *args: calls.append(("orchestrated", *args))
    )
    laya_local._engine = FakeLaya({"route": ("orchestrated", 0.88)})

    class MockBridge:
        def send(self, message):
            pass

        def broadcast(self, message):
            pass

    runner = firefox.TaskRunner(MockBridge())
    runner.start("Recherchiere Flüge und erstelle eine Datei", "https://example.com", 7)
    assert calls and calls[0][0] == "orchestrated"
    runner._lock.release()  # the mocked _run did not release what start() acquired
