"""Run the final interleaved browser-backend benchmark and keep every sample.

    uv run --env-file .env python scripts/run_backend_benchmark.py --suite ab
    uv run --env-file .env python scripts/run_backend_benchmark.py --suite reliability
    uv run --env-file .env python scripts/run_backend_benchmark.py --suite probes

Each run gets <record-root>/<label>-<backend>-<n>/ holding the Agent's own
metrics.json and trace.json plus this harness's evidence:

    summary.json           one row of the final result table, with the score inputs
    steps.json             per-decision Jev operation/target probabilities
    validation.json        the independent validator's DOM read after the run
    process_evidence.json  runtime pids, orphan scan, pid liveness after close
    tab_evidence.json      Chrome targets before/after (only for browser_harness)
    version_manifest.json  git commit, implementation hash, interpreter, ego path

Backends alternate inside each repetition (Chrome, Ego, Chrome, Ego, ...) so a
slow website drift cannot favour one arm. Failures are recorded, never retried
and never overwritten. Ego runs each allocate their own TaskSpace named
``ultrafast-benchmark-ego-<run_id>``; Chrome runs each open and close their own
tab and never touch a tab the person already had open.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_suite import (  # noqa: E402
    Progress,
    fixture_server,
    fixture_url,
    summarize_validation,
    task_by_key,
    tasks_for_suite,
    validate,
    validator_passed,
)
from benchmark_suite import (
    fixture_url as _fixture_url,  # noqa: F401 - re-exported for tests
)
from live_runs import (  # noqa: E402
    DEFAULT_KILL_GRACE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    RunTimeout,
    collect_backend_evidence,
    collect_process_evidence,
    kill_runtime,
    pid_alive_after_close,
    scan_ego_processes,
    utc_now,
    version_manifest,
    watchdog,
    write_json,
)

DEFAULT_RECORD_ROOT = "recordings/final"
TARGET_OPERATIONS = {"CLICK", "TYPE_TEXT", "SELECT"}
EGO_SPACE_PREFIX = "ultrafast-benchmark-ego-"


def norm(value):
    return " ".join(str(value or "").split()).strip().casefold()


def labels_match(expected, predicted):
    """Whether two accessible names denote the same element.

    The two backends name the same control at different granularity — Chrome
    reports the input as ``Search``, Ego as ``Search Wikipedia`` — so a name that
    is wholly contained in the other, on token boundaries, counts as the same
    element. Two different names never match.
    """
    left, right = norm(expected), norm(predicted)
    if not left or not right:
        return left == right
    return left == right or f" {left} " in f" {right} " or f" {right} " in f" {left} "


def print_line(payload):
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


# ------------------------------------------------------------- measured steps

def _target_head(page, operation):
    from jev_ultrafast.model import action_space

    _elements, targets, _controls = action_space((page or {}).get("actions", []))
    return targets.get(operation, {})


def build_step_record(index, decision, entry, page_before, progress, task):
    """One executed decision, with the probabilities Jev actually returned."""
    operation = str(decision.get("operation") or "")
    operation_probabilities = {
        str(name): float(value)
        for name, value in (decision.get("operation_probabilities") or {}).items()
    }
    ranked = sorted(operation_probabilities.items(), key=lambda item: (-item[1], item[0]))
    second_name, second_probability = ranked[1] if len(ranked) > 1 else (None, None)

    head = _target_head(page_before, operation)
    target_probabilities = {}
    predicted_target = None
    if decision.get("target") is not None and head:
        predicted_target = str(head.get(str(decision["target"]), {}).get("label") or "")
        for key, value in (decision.get("target_probabilities") or {}).items():
            action = head.get(str(key))
            if action is not None:
                target_probabilities[str(action.get("label") or "")] = float(value)
    target_ranked = sorted(target_probabilities.items(), key=lambda item: (-item[1], item[0]))
    target_second, target_second_probability = target_ranked[1] if len(target_ranked) > 1 else (None, None)

    expectation = task["expect"](page_before, progress) if task.get("expect") else None
    expected_operation = expectation["operation"] if expectation else None
    expected_target = expectation.get("target") if expectation else None
    alternatives = list(expectation.get("alternatives") or []) if expectation else []

    strict_operation = None if expected_operation is None else operation == expected_operation
    # Target metrics are measured on the steps where the policy actually
    # predicted a target, because that is when a target head exists to score.
    # A step that chose WAIT/DONE has no target prediction at all; its operation
    # error is already counted by the operation metrics. A step whose state the
    # task does not pin down has no target ground truth either, so it stays
    # unscored instead of being counted as a wrong target.
    strict_target = None
    correct_target_probability = None
    if operation in TARGET_OPERATIONS and expected_target is not None:
        strict_target = bool(strict_operation) and labels_match(expected_target, predicted_target)
        correct_target_probability = 0.0
        if strict_operation:
            for label, probability in target_probabilities.items():
                if labels_match(expected_target, label):
                    correct_target_probability = probability
                    break

    def acceptable(operation_name, target_label):
        candidates = [(expected_operation, expected_target)] + [
            (item.get("operation"), item.get("target")) for item in alternatives
        ]
        for candidate_operation, candidate_target in candidates:
            if candidate_operation != operation_name:
                continue
            if candidate_target is None or labels_match(candidate_target, target_label):
                return True
        return False

    record = {
        "step": index,
        "operation_expected": expected_operation,
        "operation_expected_alternatives": alternatives,
        "operation_top1": operation,
        "operation_top1_probability": operation_probabilities.get(operation),
        "operation_correct_probability": (
            operation_probabilities.get(expected_operation) if expected_operation else None
        ),
        "operation_second": second_name,
        "operation_second_probability": second_probability,
        "operation_margin": (
            round(ranked[0][1] - second_probability, 6) if len(ranked) > 1 else None
        ),
        "operation_top1_correct": strict_operation,
        "operation_acceptable": acceptable(operation, predicted_target),
        "operation_probabilities": operation_probabilities,
        "operation_brier": (
            round(
                sum(
                    (probability - (1.0 if name == expected_operation else 0.0)) ** 2
                    for name, probability in operation_probabilities.items()
                )
                / len(operation_probabilities),
                6,
            )
            if operation_probabilities and expected_operation
            else None
        ),
        "target_expected": expected_target,
        "target_top1": predicted_target,
        "target_top1_probability": target_probabilities.get(predicted_target or ""),
        "target_correct_probability": correct_target_probability,
        "target_second": target_second,
        "target_second_probability": target_second_probability,
        "target_margin": (
            round(target_ranked[0][1] - target_second_probability, 6) if len(target_ranked) > 1 else None
        ),
        "target_top1_correct": strict_target,
        "target_probabilities": target_probabilities,
        "target_scored": operation in TARGET_OPERATIONS,
        "choice": decision.get("choice"),
        "chosen_kind": entry.get("kind"),
        "chosen_action": entry.get("action"),
        "typed_text": entry.get("text"),
        "page_url_before": (page_before or {}).get("url"),
        "page_title_before": (page_before or {}).get("title"),
        "page_changed": entry.get("page_changed"),
        "url_after": entry.get("url"),
        "confidence": decision.get("confidence"),
        "jev_latency_ms": decision.get("latency_ms"),
        "executed_ms": entry.get("executed_ms"),
        "premature_done": (
            operation == "DONE" and expectation is not None and not expectation.get("goal_satisfied")
        ),
        "unnecessary_wait": (
            operation == "WAIT"
            and expected_operation is not None
            and not acceptable("WAIT", None)
        ),
        "incorrect_blocked": operation == "BLOCKED" and expected_operation not in (None, "BLOCKED"),
    }
    return record


def shape_page(page):
    text = str((page or {}).get("text") or "")
    return {
        "url": (page or {}).get("url"),
        "title": (page or {}).get("title"),
        "scroll": (page or {}).get("scroll"),
        "actions": len((page or {}).get("actions") or []),
        "text_head": text[:400],
        "mentions_submitted": "request submitted" in text.casefold(),
    }


# ------------------------------------------------------------- tab isolation

def chrome_targets():
    try:
        from browser_harness.helpers import cdp

        return sorted(item["targetId"] for item in cdp("Target.getTargets").get("targetInfos", []))
    except BaseException:  # noqa: BLE001 - evidence must never break a run
        return None


def begin_tab_evidence(backend):
    if backend != "browser_harness":
        return {"backend": backend, "sanctioned": "new_task_space_per_run"}
    return {"backend": backend, "pre_targets": chrome_targets()}


def finish_tab_evidence(evidence, backend, created_target):
    if backend != "browser_harness":
        return evidence
    post = chrome_targets()
    # Chrome tears a closed target down asynchronously, so the run's own tab can
    # still be listed for a moment right after close. Re-read briefly before
    # calling it a leak, and keep both readings in the evidence.
    settled = post
    if created_target and post is not None and created_target in post:
        for _ in range(8):
            time.sleep(0.25)
            settled = chrome_targets()
            if settled is None or created_target not in settled:
                break
    pre = evidence.get("pre_targets")
    evidence.update(
        post_targets=settled,
        post_targets_immediate=post,
        created_test_tab=bool(created_target),
        test_tab_closed=bool(created_target) and settled is not None and created_target not in settled,
        existing_user_tabs_unchanged=(
            None if pre is None or settled is None else all(target in settled for target in pre)
        ),
    )
    return evidence


def run_benchmark_once(task, backend, run_index, port, *, record_root, source_root, timeout_seconds,
                       kill_grace=DEFAULT_KILL_GRACE_SECONDS, suite="ab"):
    """One measured run: construct, drive, validate, close, and keep all evidence."""
    run_id = f"{task['label']}-{backend}-{run_index}"
    record_dir = Path(record_root) / run_id
    record_dir.mkdir(parents=True, exist_ok=True)
    url = fixture_url(port, task)
    manifest = version_manifest(source_root)
    write_json(record_dir / "version_manifest.json", manifest)
    tab_evidence = begin_tab_evidence(backend)
    # What the Ego scan already sees before this run starts, so an unrelated
    # runtime on the same machine can never be reported as this run's orphan.
    process_baseline = scan_ego_processes() if backend == "ego" else []
    previous_space = os.environ.get("ULTRAFAST_EGO_TASK_NAME")
    ego_space_name = None
    if backend == "ego":
        # A fresh, uniquely named TaskSpace per run: the runtime never touches a
        # space a person may be using.
        ego_space_name = f"{EGO_SPACE_PREFIX}{run_id}"
        os.environ["ULTRAFAST_EGO_TASK_NAME"] = ego_space_name

    from jev_ultrafast import Agent

    started = time.perf_counter()
    steps = []
    pages = []
    stale_ticks = []
    progress = Progress()
    failure = None
    close_failure = None
    agent = None
    created_target = None
    evidence = {"final_status": "not_started", "phase": "construct"}
    try:
        try:
            with watchdog(timeout_seconds):
                agent = Agent(
                    url,
                    task["goal"],
                    backend=backend,
                    record_dir=record_dir,
                    screenshots=False,
                )
            evidence["agent"] = agent
            evidence.update(collect_backend_evidence(agent))
            if backend == "browser_harness":
                created_target = getattr(agent.browser, "target", None)
            page_before = agent.state["page"]
            pages.append(shape_page(page_before))
            evidence["phase"] = "run"
            generator = agent.run()
            with watchdog(timeout_seconds):
                while True:
                    history_before = len(agent.state["history"])
                    decisions_before = len(agent.state["decisions"])
                    stale_before = agent.metrics.stale_count
                    try:
                        next(generator)
                    except StopIteration:
                        break
                    status = agent.state["status"]
                    entry = None
                    if len(agent.state["history"]) > history_before:
                        entry = agent.state["history"][-1]
                    elif len(agent.state["decisions"]) > decisions_before and status in {"done", "blocked"}:
                        # A terminal DONE/BLOCKED decision stops the run without
                        # touching the page, so it has no history entry of its
                        # own. It is still a decision Jev made and paid for.
                        terminal = agent.state["decisions"][-1]
                        entry = {
                            "kind": str(terminal.get("choice") or "").lower(),
                            "action": terminal.get("choice"),
                            "choice": terminal.get("choice"),
                            "text": None,
                            "page_changed": None,
                            "url": (agent.state["page"] or {}).get("url"),
                            "executed_ms": terminal.get("elapsed_ms"),
                            "terminal": True,
                        }
                    if entry is None:
                        # A stale-retried or fused tick: no mutation was executed,
                        # so it is not scored, but it is kept as raw evidence.
                        stale_ticks.append(
                            {
                                "after_step": len(steps),
                                "status": status,
                                "stale_delta": agent.metrics.stale_count - stale_before,
                                "decision": (
                                    agent.state["decisions"][-1].get("operation")
                                    if len(agent.state["decisions"]) > decisions_before
                                    else None
                                ),
                                "url": (agent.state["page"] or {}).get("url"),
                            }
                        )
                        page_before = agent.state["page"]
                        continue
                    decision = agent.state["decisions"][-1]
                    record = build_step_record(len(steps) + 1, decision, entry, page_before, progress, task)
                    if record["operation_top1"] in {"DONE", "BLOCKED"}:
                        # Independent goal check at the moment the policy claims
                        # the goal state, so a premature DONE is measured, not guessed.
                        try:
                            live = validate(agent.browser, task["validator"])
                            record["goal_verified_at_decision"] = validator_passed(live)
                            record["goal_state_at_decision"] = summarize_validation(live)["checks"]
                        except BaseException as error:  # noqa: BLE001 - evidence only
                            record["goal_verified_at_decision"] = None
                            record["goal_verify_error"] = f"{type(error).__name__}: {error}"
                        record["premature_done"] = (
                            record["operation_top1"] == "DONE"
                            and record.get("goal_verified_at_decision") is False
                        )
                    steps.append(record)
                    progress.observe_action(entry)
                    page_after = agent.state["page"]
                    pages.append(shape_page(page_after))
                    page_before = page_after
            evidence.update(collect_backend_evidence(agent))
        finally:
            if agent is not None:
                evidence["agent"] = agent

        # Independent outcome verification while the page is still open.
        validation = None
        validation_error = None
        try:
            with watchdog(timeout_seconds):
                validation = validate(agent.browser, task["validator"])
        except BaseException as error:  # noqa: BLE001 - a failed read is a failed run
            validation_error = f"{type(error).__name__}: {error}"
        validation_summary = summarize_validation(validation) if validation is not None else {"pass": False}
        write_json(
            record_dir / "validation.json",
            {
                "run_id": run_id,
                "backend": backend,
                "validator": task["key"],
                "result": validation,
                "error": validation_error,
                **validation_summary,
            },
        )
    except BaseException as error:  # noqa: BLE001 - every failure becomes one recorded run
        failure = {
            "run_id": run_id,
            "phase": evidence.get("phase") or "run",
            "timeout_seconds": timeout_seconds,
            "exception": type(error).__name__,
            "message": str(error),
            "is_watchdog": isinstance(error, RunTimeout),
        }
        agent = evidence.get("agent") or agent
        write_json(record_dir / "runner_failure.json", failure)

    pids = []
    try:
        pids = list(evidence.get("ego_child_pids") or [])
    except BaseException:  # noqa: BLE001
        pids = []
    if agent is not None:
        close_started = time.perf_counter()
        try:
            with watchdog(timeout_seconds):
                agent.close()
        except RunTimeout:
            close_failure = f"close exceeded {timeout_seconds:g}s"
            kill_runtime(pids, kill_grace)
        except BaseException as error:  # noqa: BLE001 - recorded, not raised
            close_failure = f"{type(error).__name__}: {error}"
        close_ms = round((time.perf_counter() - close_started) * 1000)
    else:
        close_ms = None
    if previous_space is None:
        os.environ.pop("ULTRAFAST_EGO_TASK_NAME", None)
    else:
        os.environ["ULTRAFAST_EGO_TASK_NAME"] = previous_space

    metrics = None
    try:
        metrics = json.loads((record_dir / "metrics.json").read_text())
    except (OSError, ValueError):
        metrics = None
    if isinstance(metrics, dict) and metrics.get("status"):
        status = metrics["status"]
    elif failure and failure["phase"] == "construct":
        status = "not_started"
    else:
        status = evidence.get("final_status") or "missing_metrics"

    process_evidence = collect_process_evidence(
        run_id, backend, pids, utc_now(), metrics, baseline_pids=process_baseline
    )
    write_json(record_dir / "process_evidence.json", process_evidence)
    tab_evidence = finish_tab_evidence(tab_evidence, backend, created_target)
    write_json(record_dir / "tab_evidence.json", tab_evidence)
    write_json(
        record_dir / "steps.json",
        {
            "run_id": run_id,
            "backend": backend,
            "task": task["key"],
            "goal": task["goal"],
            "url": url,
            "stale_ticks": stale_ticks,
            "pages": pages,
            "steps": steps,
        },
    )

    validation_ok = None
    try:
        validation_ok = json.loads((record_dir / "validation.json").read_text()).get("pass")
    except (OSError, ValueError):
        validation_ok = None

    summary = {
        "run_id": run_id,
        "task": task["key"],
        "task_name": task["name"],
        "label": task["label"],
        "suite": suite,
        "backend": backend,
        "run_index": run_index,
        "url": url,
        "goal": task["goal"],
        "status": status,
        "validator_pass": validation_ok,
        "wall_elapsed_ms": (metrics or {}).get("wall_elapsed_ms"),
        "policy_elapsed_ms": (metrics or {}).get("policy_elapsed_ms"),
        "jev_calls": (metrics or {}).get("jev_calls"),
        "browser_actions_attempted": (metrics or {}).get("browser_actions_attempted"),
        "browser_actions_succeeded": (metrics or {}).get("browser_actions_succeeded"),
        "action_success_rate": (metrics or {}).get("action_success_rate"),
        "stale_count": (metrics or {}).get("stale_count"),
        "ego_operation_ms": (metrics or {}).get("ego_operation_ms"),
        "ego_observe_ms": (metrics or {}).get("ego_observe_ms"),
        "ego_act_ms": (metrics or {}).get("ego_act_ms"),
        "ego_fresh_ms": (metrics or {}).get("ego_fresh_ms"),
        "backend_operation_ms": (metrics or {}).get("backend_operation_ms"),
        "backend_startup_ms": (metrics or {}).get("backend_startup_ms"),
        "backend_cleanup_ms": (metrics or {}).get("backend_cleanup_ms"),
        "text_helper_calls": (metrics or {}).get("text_helper_calls"),
        "error_type": (metrics or {}).get("error_type"),
        "runtime_spawn_count": process_evidence.get("metrics", {}).get("runtime_spawn_count")
        if isinstance(process_evidence.get("metrics"), dict)
        else None,
        "orphan_process_remaining": process_evidence["orphan_process_remaining"],
        "pid_alive_after_close": pid_alive_after_close(process_evidence["pid_alive_after_close"]),
        "pid_liveness": process_evidence["pid_alive_after_close"],
        "created_test_tab": tab_evidence.get("created_test_tab"),
        "test_tab_closed": tab_evidence.get("test_tab_closed"),
        "existing_user_tabs_unchanged": tab_evidence.get("existing_user_tabs_unchanged"),
        "ego_task_name": ego_space_name,
        "steps_recorded": len(steps),
        "stale_ticks": len(stale_ticks),
        "step_count": len(steps),
        "close_elapsed_ms": close_ms,
        "close_failure": close_failure,
        "runner_failure": failure,
        "runner_elapsed_ms": round((time.perf_counter() - started) * 1000),
        "wall_clock_utc": utc_now(),
        "git_commit": manifest["git_commit"],
        "git_dirty": manifest["git_dirty"],
        "implementation_hash": manifest["implementation_hash"],
        "record_dir": str(record_dir),
    }
    write_json(record_dir / "summary.json", summary)
    return summary


# ------------------------------------------------------------------- planning

def build_plan(tasks, backends, record_root, runs_override=None):
    plan = []
    for task in tasks:
        count = int(runs_override or task.get("runs") or 1)
        for index in range(1, count + 1):
            for backend in backends:
                run_id = f"{task['label']}-{backend}-{index}"
                plan.append(
                    {
                        "run_id": run_id,
                        "task": task["key"],
                        "backend": backend,
                        "run_index": index,
                        "record_dir": str(Path(record_root) / run_id),
                    }
                )
    return plan


def backend_order(arguments):
    values = []
    for value in arguments.backend or []:
        values.append(value.strip().lower())
    for value in (arguments.backends or "").split(","):
        if value.strip():
            values.append(value.strip().lower())
    if not values:
        values = ["browser_harness", "ego"]
    unknown = [value for value in values if value not in {"browser_harness", "ego"}]
    if unknown:
        raise SystemExit(f"Unknown backend(s): {', '.join(unknown)}")
    return values


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", default="ab", choices=("ab", "reliability", "probes", "all"))
    parser.add_argument("--only", action="append", help="Restrict to one task key (repeatable).")
    parser.add_argument("--runs", type=int, help="Override each task's repetition count.")
    parser.add_argument("--record-root", default=None,
                        help="Defaults to recordings/final/<suite> so each suite scores on its own.")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--kill-grace-seconds", type=float, default=DEFAULT_KILL_GRACE_SECONDS)
    parser.add_argument("--backend", action="append", choices=("browser_harness", "ego"), dest="backend")
    parser.add_argument("--backends", help="Comma-separated backend order; defaults to browser_harness,ego.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and exit without a browser.")
    parser.add_argument("--force", action="store_true", help="Allow writing into existing run directories.")
    parser.add_argument("--source-root")
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    suites = ("ab", "reliability", "probes") if arguments.suite == "all" else (arguments.suite,)
    tasks = []
    for suite in suites:
        for task in tasks_for_suite(suite):
            if task not in tasks:
                tasks.append(task)
    if arguments.only:
        wanted = set(arguments.only)
        tasks = [task for task in tasks if task["key"] in wanted]
        missing = wanted - {task["key"] for task in tasks}
        if missing:
            raise SystemExit(f"Unknown task key(s) for this suite: {', '.join(sorted(missing))}")
    backends = backend_order(arguments)
    if arguments.suite == "reliability" and backends == ["browser_harness", "ego"]:
        backends = ["ego"]  # Part A's regression is an Ego-only consecutive series.
    record_root = arguments.record_root or f"{DEFAULT_RECORD_ROOT}/{arguments.suite}"
    plan = build_plan(tasks, backends, record_root, arguments.runs)
    if arguments.dry_run:
        print(
            json.dumps(
                {"dry_run": True, "suite": arguments.suite, "runs": len(plan), "plan": plan},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    existing = [run["record_dir"] for run in plan if Path(run["record_dir"]).exists()]
    if existing and not arguments.force:
        raise SystemExit(
            f"run directories already exist: {', '.join(existing)}; reuse --force to overwrite"
        )
    if arguments.source_root:
        source_root = Path(arguments.source_root).resolve()
    else:
        source_root = Path(__file__).resolve().parents[1]
    results = []
    with fixture_server() as port:
        print_line({"local_fixture_server": f"http://127.0.0.1:{port}/", "runs": len(plan)})
        for run in plan:
            task = task_by_key(run["task"])
            summary = run_benchmark_once(
                task,
                run["backend"],
                run["run_index"],
                port,
                record_root=record_root,
                suite=arguments.suite,
                source_root=str(source_root),
                timeout_seconds=arguments.timeout_seconds,
                kill_grace=arguments.kill_grace_seconds,
            )
            results.append(summary)
            print_line(
                {
                    key: summary.get(key)
                    for key in (
                        "run_id",
                        "task",
                        "backend",
                        "status",
                        "validator_pass",
                        "wall_elapsed_ms",
                        "jev_calls",
                        "browser_actions_attempted",
                        "browser_actions_succeeded",
                        "stale_count",
                        "steps_recorded",
                        "orphan_process_remaining",
                        "pid_alive_after_close",
                        "created_test_tab",
                        "test_tab_closed",
                        "existing_user_tabs_unchanged",
                        "error_type",
                        "runner_failure",
                    )
                }
            )
    failures = [row for row in results if row["status"] not in {"done", "blocked"}]
    print_line(
        {
            "suite": arguments.suite,
            "runs": len(results),
            "validator_passed": sum(1 for row in results if row["validator_pass"]),
            "non_terminal_status": len(failures),
            "record_root": record_root,
        }
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())