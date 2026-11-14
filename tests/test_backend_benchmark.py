"""Offline contracts for the final benchmark fixtures, expectations, and scoring."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import benchmark_suite  # noqa: E402
import live_runs  # noqa: E402
import run_backend_benchmark as runner  # noqa: E402
import score_backend_benchmark as scorer  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_every_registered_fixture_exists():
    for task in benchmark_suite.TASKS:
        if task.get("fixture"):
            assert (FIXTURES / task["fixture"]).is_file(), task["fixture"]


def test_fixture_tasks_have_goals_and_validators():
    for task in benchmark_suite.TASKS:
        assert task["goal"].strip()
        assert task["validator"].strip().endswith(")()")
        assert task["runs"] >= 1
        assert task.get("url") or task.get("fixture")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to syntax-check browser scripts")
def test_validator_javascript_parses(tmp_path):
    for task in benchmark_suite.TASKS:
        target = tmp_path / f"{task['key']}.js"
        target.write_text("void " + task["validator"] + ";\n")
        done = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
        assert done.returncode == 0, f"{task['key']}: {done.stderr}"


def test_fixture_server_serves_the_benchmark_page():
    with benchmark_suite.fixture_server() as port:
        import urllib.request

        url = f"http://127.0.0.1:{port}/jev_complex_benchmark.html"
        body = urllib.request.urlopen(url, timeout=5).read().decode()
    assert "Travel Research Console" in body
    assert 'data-submitted="false"' in body


def test_fixture_url_uses_the_loopback_port():
    task = benchmark_suite.task_by_key("complex")
    assert benchmark_suite.fixture_url(1234, task) == "http://127.0.0.1:1234/jev_complex_benchmark.html"
    assert benchmark_suite.fixture_url(1234, benchmark_suite.task_by_key("example")).startswith("https://example.com")


def test_complex_expectation_follows_the_canonical_order():
    task = benchmark_suite.task_by_key("complex")
    page = {"url": "http://127.0.0.1/x", "title": "t", "actions": []}
    progress = benchmark_suite.Progress()
    assert task["expect"](page, progress)["operation"] == "TYPE_TEXT"
    assert task["expect"](page, progress)["target"] == "Destination city"

    progress.observe_action({"kind": "fill", "action": "Destination city", "choice": "e1"})
    assert task["expect"](page, progress)["operation"] == "SELECT"

    progress.observe_action({"kind": "select", "action": "Category → Flights", "choice": "e2:2"})
    assert task["expect"](page, progress)["operation"] == "CLICK"
    assert task["expect"](page, progress)["target"] == "High"
    assert task["expect"](page, progress)["alternatives"] == [
        {"operation": "WAIT", "target": None}
    ]

    progress.observe_action({"kind": "click", "action": "High", "choice": "e5"})
    without_continue = task["expect"](page, progress)
    assert without_continue["operation"] == "WAIT"
    with_continue = task["expect"](
        {"url": "u", "title": "t", "actions": [{"id": "e9", "kind": "click", "label": "Continue"}]},
        progress,
    )
    assert with_continue["operation"] == "CLICK"
    assert with_continue["target"] == "Continue"

    progress.observe_action({"kind": "click", "action": "Continue", "choice": "e9"})
    assert task["expect"](page, progress)["operation"] == "SCROLL_DOWN"
    scrolled_review = {
        "url": "u",
        "title": "t",
        "actions": [{"id": "e12", "kind": "click", "label": "Final Review"}],
    }
    assert task["expect"](scrolled_review, progress)["operation"] == "CLICK"
    assert task["expect"](scrolled_review, progress)["target"] == "Final Review"

    progress.observe_action({"kind": "click", "action": "Final Review", "choice": "e12"})
    assert task["expect"](page, progress)["target"] == "Submit request"

    progress.observe_action({"kind": "click", "action": "Submit request", "choice": "e13"})
    final = task["expect"](page, progress)
    assert final["operation"] == "DONE"
    assert final["goal_satisfied"] is True


def test_progress_counts_distractors_without_satisfying_requirements():
    progress = benchmark_suite.Progress()
    progress.observe_action({"kind": "fill", "action": "Search requests", "choice": "e1"})
    progress.observe_action({"kind": "click", "action": "Save and continue", "choice": "e2"})
    progress.observe_action({"kind": "click", "action": "Submit request", "choice": "e3"})
    assert progress.destination_filled is False
    assert progress.continued is False
    assert progress.submitted is True
    assert progress.distractor_steps == ["search requests", "save and continue"]


def _page():
    return {
        "url": "http://127.0.0.1:9/jev_complex_benchmark.html",
        "title": "Travel Research Request",
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Destination city", "role": "textbox", "value": "", "node": 1},
            {"id": "e2", "kind": "fill", "label": "Search requests", "role": "searchbox", "value": "", "node": 2},
            {"id": "e3", "kind": "click", "label": "Help", "role": "button", "value": "", "node": 3},
            {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
        ],
    }


def _decision():
    return {
        "choice": "e1",
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 0.9,
        "latency_ms": 120,
        "operation_probabilities": {"TYPE_TEXT": 0.8, "CLICK": 0.2},
        "target_probabilities": {"1": 0.7, "2": 0.3},
        "usage": {},
    }


def _entry():
    return {
        "kind": "fill",
        "action": "Destination city",
        "choice": "e1",
        "text": "Zurich to London",
        "page_changed": True,
        "url": "http://127.0.0.1:9/jev_complex_benchmark.html",
        "executed_ms": 500,
    }


def test_step_record_captures_operation_and_target_probabilities():
    task = benchmark_suite.task_by_key("complex")
    record = runner.build_step_record(1, _decision(), _entry(), _page(), benchmark_suite.Progress(), task)
    assert record["operation_expected"] == "TYPE_TEXT"
    assert record["operation_top1"] == "TYPE_TEXT"
    assert record["operation_top1_correct"] is True
    assert record["operation_correct_probability"] == pytest.approx(0.8)
    assert record["operation_second"] == "CLICK"
    assert record["operation_margin"] == pytest.approx(0.6, abs=1e-6)
    assert record["target_expected"] == "Destination city"
    assert record["target_top1"] == "Destination city"
    assert record["target_top1_correct"] is True
    assert record["target_correct_probability"] == pytest.approx(0.7)
    assert record["target_second"] == "Search requests"
    assert record["target_margin"] == pytest.approx(0.4, abs=1e-6)
    assert record["target_scored"] is True
    assert record["operation_brier"] == pytest.approx((0.2**2 + 0.2**2) / 2, abs=1e-6)


def test_step_record_marks_a_wrong_operation_and_an_unknown_target():
    task = benchmark_suite.task_by_key("complex")
    decision = {**_decision(), "choice": "e3", "operation": "CLICK", "target": "3",
                "operation_probabilities": {"CLICK": 0.6, "TYPE_TEXT": 0.4},
                "target_probabilities": {"1": 0.5, "2": 0.5}}
    entry = {**_entry(), "kind": "click", "action": "Help", "choice": "e3", "text": None}
    record = runner.build_step_record(1, decision, entry, _page(), benchmark_suite.Progress(), task)
    assert record["operation_top1_correct"] is False
    assert record["operation_correct_probability"] == pytest.approx(0.4)
    assert record["target_top1_correct"] is False
    assert record["target_correct_probability"] == 0.0
    assert record["operation_acceptable"] is False


def test_step_record_marks_an_acceptable_alternative():
    task = benchmark_suite.task_by_key("complex")
    progress = benchmark_suite.Progress()
    progress.observe_action({"kind": "fill", "action": "Destination city", "choice": "e1"})
    progress.observe_action({"kind": "select", "action": "Category → Flights", "choice": "e2:2"})
    decision = {**_decision(), "choice": "wait", "operation": "WAIT", "target": None,
                "operation_probabilities": {"WAIT": 0.55, "CLICK": 0.45},
                "target_probabilities": {}}
    entry = {**_entry(), "kind": "wait", "action": "Wait for the page to update", "choice": "wait", "text": None}
    record = runner.build_step_record(3, decision, entry, _page(), progress, task)
    assert record["operation_expected"] == "CLICK"
    assert record["operation_top1_correct"] is False
    assert record["operation_acceptable"] is True
    assert record["unnecessary_wait"] is False
    assert record["target_top1_correct"] is None
    assert record["target_scored"] is False


def test_step_record_flags_a_premature_done():
    task = benchmark_suite.task_by_key("complex")
    decision = {**_decision(), "choice": "DONE", "operation": "DONE", "target": None,
                "operation_probabilities": {"DONE": 0.9, "TYPE_TEXT": 0.1},
                "target_probabilities": {}}
    entry = {**_entry(), "kind": "done", "action": "DONE", "choice": "DONE", "text": None}
    record = runner.build_step_record(1, decision, entry, _page(), benchmark_suite.Progress(), task)
    assert record["operation_top1"] == "DONE"
    assert record["premature_done"] is True
    assert record["target_top1_correct"] is None


def test_example_expectation_switches_to_done_on_the_iana_page():
    task = benchmark_suite.task_by_key("example")
    start = {"url": "https://example.com/", "title": "Example Domain",
             "actions": [{"id": "e1", "kind": "click", "label": "Learn more"}]}
    first = task["expect"](start, benchmark_suite.Progress())
    assert first["operation"] == "CLICK"
    assert first["target"] == "Learn more"
    assert first["goal_satisfied"] is False
    landed = {"url": "https://www.iana.org/help/example-domains", "title": "Example Domains", "actions": []}
    final = task["expect"](landed, benchmark_suite.Progress())
    assert final["operation"] == "DONE"
    assert final["goal_satisfied"] is True


def test_wikipedia_expectation_uses_the_observed_page_state():
    task = benchmark_suite.task_by_key("wikipedia")
    main = {"url": "https://en.wikipedia.org/wiki/Main_Page", "title": "Wikipedia", "actions": []}
    assert task["expect"](main, benchmark_suite.Progress())["operation"] == "TYPE_TEXT"
    typed = {
        "url": "https://en.wikipedia.org/wiki/Main_Page",
        "title": "Wikipedia",
        "actions": [{"id": "e1", "kind": "fill", "label": "Search", "value": "Gödel incompleteness"}],
    }
    assert task["expect"](typed, benchmark_suite.Progress())["operation"] == "CLICK"
    article = {
        "url": "https://en.wikipedia.org/wiki/G%C3%B6del%27s_incompleteness_theorems",
        "title": "Gödel's incompleteness theorems - Wikipedia",
        "actions": [],
    }
    final = task["expect"](article, benchmark_suite.Progress())
    assert final["operation"] == "DONE"
    assert final["goal_satisfied"] is True


def test_plan_interleaves_backends_inside_each_repetition():
    tasks = [benchmark_suite.task_by_key("example")]
    plan = runner.build_plan(tasks, ["browser_harness", "ego"], "recordings/x")
    assert [item["backend"] for item in plan] == [
        "browser_harness", "ego", "browser_harness", "ego", "browser_harness", "ego",
    ]
    assert [item["run_index"] for item in plan] == [1, 1, 2, 2, 3, 3]
    assert plan[0]["run_id"] == "example-browser_harness-1"


def _write_run(root, run_id, backend, task, **overrides):
    directory = Path(root) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "task": task,
        "backend": backend,
        "status": "done",
        "validator_pass": True,
        "wall_elapsed_ms": 5000,
        "jev_calls": 6,
        "browser_actions_attempted": 5,
        "browser_actions_succeeded": 5,
        "stale_count": 0,
        "backend_operation_ms": 900,
        "backend_startup_ms": 400,
        "backend_cleanup_ms": 50,
        "runtime_spawn_count": 1 if backend == "ego" else None,
        "orphan_process_remaining": False if backend == "ego" else None,
        "pid_alive_after_close": False if backend == "ego" else None,
        "created_test_tab": True if backend != "ego" else None,
        "test_tab_closed": True if backend != "ego" else None,
        "existing_user_tabs_unchanged": True if backend != "ego" else None,
        "ego_task_name": f"ultrafast-benchmark-ego-{run_id}" if backend == "ego" else None,
    }
    summary.update(overrides)
    (directory / "summary.json").write_text(json.dumps(summary))
    steps = [
        {
            "step": 1,
            "operation_expected": "TYPE_TEXT",
            "operation_top1": "TYPE_TEXT",
            "operation_top1_correct": True,
            "operation_correct_probability": 0.8,
            "operation_margin": 0.5,
            "operation_brier": 0.1,
            "operation_top1_probability": 0.8,
            "target_expected": "Destination city",
            "target_top1_correct": True,
            "target_correct_probability": 0.7,
            "target_margin": 0.4,
            "target_top1_probability": 0.7,
            "target_scored": True,
            "premature_done": False,
            "unnecessary_wait": False,
            "incorrect_blocked": False,
            "goal_verified_at_decision": None,
        },
        {
            "step": 2,
            "operation_expected": "DONE",
            "operation_top1": "DONE",
            "operation_top1_correct": True,
            "operation_correct_probability": 0.9,
            "operation_margin": 0.7,
            "operation_brier": 0.02,
            "operation_top1_probability": 0.9,
            "target_top1_correct": None,
            "target_correct_probability": None,
            "target_margin": None,
            "target_top1_probability": None,
            "target_scored": False,
            "premature_done": False,
            "unnecessary_wait": False,
            "incorrect_blocked": False,
            "goal_verified_at_decision": True,
        },
    ]
    (directory / "steps.json").write_text(
        json.dumps(
            {
                "steps": steps,
                "pages": [{"url": "a"}, {"mentions_submitted": True}, {"mentions_submitted": True}],
            }
        )
    )
    (directory / "validation.json").write_text(json.dumps({"pass": summary["validator_pass"]}))
    return directory


def test_scoring_applies_the_fixed_formula(tmp_path):
    _write_run(tmp_path, "complex-browser_harness-1", "browser_harness", "complex", wall_elapsed_ms=4000)
    _write_run(tmp_path, "complex-ego-1", "ego", "complex", wall_elapsed_ms=8000)
    payload = scorer.build_score(tmp_path)
    chrome = payload["scores"]["browser_harness"]
    ego = payload["scores"]["ego"]
    assert chrome["reliability_35"] == pytest.approx(35.0)
    assert chrome["operation_15"] == pytest.approx(15.0)
    assert chrome["target_15"] == pytest.approx(15.0)
    # 5 x mean(0.8, 0.9) for the operation head + 5 x 0.7 for the one scored target step
    assert chrome["probability_10"] == pytest.approx(7.75)
    assert chrome["isolation_10"] == 10
    # Chrome is the faster median, so it takes the full 15 and Ego takes half.
    assert chrome["performance_15"] == pytest.approx(15.0)
    assert ego["performance_15"] == pytest.approx(7.5)
    assert chrome["total"] == pytest.approx(97.75)
    assert ego["total"] == pytest.approx(90.25)
    assert payload["reports"]["ego"]["goal_state"]["confirmed_done"] == 1
    assert payload["reports"]["browser_harness"]["goal_state"]["premature_done"] == 0
    per_run = scorer.goal_state(
        [{"operation_top1": "DONE", "step": 2}],
        [{"mentions_submitted": False}, {"mentions_submitted": True}],
    )
    # The success panel was first seen in pages[1]; the DONE decision is step 2,
    # which is the very next decision after it.
    assert per_run["steps_from_success_to_done"] == 1


def test_scoring_reports_goal_state_counters(tmp_path):
    _write_run(
        tmp_path,
        "complex-ego-1",
        "ego",
        "complex",
        validator_pass=False,
        status="blocked",
    )
    steps = json.loads((tmp_path / "complex-ego-1" / "steps.json").read_text())
    steps["steps"][0]["premature_done"] = True
    steps["steps"][1]["premature_done"] = False
    steps["steps"][1]["unnecessary_wait"] = True
    steps["steps"][1]["incorrect_blocked"] = True
    (tmp_path / "complex-ego-1" / "steps.json").write_text(json.dumps(steps))
    payload = scorer.build_score(tmp_path)
    assert payload["reports"]["ego"]["goal_state"] == {
        "premature_done": 1,
        "confirmed_done": 1,
        "unnecessary_wait": 1,
        "incorrect_blocked": 1,
        "success_panel_seen": 1,
    }
    assert payload["reports"]["ego"]["validator_pass_rate"] == 0.0
    assert payload["reports"]["ego"]["status_counts"] == {"blocked": 1}


def test_scoring_penalizes_isolation_failures(tmp_path):
    _write_run(tmp_path, "complex-ego-1", "ego", "complex", orphan_process_remaining=True)
    _write_run(tmp_path, "complex-ego-2", "ego", "complex", runtime_spawn_count=2)
    payload = scorer.build_score(tmp_path)
    assert payload["isolation"]["ego"]["failure_count"] == 2
    assert payload["scores"]["ego"]["isolation_10"] == 6


def test_scoring_handles_a_single_backend_without_performance_points(tmp_path):
    _write_run(tmp_path, "complex-ego-1", "ego", "complex")
    payload = scorer.build_score(tmp_path)
    assert payload["scores"]["ego"]["performance_15"] is None
    assert payload["scores"]["ego"]["total"] == pytest.approx(35 + 15 + 15 + 7.75 + 10)


def test_markdown_and_closing_paragraph_name_both_backends(tmp_path):
    _write_run(tmp_path, "complex-browser_harness-1", "browser_harness", "complex")
    _write_run(tmp_path, "complex-ego-1", "ego", "complex")
    payload = scorer.build_score(tmp_path)
    table = scorer.markdown_table(payload)
    assert "**TOTAL /100**" in table
    assert "Ego Browser" in table and "Chrome / Browser Harness" in table
    closing = scorer.closing_paragraph(payload)
    assert closing.startswith("最终成绩对比")
    assert "Ego Browser:" in closing
    assert "Chrome / Browser Harness:" in closing
    assert "本轮原始结果：" in closing
    assert "runs passed" in closing

# Shape of the real tfs payload Google Flights produces: route, then the trip
# type as protobuf field 19 (varint 1 = round trip, 2 = one way).
UNDATED_TFS = "CBwQARocagwIAxIIL20vMDg5NjZyDAgDEggvbS8wNGpwbEABSAFwAYIBCwj___________8BmAEC"


def _tfs_url(payload):
    raw = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"https://www.google.com/travel/flights?tfs={raw}&hl=en"


DATED_ONE_WAY = _tfs_url(
    b"\x08\x1c\x10\x01\x1a(\x12\n2026-09-24j\x0c\x08\x03\x12\x08/m/08966r\x0c\x08\x03"
    b"\x12\x08/m/04jpl@\x01H\x01p\x01\x98\x01\x02"
)
DATED_ROUND_TRIP = _tfs_url(
    b"\x08\x1c\x10\x01\x1a(\x12\n2026-09-24j\x0c\x08\x03\x12\x08/m/08966r\x0c\x08\x03"
    b"\x12\x08/m/04jpl@\x01H\x01p\x01\x98\x01\x01"
)


def test_flights_validator_requires_a_committed_one_way_date():
    hosted = "https://www.google.com/travel/flights?tfs={}&hl=en"
    assert benchmark_suite._committed_itinerary(DATED_ONE_WAY) == (True, True)
    # The same date before "One way" was chosen is round trip (field 19 == 1).
    assert benchmark_suite._committed_itinerary(DATED_ROUND_TRIP) == (True, False)
    assert benchmark_suite._committed_itinerary(hosted.format(UNDATED_TFS)) == (False, True)
    assert benchmark_suite._committed_itinerary("https://www.google.com/travel/flights?hl=en") == (False, False)
    assert benchmark_suite._committed_itinerary("") == (False, False)
    assert benchmark_suite._committed_itinerary(hosted.format("!!!!")) == (False, False)


def test_flights_expectation_claims_only_committed_states():
    task = benchmark_suite.task_by_key("flights")
    origin_only = {
        "url": "https://www.google.com/travel/flights?hl=en",
        "title": "Find Cheap Flights Worldwide & Book Your Ticket - Google Flights",
        "text": "Flights One way 1 Economy Z\u00fcrich London cheap flights from Z\u00fcrich from $158",
        "actions": [{"id": "e1", "kind": "click", "label": "Change ticket type. One way"}],
    }
    # A filled form with promotional prices is not a committed search.
    assert task["expect"](origin_only, benchmark_suite.Progress()) is None

    committed = {
        "url": DATED_ONE_WAY,
        "title": "Find Cheap Flights from Z\u00fcrich to London (ZRH - LON)",
        "text": "one way price from $178",
        "actions": [],
    }
    final = task["expect"](committed, benchmark_suite.Progress())
    assert final["operation"] == "DONE"
    assert final["goal_satisfied"] is True


def test_flights_expectation_asks_for_one_way_first():
    task = benchmark_suite.task_by_key("flights")
    fresh = {
        "url": "https://www.google.com/travel/flights?hl=en",
        "title": "Find Cheap Flights Worldwide & Book Your Ticket - Google Flights",
        "text": "Flights Round trip 1 Economy",
        "actions": [
            {"id": "e1", "kind": "click", "label": "Change ticket type. Round trip"},
            {"id": "e2", "kind": "fill", "label": "Where from?"},
        ],
    }
    step = task["expect"](fresh, benchmark_suite.Progress())
    assert step["operation"] == "CLICK"
    # Targets are compared casefolded, so the expectation carries a normalised label.
    assert step["target"] == "change ticket type. round trip"


def test_flights_goal_has_no_hardcoded_date():
    goal = benchmark_suite.task_by_key("flights")["goal"]
    assert "September" not in goal
    assert not any(year in goal for year in ("2025", "2026", "2027"))


def test_orphan_detection_ignores_processes_seen_before_the_run():
    """A shared machine keeps unrelated ego-browser processes around."""
    calls = []

    def fake_scan(pattern=None):
        calls.append(pattern)
        return [111, 222]

    original_scan = live_runs.scan_ego_processes
    original_table = live_runs.system_process_table
    original_alive = live_runs.pid_alive
    live_runs.scan_ego_processes = fake_scan
    live_runs.system_process_table = lambda pids: {pid: {"pid": pid, "alive": False} for pid in pids}
    live_runs.pid_alive = lambda pid, states=None: False
    try:
        # Both matches already existed before the run: not this run's orphan.
        evidence = live_runs.collect_process_evidence(
            "run", "ego", [333], "now", baseline_pids=[111, 222]
        )
        assert evidence["orphan_process_remaining"] is False
        assert evidence["post_close_scan"]["attributed"] == []
        assert "already present before this run" in " ".join(evidence["notes"])

        # A process that only appeared during the run is attributed to it.
        evidence = live_runs.collect_process_evidence(
            "run", "ego", [333], "now", baseline_pids=[111]
        )
        assert evidence["orphan_process_remaining"] is True
        assert evidence["post_close_scan"]["attributed"] == [222]
    finally:
        live_runs.scan_ego_processes = original_scan
        live_runs.system_process_table = original_table
        live_runs.pid_alive = original_alive


def test_orphan_detection_still_flags_a_surviving_runtime_pid():
    original_scan = live_runs.scan_ego_processes
    original_table = live_runs.system_process_table
    original_alive = live_runs.pid_alive
    live_runs.scan_ego_processes = lambda pattern=None: []
    live_runs.system_process_table = lambda pids: {pid: {"pid": pid, "alive": True} for pid in pids}
    live_runs.pid_alive = lambda pid, states=None: True
    try:
        evidence = live_runs.collect_process_evidence("run", "ego", [333], "now", baseline_pids=[])
        assert evidence["orphan_process_remaining"] is True
        assert evidence["surviving_pids"] == [333]
        assert live_runs.pid_alive_after_close(evidence["pid_alive_after_close"]) is True
    finally:
        live_runs.scan_ego_processes = original_scan
        live_runs.system_process_table = original_table
        live_runs.pid_alive = original_alive


def test_tab_evidence_waits_for_async_target_teardown(monkeypatch):
    """Chrome lists a closed target for a moment; the check must not call that a leak."""
    import time as _time

    targets = [["USER-1", "TEST-TAB"]]

    def fake_targets():
        return list(targets[0])

    def fake_sleep(seconds):
        targets[0] = ["USER-1"]  # the target finishes closing during the wait
        return None

    monkeypatch.setattr(runner, "chrome_targets", fake_targets)
    monkeypatch.setattr(_time, "sleep", fake_sleep)
    evidence = {"backend": "browser_harness", "pre_targets": ["USER-1"]}
    settled = runner.finish_tab_evidence(evidence, "browser_harness", "TEST-TAB")
    assert settled["test_tab_closed"] is True
    assert settled["existing_user_tabs_unchanged"] is True
    assert settled["post_targets_immediate"] == ["USER-1", "TEST-TAB"]
    assert settled["post_targets"] == ["USER-1"]


def test_tab_evidence_still_reports_a_real_leak(monkeypatch):
    import time as _time

    monkeypatch.setattr(runner, "chrome_targets", lambda: ["USER-1", "TEST-TAB"])
    monkeypatch.setattr(_time, "sleep", lambda seconds: None)
    evidence = {"backend": "browser_harness", "pre_targets": ["USER-1"]}
    settled = runner.finish_tab_evidence(evidence, "browser_harness", "TEST-TAB")
    assert settled["test_tab_closed"] is False


def test_a_step_without_target_ground_truth_is_not_scored_as_a_wrong_target(tmp_path):
    """A state the task does not pin down must not count as a wrong target.

    Runs recorded before the runner learned this carry ``False`` rather than
    ``None``, so the scorer applies the rule itself.
    """
    from scripts import score_backend_benchmark as scorer

    _write_run(tmp_path, "example-browser_harness-1", "browser_harness", "example")
    steps_path = tmp_path / "example-browser_harness-1" / "steps.json"
    steps_path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "step": 1,
                        "operation_expected": "CLICK",
                        "operation_top1": "CLICK",
                        "operation_top1_correct": True,
                        "operation_correct_probability": 0.8,
                        "operation_top1_probability": 0.8,
                        "target_expected": None,
                        "target_top1": "Somewhere else",
                        "target_top1_correct": False,
                    },
                    {
                        "step": 2,
                        "operation_expected": "CLICK",
                        "operation_top1": "CLICK",
                        "operation_top1_correct": True,
                        "operation_correct_probability": 0.8,
                        "operation_top1_probability": 0.8,
                        "target_expected": "Continue",
                        "target_top1": "Continue",
                        "target_top1_correct": True,
                        "target_correct_probability": 0.7,
                    },
                ]
            }
        )
    )
    payload = scorer.build_score(tmp_path)
    report = payload["reports"]["browser_harness"]
    assert report["target_steps_scored"] == 1
    assert report["target_top1_accuracy"] == pytest.approx(1.0)


def test_the_runner_records_the_task_space_it_actually_used():
    """A wiring check for a one-line bug that only exists in this script.

    The environment variable is restored before the summary is written, so
    reading it back at that point recorded ``None`` for every Ego run and made
    the "a fresh TaskSpace per run" isolation check unverifiable.
    """
    from scripts import run_backend_benchmark as runner

    source = Path(runner.__file__).read_text()
    assert 'ego_space_name = f"{EGO_SPACE_PREFIX}{run_id}"' in source
    assert '"ego_task_name": ego_space_name,' in source
    # The bug was reading the variable back after the run restored it.
    assert '"ego_task_name": os.environ.get("ULTRAFAST_EGO_TASK_NAME")' not in source


def test_isolation_scores_only_what_the_benchmark_did(tmp_path):
    """A third party's tabs and an unrecorded field are not our failures."""
    from scripts import score_backend_benchmark as scorer

    _write_run(tmp_path, "example-browser_harness-1", "browser_harness", "example")
    _write_run(
        tmp_path,
        "example-browser_harness-2",
        "browser_harness",
        "example",
        existing_user_tabs_unchanged=False,
    )
    _write_run(
        tmp_path,
        "example-ego-1",
        "ego",
        "example",
        ego_task_name=None,
        runtime_spawn_count=1,
        orphan_process_remaining=False,
        pid_alive_after_close=False,
    )
    _write_run(tmp_path, "example-ego-2", "ego", "example")
    payload = scorer.build_score(tmp_path)
    chrome = payload["isolation"]["browser_harness"]
    assert chrome["score"] == 10
    assert chrome["failures"] == []
    assert chrome["user_tabs_changed"] == 1

    ego = payload["isolation"]["ego"]
    # Run 1's space name was never recorded: unknown, so not a failure.
    assert ego["unknown"]["new_space_per_run"] == 1
    assert ego["checks"]["new_space_per_run"] == 1.0
    assert ego["failures"] == []
    assert ego["score"] == 10


