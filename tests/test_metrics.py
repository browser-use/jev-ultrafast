"""Offline contracts for run metrics and startup checks."""

import importlib
import json

import pytest

from jev_ultrafast.agent import Agent
from jev_ultrafast.browser import StalePage
from jev_ultrafast.metrics import RunMetrics
from jev_ultrafast.preflight import preflight
from tests.test_agent import page


def test_metrics_counts_actions_stale_and_backend_timings(tmp_path):
    metrics = RunMetrics("ego")
    metrics.record_jev(12.4)
    metrics.record_action_attempt("click")
    metrics.record_action_success("click")
    metrics.record_action_attempt("fill")
    metrics.record_stale()
    metrics.record_backend_operation("observe", 3.4)
    metrics.record_backend_operation("act", 5.6)
    metrics.finish("error", RuntimeError("offline"))

    output = metrics.write(tmp_path / "metrics.json")
    assert output["jev_calls"] == 1
    assert output["browser_actions_attempted"] == 2
    assert output["browser_actions_succeeded"] == 1
    assert output["action_success_rate"] == 0.5
    assert output["stale_count"] == 1
    assert output["ego_total_operation_ms"] == 9
    assert output["error_type"] == "RuntimeError"
    assert json.loads((tmp_path / "metrics.json").read_text())["backend"] == "ego"


def test_metrics_imports_ego_cleanup_and_timing_contract():
    class Backend:
        runtime_spawn_count = 1
        cleanup_status = {
            "graceful_exit": True,
            "forced_terminate": False,
            "forced_kill": False,
            "orphan_process_remaining": False,
        }
        ego_timings = {
            "observe": {"count": 2, "total_ms": 10, "max_ms": 7, "last_ms": 3},
            "fresh": {"count": 1, "total_ms": 2, "max_ms": 2, "last_ms": 2},
            "act": {"count": 1, "total_ms": 4, "max_ms": 4, "last_ms": 4},
            "ego_total_operation_ms": 16,
        }

    metrics = RunMetrics("ego")
    metrics.merge_backend(Backend())
    output = metrics.snapshot()
    assert output["runtime_spawn_count"] == 1
    assert output["ego_observe_ms"] == 10
    assert output["ego_total_operation_ms"] == 16
    assert output["orphan_process_remaining"] is False


def test_preflight_is_secret_free_and_checks_ego_command(monkeypatch):
    preflight_module = importlib.import_module("jev_ultrafast.preflight")
    monkeypatch.setattr(preflight_module.shutil, "which", lambda name: "/tmp/ego-browser")
    result = preflight("ego", environ={"TYPESAFE_API_KEY": "secret", "TEXT_MODEL_API_KEY": "text-secret"})
    assert result == {
        "jev_api": "configured",
        "text_helper": "configured",
        "backend": "ego",
        "ego_browser": "available",
    }
    assert "secret" not in json.dumps(result)


def test_preflight_rejects_missing_jev_key_and_unknown_backend():
    with pytest.raises(ValueError, match="Unknown browser backend"):
        preflight("bad", environ={})
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        preflight("browser_harness", environ={})


def test_failed_agent_run_writes_metrics_without_model_payload(tmp_path, monkeypatch):
    class Backend:
        backend_name = "browser_harness"

        def __init__(self):
            self.closed = 0

        def observe(self, screenshot=False):
            return page()

        def fresh(self, page, action=None):
            return True

        def close(self):
            self.closed += 1

    monkeypatch.setattr("jev_ultrafast.agent.choose", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    backend = Backend()
    agent = Agent("https://example.test", "Find it", backend=backend, record_dir=tmp_path)
    with pytest.raises(RuntimeError, match="offline"):
        list(agent.run())
    agent.close()
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["status"] == "error"
    assert metrics["jev_calls"] == 1
    assert metrics["error_type"] == "RuntimeError"
    assert "Authorization" not in (tmp_path / "trace.json").read_text()
    assert backend.closed == 1


def test_repeated_stale_actions_stop_without_an_unbounded_model_loop(tmp_path, monkeypatch):
    class Backend:
        backend_name = "ego"

        def observe(self, screenshot=False):
            return page()

        def fresh(self, page, action=None):
            return True

        def act(self, action, page, text=None):
            raise StalePage("unknown ref")

        def close(self):
            pass

    decision = {
        "choice": "e2",
        "operation": "CLICK",
        "target": "2",
        "confidence": 1.0,
        "probabilities": {"e2": 1.0},
        "latency_ms": 1,
        "usage": {},
    }
    monkeypatch.setattr("jev_ultrafast.agent.choose", lambda *args: decision)
    agent = Agent("https://example.test", "Click it", backend=Backend(), record_dir=tmp_path)
    states = list(agent.run())
    agent.close()
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert states[-1]["status"] == "blocked"
    assert metrics["stale_count"] == 3
    assert metrics["jev_calls"] == 3
    assert metrics["browser_actions_succeeded"] == 0


def test_keyboard_interrupt_during_initial_observe_still_writes_artifacts(tmp_path):
    class Backend:
        backend_name = "ego"

        def observe(self, screenshot=False):
            raise KeyboardInterrupt()

        def close(self):
            pass

    with pytest.raises(KeyboardInterrupt):
        Agent("https://example.test", "Open it", backend=Backend(), record_dir=tmp_path)
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["status"] == "error"
    assert metrics["error_type"] == "KeyboardInterrupt"
    assert (tmp_path / "trace.json").exists()
