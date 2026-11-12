"""Measurement finalization contracts; no browser, model service or external site."""

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import jev_ultrafast
from examples import flights
from jev_ultrafast import browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def measurement(monkeypatch, tmp_path):
    cached = {"url": flights.URL, "text": "Cached successful result", "actions": []}
    fresh = {"url": flights.URL, "text": "Fresh result", "actions": []}
    state = {"page": cached, "history": [{"action": "Search", "kind": "click"}],
             "elapsed_ms": 100, "status": "done"}
    agent = Mock()
    agent.snapshot.side_effect = lambda: dict(state)
    agent.run.return_value = iter([state])

    def observe(**_kwargs):
        browser.cdp("Runtime.evaluate")
        return fresh

    def call(method):
        assert method == "Browser.getVersion"
        browser.cdp(method)
        return {"product": "Chrome/test"}

    agent.browser.observe.side_effect = observe
    agent.browser.call.side_effect = call
    verifier = Mock(return_value={"passed": True, "checks": {"results": True}})
    monkeypatch.setattr(jev_ultrafast, "Agent", Mock(return_value=agent))
    # Restore the script's module-level CDP timing wrapper after each test.
    monkeypatch.setattr(browser, "cdp", Mock(return_value={}))
    monkeypatch.setattr(flights, "verify", verifier)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["measure_flights.py", "--source", str(ROOT), "--output", str(output)])

    def run():
        runpy.run_path(str(ROOT / "scripts/measure_flights.py"), run_name="__main__")

    return SimpleNamespace(agent=agent, state=state, fresh=fresh, verify=verifier, run=run,
                           output=output / "state.json")


@pytest.mark.parametrize("passed", [True, False])
def test_fresh_verification_keeps_its_result_and_excludes_final_reads_from_timing(measurement, passed):
    measurement.verify.return_value = {"passed": passed, "checks": {"results": passed}}
    measurement.run()
    saved = json.loads(measurement.output.read_text())
    assert saved["history"] == measurement.state["history"]
    assert saved["page"] == measurement.state["page"]
    assert saved["final_page"] == measurement.fresh
    assert saved["verification"]["passed"] is passed
    assert saved["error"] is None
    assert saved["browser_version"] == "Chrome/test"
    assert saved["cdp"] == {}  # Final observation/version queries are outside the measured run.
    assert saved["source_hashes"] and saved["task_hash"]
    measurement.verify.assert_called_once_with(measurement.fresh)
    measurement.agent.close.assert_called_once_with()


@pytest.mark.parametrize("phase", ["observation", "verification"])
def test_final_failure_preserves_trace_and_original_run_error(measurement, phase):
    def failed_run():
        yield measurement.state
        raise RuntimeError("provider failed")

    measurement.agent.run.side_effect = failed_run
    failure = RuntimeError("final check failed")
    if phase == "observation":
        measurement.agent.browser.observe.side_effect = failure
    else:
        measurement.verify.side_effect = failure
    measurement.agent.browser.call.side_effect = RuntimeError("version unavailable")

    with pytest.raises(RuntimeError, match="final check failed"):
        measurement.run()
    saved = json.loads(measurement.output.read_text())
    assert saved["history"] == measurement.state["history"]
    assert saved["error"] == "RuntimeError: provider failed"
    assert saved["verification"] == {"passed": False, "error": "RuntimeError: final check failed"}
    assert saved.get("final_page") == (None if phase == "observation" else measurement.fresh)
    assert saved["browser_version"] is None
    assert saved["browser_version_error"] == "RuntimeError: version unavailable"
    if phase == "observation":
        measurement.verify.assert_not_called()
    measurement.agent.close.assert_called_once_with()


def test_version_failure_keeps_fresh_verification_and_still_fails(measurement):
    measurement.agent.browser.call.side_effect = RuntimeError("version unavailable")
    with pytest.raises(RuntimeError, match="version unavailable"):
        measurement.run()
    saved = json.loads(measurement.output.read_text())
    assert saved["verification"]["passed"] is True
    assert saved["final_page"] == measurement.fresh
    assert saved["error"] is None
    assert saved["browser_version"] is None
    assert saved["browser_version_error"] == "RuntimeError: version unavailable"
    measurement.agent.close.assert_called_once_with()


def test_write_failure_still_closes_agent(measurement, monkeypatch):
    write = Path.write_text

    def fail_write(path, *args, **kwargs):
        if path == measurement.output:
            raise OSError("disk full")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(OSError, match="disk full"):
        measurement.run()
    measurement.agent.close.assert_called_once_with()


def test_cleanup_failure_does_not_erase_saved_trace(measurement):
    measurement.agent.close.side_effect = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        measurement.run()
    saved = json.loads(measurement.output.read_text())
    assert saved["history"] == measurement.state["history"]
    assert saved["verification"]["passed"] is True
    measurement.agent.close.assert_called_once_with()