def test_label_matching_tolerates_the_two_backends_naming_the_same_control():
    """The same element is named at different granularity per backend."""
    from scripts import run_backend_benchmark as runner

    assert runner.labels_match("Search Wikipedia", "Search Wikipedia")
    assert runner.labels_match("Search", "Search Wikipedia")
    assert runner.labels_match("Search Wikipedia", "Search")
    assert runner.labels_match("  Search   Wikipedia ", "search wikipedia")
    # Containment is deliberately loose in this direction: the shorter name is
    # a whole-token part of the longer one, which is how a backend that names
    # the input "Search" and one that names it "Search Wikipedia" agree.
    assert runner.labels_match("Search", "Search results")
    assert not runner.labels_match("Learn more", "More information...")
    assert not runner.labels_match("Continue", "Submit request")
    assert runner.labels_match(None, None)
    assert not runner.labels_match("Continue", None)


def test_the_example_ground_truth_names_the_link_the_live_page_has():
    """It used to say "More information...", which the page stopped using."""
    from scripts import benchmark_suite as suite

    expectation = suite.expected_example({"url": "https://example.com/", "text": ""}, None)
    assert expectation["target"] == "Learn more"


def test_the_flights_expectation_accepts_the_option_it_just_opened():
    """A regression guard: the open menu used to re-demand the used control."""
    from scripts import benchmark_suite as suite

    page = {
        "url": "https://www.google.com/travel/flights?hl=en",
        "text": "Flights",
        "actions": [
            {"kind": "click", "label": "Change ticket type. Round trip"},
            {"kind": "click", "label": "One way"},
            {"kind": "click", "label": "Multi-city"},
        ],
    }
    expectation = suite.expected_flights(page, None)
    assert expectation["operation"] == "CLICK"
    # page_labels normalizes, and the expectation carries the label it read.
    assert expectation["target"].casefold() == "one way"


def test_the_flights_expectation_still_claims_the_trip_control_first():
    from scripts import benchmark_suite as suite

    page = {
        "url": "https://www.google.com/travel/flights?hl=en",
        "text": "Flights",
        "actions": [
            {"kind": "click", "label": "Change ticket type. Round trip"},
            {"kind": "fill", "label": "Where from?"},
        ],
    }
    expectation = suite.expected_flights(page, None)
    assert expectation["target"].casefold() == "change ticket type. round trip"
