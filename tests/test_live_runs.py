"""Offline tests for the live-run measurement harness. No browser, no API, no network."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"


def load_script(name):
    """Load scripts/<name>.py by path; scripts/ is not an installed package."""
    path = SCRIPTS / f"{name}.py"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(f"jev_live_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live_runs = load_script("live_runs")
live_summary = load_script("live_summary")


def tree(root, files):
    for relative, content in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


class FakeTransport:
    """A real child process stands in for the Ego runtime so pid checks are honest."""

    def __init__(self, process):
        self.process = process
        self.runtime_spawn_count = 1
        self.cleanup_status = {
            "graceful_exit": True,
            "forced_terminate": False,
            "forced_kill": False,
            "orphan_process_remaining": False,
        }


class FakeBackend:
    backend_name = "ego"
    runtime_spawn_count = 1
    orphan_process_remaining = False
    graceful_exit = True
    forced_terminate = False
    forced_kill = False
    ego_timings = {"observe": {"count": 1, "total_ms": 4, "max_ms": 4, "last_ms": 4}, "ego_total_operation_ms": 4}

    def __init__(self, process):
        self.transport = FakeTransport(process)
        self.metrics = {
            "runtime_spawn_count": 1,
            "cleanup_status": dict(self.transport.cleanup_status),
            "ego_timings": self.ego_timings,
        }

    def observe(self, screenshot=False):
        return {
            "url": "https://example.test/",
            "actions": [{"id": "e1", "kind": "click", "label": "Open", "node": 1}],
        }

    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        return {"executed": action["id"]}

    def close(self):
        process = self.transport.process
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


class FakeAgent:
    """Minimal Agent substitute: the same observe/fresh/act/close surface the harness uses."""

    def __init__(self, *, backend=None, hang=False, orphan=False, record_dir=None):
        from jev_ultrafast.metrics import RunMetrics

        self.browser = backend
        self.hang = hang
        self.orphan = orphan
        self.metrics = RunMetrics("ego")
        self.record_dir = record_dir
        self._states = iter(("ready", "done"))

    def run(self):
        if self.hang:
            while True:
                live_runs.time.sleep(0.02)
        while True:
            try:
                status = next(self._states)
            except StopIteration:
                return
            self.metrics.record_jev(5)
            if status == "done":
                self.metrics.record_action_attempt("click")
                self.metrics.record_action_success("click")
            self.metrics.finish(status)
            yield {"status": status}

    def snapshot(self):
        return {"status": self.metrics.status, "metrics": self.metrics.snapshot()}

    def close(self):
        deadline = time.monotonic() + 5
        while self.hang and time.monotonic() < deadline:
            live_runs.time.sleep(0.02)
        if not self.orphan:
            self.browser.close()
        self.metrics.finish("done")
        self.metrics.merge_backend(self.browser)
        if self.record_dir:
            folder = Path(self.record_dir)
            folder.mkdir(parents=True, exist_ok=True)
            self.metrics.write(folder / "metrics.json")
            (folder / "trace.json").write_text(json.dumps({"status": "done"}) + "\n")


def fake_agent_factory(monkeypatch, *, hang=False, orphan=False):
    """Mirror the harness: each created fake Agent writes metrics.json into its run directory."""
    process = subprocess.Popen(["sleep", "30"])
    created = []

    def factory(**kwargs):
        agent = FakeAgent(backend=FakeBackend(process), hang=hang, orphan=orphan, **kwargs)
        created.append((agent, process))
        return agent

    monkeypatch.setattr(live_runs.time, "sleep", lambda _seconds: None)
    return factory, created, process


def finished_process():
    process = subprocess.Popen(["sleep", "0"])
    process.wait(timeout=10)
    return process


def test_pid_alive_is_false_for_a_dead_and_true_for_a_live_process():
    dead = finished_process()
    assert live_runs.pid_alive(dead.pid) is False
    assert live_runs.pid_alive(0) is False
    assert live_runs.pid_alive(None) is False
    assert live_runs.pid_alive("not-a-pid") is False

    alive = subprocess.Popen(["sleep", "30"])
    try:
        assert live_runs.pid_alive(alive.pid) is True
        assert live_runs.wait_for_pid_exit(alive.pid, timeout=0.05) is False
    finally:
        alive.kill()
        alive.wait(timeout=10)
    assert live_runs.pid_alive(alive.pid) is False


def test_an_unreaped_zombie_child_is_not_counted_as_alive():
    zombie = subprocess.Popen(["sleep", "30"])
    zombie.kill()
    time.sleep(0.3)
    os.kill(zombie.pid, 0)  # an unreaped child still answers signal 0
    assert live_runs.pid_alive(zombie.pid) is False  # ... and pid_alive reaps it
    with pytest.raises(ChildProcessError):
        os.waitpid(zombie.pid, os.WNOHANG)  # nothing left to reap
    zombie.wait(timeout=10)
    assert live_runs.pid_alive(zombie.pid) is False


def test_extract_ego_child_pids_reads_the_live_transport_process():
    process = subprocess.Popen(["sleep", "30"])
    try:
        backend = FakeBackend(process)
        assert live_runs.extract_ego_child_pids(backend) == [process.pid]
    finally:
        process.kill()
        process.wait(timeout=10)
    assert live_runs.extract_ego_child_pids(None) == []
    assert live_runs.extract_ego_child_pids(object()) == []


def test_process_evidence_flags_a_surviving_runtime():
    process = subprocess.Popen(["sleep", "30"])
    try:
        evidence = live_runs.collect_process_evidence("r1", "ego", [process.pid], live_runs.utc_now())
        assert evidence["transport_pids"] == [process.pid]
        assert evidence["pid_alive_after_close"][str(process.pid)] is True
        assert evidence["orphan_process_remaining"] is True
        assert evidence["post_close_scan"]["pattern"] == "ego-browser nodejs"
        assert isinstance(evidence["post_close_scan"]["matches"], list)
    finally:
        process.kill()
        process.wait(timeout=10)
    evidence = live_runs.collect_process_evidence("r2", "browser_harness", [], live_runs.utc_now())
    assert evidence["orphan_process_remaining"] is None
    assert evidence["post_close_scan"]["matches"] is None


def test_watchdog_raises_a_run_timeout():
    with pytest.raises(live_runs.RunTimeout):
        with live_runs.watchdog(0.05):
            time.sleep(0.3)


def test_version_manifest_implementation_hash_is_stable_and_content_sensitive(tmp_path):
    source = tree(
        tmp_path / "source",
        {
            "jev_ultrafast/__init__.py": "from .agent import Agent\n",
            "jev_ultrafast/agent.py": "class Agent: ...\n",
            "jev_ultrafast/backends/ego.py": "class EgoBrowserBackend: ...\n",
            "jev_ultrafast/snapshot.js": "const state = 1;\n",
            "README.md": "not part of the implementation hash\n",
        },
    )
    first = live_runs.version_manifest(source)
    assert first["implementation_hash"] == live_runs.version_manifest(source)["implementation_hash"]
    assert first["python"] == sys.version.split()[0]
    assert first["ego_browser"] is None or Path(first["ego_browser"]).exists()
    assert set(first) == {"git_commit", "git_dirty", "implementation_hash", "python", "ego_browser"}

    (source / "jev_ultrafast/agent.py").write_text("class Agent:  # changed\n")
    changed = live_runs.version_manifest(source)["implementation_hash"]
    assert changed != first["implementation_hash"]

    (source / "jev_ultrafast/agent.py").write_text("class Agent: ...\n")
    assert live_runs.version_manifest(source)["implementation_hash"] == first["implementation_hash"]

    (source / "jev_ultrafast/backends/extra.py").write_text("VALUE = 2\n")
    assert live_runs.version_manifest(source)["implementation_hash"] != first["implementation_hash"]
    (source / "jev_ultrafast/backends/extra.py").unlink()
    assert live_runs.version_manifest(source)["implementation_hash"] == first["implementation_hash"]

    (source / "README.md").write_text("not hashed at all\n")
    assert live_runs.version_manifest(source)["implementation_hash"] == first["implementation_hash"]


def test_version_manifest_of_this_project_is_sha256_hex():
    manifest = live_runs.version_manifest(PROJECT_ROOT)
    assert len(manifest["implementation_hash"]) == 64
    int(manifest["implementation_hash"], 16)
    assert manifest["git_commit"]
    assert manifest["ego_browser"] == shutil.which("ego-browser")
    assert live_runs.implementation_hash(PROJECT_ROOT) != live_runs.implementation_hash(SCRIPTS)


def test_build_plan_normal_mode_uses_label_index_directories(tmp_path):
    plan = live_runs.build_plan(
        url="https://example.test/",
        goal="Open it",
        backends=["ego"],
        runs=2,
        record_root=tmp_path / "recordings",
        label="wikipedia",
        source_root=str(PROJECT_ROOT),
    )
    assert [run["run_id"] for run in plan] == ["wikipedia-1", "wikipedia-2"]
    assert [run["run_index"] for run in plan] == [1, 2]
    assert {run["backend"] for run in plan} == {"ego"}
    assert [Path(run["record_dir"]).name for run in plan] == ["wikipedia-1", "wikipedia-2"]


def test_build_plan_ab_mode_interleaves_one_run_per_backend_per_round(tmp_path):
    plan = live_runs.build_plan(
        url="https://example.test/",
        goal="Open it",
        backends=["browser_harness", "ego"],
        runs=3,
        record_root=tmp_path / "recordings",
        label="wikipedia",
        source_root=str(PROJECT_ROOT),
        ab=True,
    )
    assert [run["backend"] for run in plan] == [
        "browser_harness",
        "ego",
        "browser_harness",
        "ego",
        "browser_harness",
        "ego",
    ]
    assert [run["run_id"] for run in plan] == [
        "wikipedia-browser_harness-1",
        "wikipedia-ego-1",
        "wikipedia-browser_harness-2",
        "wikipedia-ego-2",
        "wikipedia-browser_harness-3",
        "wikipedia-ego-3",
    ]
    assert [run["run_index"] for run in plan] == [1, 1, 2, 2, 3, 3]


def test_dry_run_cli_prints_the_matrix_without_starting_anything(capsys):
    assert live_runs.main(["--dry-run", "--url", "https://example.test/", "--goal", "Open it", "--runs", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["ab"] is False
    assert payload["backend_order"] == ["browser_harness"]
    assert [run["run_id"] for run in payload["plan"]] == ["example.test-1", "example.test-2"]

    assert (
        live_runs.main(
            [
                "--dry-run",
                "--ab",
                "--url",
                "https://example.test/",
                "--goal",
                "Open it",
                "--backends",
                "browser_harness,ego",
                "--runs",
                "2",
                "--label",
                "wikipedia",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ab"] is True
    assert payload["backend_order"] == ["browser_harness", "ego"]
    assert payload["runs"] == 4
    assert [run["backend"] for run in payload["plan"]] == ["browser_harness", "ego", "browser_harness", "ego"]


def test_dry_run_rejects_ab_with_a_single_backend():
    with pytest.raises(SystemExit):
        live_runs.main(["--dry-run", "--ab", "--url", "https://example.test/", "--goal", "Open it"])


def test_run_once_writes_every_required_artifact(tmp_path, monkeypatch):
    factory, created, process = fake_agent_factory(monkeypatch)
    try:
        run = live_runs.build_plan(
            url="https://example.test/",
            goal="Open it",
            backends=["ego"],
            runs=1,
            record_root=tmp_path / "recordings",
            label="demo",
            source_root=str(PROJECT_ROOT),
        )[0]
        summary = live_runs.run_once(
            run, agent_factory=lambda: factory(record_dir=run["record_dir"]), timeout_seconds=5
        )
        record_dir = Path(run["record_dir"])
        for name in ("summary.json", "process_evidence.json", "version_manifest.json"):
            assert (record_dir / name).exists(), name
        assert json.loads((record_dir / "summary.json").read_text())["run_id"] == "demo-1"
        for key in (
            "run_id",
            "label",
            "backend",
            "url",
            "goal",
            "status",
            "wall_elapsed_ms",
            "policy_elapsed_ms",
            "jev_calls",
            "browser_actions_attempted",
            "browser_actions_succeeded",
            "action_success_rate",
            "stale_count",
            "ego_operation_ms",
            "ego_observe_ms",
            "ego_act_ms",
            "ego_fresh_ms",
            "text_helper_calls",
            "error_type",
            "runtime_spawn_count",
            "orphan_process_remaining",
            "graceful_exit",
            "forced_terminate",
            "forced_kill",
            "pid_alive_after_close",
            "ego_child_pids",
            "wall_clock_utc",
            "git_commit",
            "implementation_hash",
        ):
            assert key in summary, key
        assert summary["ego_child_pids"] == [process.pid]
        assert summary["pid_alive_after_close"] is False
        assert summary["status"] == "done"
        assert summary["jev_calls"] == 2
        assert summary["action_success_rate"] == 1.0
        evidence = json.loads((record_dir / "process_evidence.json").read_text())
        assert evidence["transport_pids"] == [process.pid]
        assert evidence["pid_alive_after_close"][str(process.pid)] is False
        assert evidence["surviving_pids"] == []
        assert evidence["post_close_scan"]["pattern"] == "ego-browser nodejs"
        # Unrelated Ego processes on this machine are reported honestly, but only
        # the ones this run started can make it an orphan.
        assert summary["orphan_process_remaining"] == evidence["orphan_process_remaining"]
        assert evidence["post_close_scan"]["baseline"] is not None
        assert evidence["orphan_process_remaining"] is bool(
            evidence["post_close_scan"]["attributed"] or evidence["surviving_pids"]
        )
        for payload in (summary, evidence):
            text = json.dumps(payload).lower()
            assert "api_key" not in text and "authorization" not in text and "bearer" not in text
        assert json.loads((record_dir / "version_manifest.json").read_text())["implementation_hash"] == summary[
            "implementation_hash"
        ]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_run_once_records_a_watchdog_failure_and_continues(tmp_path, monkeypatch):
    factory, created, process = fake_agent_factory(monkeypatch, hang=True)
    try:
        run = live_runs.build_plan(
            url="https://example.test/",
            goal="Open it",
            backends=["ego"],
            runs=1,
            record_root=tmp_path / "recordings",
            label="hung",
            source_root=str(PROJECT_ROOT),
        )[0]
        started = time.perf_counter()
        summary = live_runs.run_once(
            run, agent_factory=lambda: factory(record_dir=run["record_dir"]), timeout_seconds=0.25, kill_grace=2
        )
        assert time.perf_counter() - started < 30  # the watchdog, not the hang, ended the run
        record_dir = Path(run["record_dir"])
        failure = json.loads((record_dir / "runner_failure.json").read_text())
        assert failure["run_id"] == "hung-1"
        assert failure["timeout_seconds"] == 0.25
        assert failure["phase"] == "run"
        assert failure["exception"] == "RunTimeout"
        assert summary["runner_failure"] == failure
        assert "close exceeded" in summary["close_failure"]  # the forced close timed out too
        assert summary["close_elapsed_ms"] >= 250
        assert summary["status"] == "running"  # the interrupted run never reported a final status
        assert summary["pid_alive_after_close"] is False
        assert process.poll() is not None  # the hung runtime was reaped, not left behind
        assert (record_dir / "process_evidence.json").exists()
        assert (record_dir / "summary.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_run_once_surfaces_an_orphaned_runtime(tmp_path, monkeypatch):
    factory, created, process = fake_agent_factory(monkeypatch, orphan=True)
    try:
        run = live_runs.build_plan(
            url="https://example.test/",
            goal="Open it",
            backends=["ego"],
            runs=1,
            record_root=tmp_path / "recordings",
            label="orphan",
            source_root=str(PROJECT_ROOT),
        )[0]
        summary = live_runs.run_once(
            run, agent_factory=lambda: factory(record_dir=run["record_dir"]), timeout_seconds=30
        )
        record_dir = Path(run["record_dir"])
        assert summary["pid_alive_after_close"] is True
        assert summary["orphan_process_remaining"] is True
        assert summary["ego_child_pids"] == [process.pid]
        evidence = json.loads((record_dir / "process_evidence.json").read_text())
        assert evidence["surviving_pids"] == [process.pid]
        assert evidence["notes"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_run_once_records_a_construction_failure_before_an_agent_exists(tmp_path):
    def broken_factory():
        raise RuntimeError("Ego backend is unavailable")

    run = live_runs.build_plan(
        url="https://example.test/",
        goal="Open it",
        backends=["ego"],
        runs=1,
        record_root=tmp_path / "recordings",
        label="nobrowser",
        source_root=str(PROJECT_ROOT),
    )[0]
    summary = live_runs.run_once(run, agent_factory=broken_factory, timeout_seconds=5)
    failure = json.loads((Path(run["record_dir"]) / "runner_failure.json").read_text())
    assert failure["phase"] == "construct"
    assert failure["exception"] == "RuntimeError"
    assert failure["message"] == "Ego backend is unavailable"
    assert summary["status"] == "not_started"  # the Agent never reached a model outcome
    assert summary["ego_child_pids"] == []
    assert summary["pid_alive_after_close"] is None  # no runtime pid was ever observed
    assert summary["orphan_process_remaining"] is False
    assert (Path(run["record_dir"]) / "summary.json").exists()
    assert not (Path(run["record_dir"]) / "metrics.json").exists()  # no Agent, no metrics


def test_pid_alive_after_close_reports_unknown_without_a_tracked_pid():
    assert live_runs.pid_alive_after_close({}) is None
    assert live_runs.pid_alive_after_close({"1": False, "2": False}) is False
    assert live_runs.pid_alive_after_close({"1": False, "2": True}) is True


def test_aggregate_math_includes_single_sample_and_empty_group():
    rows = [
        {"wall_elapsed_ms": 100, "jev_calls": 2, "action_success_rate": 1.0, "stale_count": 0, "ego_operation_ms": 10},
        {"wall_elapsed_ms": 300, "jev_calls": 4, "action_success_rate": 0.5, "stale_count": 2, "ego_operation_ms": 30},
    ]
    aggregate = live_summary.aggregate(rows)
    assert aggregate["wall_elapsed_ms"] == {"median": 200, "min": 100, "max": 300, "count": 2}
    assert aggregate["jev_calls"]["median"] == 3
    assert aggregate["action_success_rate"]["min"] == 0.5
    assert aggregate["stale_count"]["max"] == 2
    assert aggregate["ego_operation_ms"]["median"] == 20

    single = live_summary.aggregate([{"wall_elapsed_ms": 7, "jev_calls": None, "action_success_rate": None}])
    assert single["wall_elapsed_ms"] == {"median": 7, "min": 7, "max": 7, "count": 1}
    assert single["jev_calls"] == {"median": None, "min": None, "max": None, "count": 0}

    empty = live_summary.aggregate([])
    for key in live_summary.SUMMARY_METRIC_KEYS:
        assert empty[key] == {"median": None, "min": None, "max": None, "count": 0}


def test_summary_reader_groups_runs_and_reports_numbers_only(tmp_path, capsys):
    root = tmp_path / "recordings"
    for name, backend, wall, rate, stale, ego_ms in (
        ("wikipedia-1", "ego", 1200, 1.0, 0, 300),
        ("wikipedia-2", "ego", 1800, 0.5, 2, 500),
    ):
        directory = root / name
        directory.mkdir(parents=True)
        live_runs.write_json(
            directory / "summary.json",
            {
                "run_id": name,
                "label": "wikipedia",
                "backend": backend,
                "status": "done",
                "wall_elapsed_ms": wall,
                "jev_calls": 3,
                "action_success_rate": rate,
                "stale_count": stale,
                "ego_operation_ms": ego_ms,
            },
        )
    rows = [live_summary.load_run(directory) for directory in live_summary.discover_runs(root)]
    assert all(row is not None for row in rows)
    report = live_summary.build_report(rows)
    assert list(report) == ["wikipedia\tego"]
    assert report["wikipedia\tego"]["aggregate"]["wall_elapsed_ms"]["median"] == 1500

    assert live_summary.main([str(root)]) == 0
    output = capsys.readouterr().out
    assert "wikipedia / ego" in output
    assert "median" in output and "1500" in output
    assert "faster" not in output.lower()

    assert live_summary.main([str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["aggregate"]["wikipedia\tego"]["aggregate"]["wall_elapsed_ms"]["min"] == 1200


def test_summary_reader_falls_back_to_metrics_json(tmp_path):
    directory = tmp_path / "ego-live-1"
    directory.mkdir()
    (directory / "metrics.json").write_text(
        json.dumps(
            {
                "backend": "ego",
                "status": "done",
                "wall_elapsed_ms": 10,
                "ego_total_operation_ms": 4,
                "browser_actions_attempted": 2,
                "browser_actions_succeeded": 1,
            }
        )
    )
    row = live_summary.load_run(directory)
    assert row["source"] == "metrics.json"
    assert row["ego_operation_ms"] == 4
    assert row["action_success_rate"] == 0.5
    assert row["label"] == "unlabeled"


def test_summary_reader_ignores_directories_without_artifacts(tmp_path):
    (tmp_path / "empty").mkdir()
    assert live_summary.discover_runs(tmp_path) == [tmp_path / "empty"]
    assert live_summary.load_run(tmp_path / "empty") is None


def test_sanitize_slug_and_run_directory_names(tmp_path):
    assert live_runs.sanitize_slug("wikipedia v2/run") == "wikipedia-v2-run"
    assert live_runs.sanitize_slug("///") == "run"
    assert live_runs.run_directory_name("wiki", "ego", 7, False) == "wiki-7"
    assert live_runs.run_directory_name("wiki", "browser_harness", 7, True) == "wiki-browser_harness-7"


def test_kill_process_tree_terminates_a_live_process():
    process = subprocess.Popen(["sleep", "30"])
    try:
        assert live_runs.kill_process_tree(process.pid, grace=3, reap=True) is True
        process.wait(timeout=10)
        assert live_runs.pid_alive(process.pid) is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_kill_process_tree_ignores_unsafe_targets():
    assert live_runs.kill_process_tree(0) is False
    assert live_runs.kill_process_tree(-1) is False
    assert live_runs.kill_process_tree("nope") is False


def test_kill_runtime_reaps_a_hung_runtime_and_its_children():
    parent = subprocess.Popen(["sleep", "30"], start_new_session=True)
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    assert live_runs.pid_alive(parent.pid)
    try:
        report = live_runs.kill_runtime([parent.pid, child.pid], grace=3)
        assert set(report["killed_pids"]) == {parent.pid, child.pid}
        assert live_runs.pid_alive(parent.pid) is False
        assert live_runs.pid_alive(child.pid) is False
    finally:
        for process in (parent, child):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def test_process_group_ids_skips_pids_without_a_process_table():
    # The sandboxed ps is unavailable here; the honest result is no group to kill.
    assert live_runs.process_group_ids([os.getpid()]) in ([], [os.getpgrp()])


def test_scan_ego_processes_returns_pids_without_shell_interpretation():
    matches = live_runs.scan_ego_processes()
    assert isinstance(matches, list)
    assert all(isinstance(pid, int) for pid in matches)


def test_watchdog_without_a_deadline_is_a_no_op():
    with live_runs.watchdog(0):
        pass
    assert os.getpid() > 0
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0