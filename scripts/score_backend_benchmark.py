"""Score the final backend benchmark with the fixed 100-point formula.

    uv run python scripts/score_backend_benchmark.py --record-root recordings/final

Reads every run's summary.json and steps.json under the record root, keeps the
raw numbers, and produces the six subscores, both totals, the result table, and
the closing comparison paragraph.

The formula is fixed by the task specification:

    Reliability             35 x validator_pass_rate
    Jev operation judgment  15 x operation_top1_accuracy
    Jev target selection    15 x target_top1_accuracy
    Probability quality     5 x mean_correct_operation_probability
                          + 5 x mean_correct_target_probability
    Performance             15  (per task: fastest median = 15,
                                 other = 15 x fastest_median / its_median)
    Isolation / cleanup     10  (10, minus 2 per clear isolation/cleanup failure)

Probabilities are not success rates, so accuracy and mean correct-choice
probability are reported separately, together with margins, Brier scores, and
the goal-state counters.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

BACKEND_LABELS = {"browser_harness": "Chrome / Browser Harness", "ego": "Ego Browser"}
TERMINAL_STATUSES = {"done", "blocked"}
TARGET_OPERATIONS = {"CLICK", "TYPE_TEXT", "SELECT"}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def mean(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return round(statistics.fmean(values), 6) if values else None


def ratio(values):
    values = [value for value in values if isinstance(value, bool)]
    return round(sum(1 for value in values if value) / len(values), 6) if values else None


def collect(record_root):
    runs = []
    for summary_path in sorted(Path(record_root).glob("*/summary.json")):
        summary = read_json(summary_path)
        if not isinstance(summary, dict):
            continue
        steps = read_json(summary_path.parent / "steps.json") or {}
        validation = read_json(summary_path.parent / "validation.json") or {}
        summary["_steps"] = steps.get("steps") or []
        summary["_pages"] = steps.get("pages") or []
        summary["_validation"] = validation
        summary["_dir"] = str(summary_path.parent)
        runs.append(summary)
    return runs


def goal_state(steps, pages):
    """Premature DONE, unnecessary WAIT, incorrect BLOCKED, and DONE latency."""
    premature_done = [step for step in steps if step.get("premature_done")]
    confirmed_done = [step for step in steps if step.get("operation_top1") == "DONE"
                      and step.get("goal_verified_at_decision") is True]
    unnecessary_wait = [step for step in steps if step.get("unnecessary_wait")]
    incorrect_blocked = [step for step in steps if step.get("incorrect_blocked")]
    # The first page a run observed with the success panel visible, and the step
    # index of the DONE decision that followed it.
    success_index = next(
        (index for index, page in enumerate(pages) if page.get("mentions_submitted")), None
    )
    done_step = next(
        (step["step"] for step in steps if step.get("operation_top1") == "DONE"), None
    )
    return {
        "premature_done": len(premature_done),
        "confirmed_done": len(confirmed_done),
        "unnecessary_wait": len(unnecessary_wait),
        "incorrect_blocked": len(incorrect_blocked),
        "success_panel_seen": success_index is not None,
        "steps_from_success_to_done": (
            None if success_index is None or done_step is None else done_step - success_index
        ),
    }


def backend_report(runs):
    """Every raw aggregate one backend contributes to the score."""
    steps = [step for run in runs for step in run["_steps"]]
    operation_scored = [step for step in steps if step.get("operation_top1_correct") is not None]
    # A step whose state the task does not pin down has no target ground truth.
    # Runs recorded before the runner learned that carry a False for those
    # steps, so the condition is applied here as well and every run is scored
    # by the same rule.
    target_scored = [
        step
        for step in steps
        if step.get("target_top1_correct") is not None and step.get("target_expected") is not None
    ]
    wait_blocked = [step for step in steps if step.get("operation_top1") in {"DONE", "BLOCKED"}]
    terminals = [run for run in runs if run.get("status") in TERMINAL_STATUSES]
    goal_states = [(run, goal_state(run["_steps"], run["_pages"])) for run in runs]
    return {
        "runs": len(runs),
        "terminal_runs": len(terminals),
        "wall_samples": [
            run["wall_elapsed_ms"]
            for run in terminals
            if isinstance(run.get("wall_elapsed_ms"), (int, float))
        ],
        "validator_pass_rate": ratio([run.get("validator_pass") for run in runs]),
        "validator_passed": sum(1 for run in runs if run.get("validator_pass")),
        "status_counts": {
            status: sum(1 for run in runs if run.get("status") == status)
            for status in sorted({str(run.get("status")) for run in runs})
        },
        "operation_steps_scored": len(operation_scored),
        "operation_top1_accuracy": ratio([step.get("operation_top1_correct") for step in operation_scored]),
        "operation_acceptable_rate": ratio([step.get("operation_acceptable") for step in operation_scored]),
        "target_steps_scored": len(target_scored),
        "target_top1_accuracy": ratio([step.get("target_top1_correct") for step in target_scored]),
        "mean_correct_operation_probability": mean(
            [step.get("operation_correct_probability") for step in operation_scored]
        ),
        "mean_correct_target_probability": mean([step.get("target_correct_probability") for step in target_scored]),
        "mean_operation_margin": mean([step.get("operation_margin") for step in operation_scored]),
        "mean_target_margin": mean([step.get("target_margin") for step in target_scored]),
        "mean_operation_brier": mean([step.get("operation_brier") for step in operation_scored]),
        "mean_operation_top1_probability": mean([step.get("operation_top1_probability") for step in operation_scored]),
        "mean_target_top1_probability": mean([step.get("target_top1_probability") for step in target_scored]),
        "operation_mix": {
            operation: sum(1 for step in steps if step.get("operation_top1") == operation)
            for operation in sorted({str(step.get("operation_top1")) for step in steps})
        },
        "expected_mix": {
            operation: sum(1 for step in steps if step.get("operation_expected") == operation)
            for operation in sorted({str(step.get("operation_expected")) for step in steps})
        },
        "goal_state": {
            key: (
                sum(1 for _run, state in goal_states if state[key])
                if key == "success_panel_seen"
                else sum(state[key] for _run, state in goal_states)
            )
            for key in (
                "premature_done",
                "confirmed_done",
                "unnecessary_wait",
                "incorrect_blocked",
                "success_panel_seen",
            )
        },
        "done_steps": len(wait_blocked),
        "jev_calls": sum(run.get("jev_calls") or 0 for run in runs),
        "text_helper_calls": sum(run.get("text_helper_calls") or 0 for run in runs),
        "actions_attempted": sum(run.get("browser_actions_attempted") or 0 for run in runs),
        "actions_succeeded": sum(run.get("browser_actions_succeeded") or 0 for run in runs),
        "action_success_rate": (
            round(
                sum(run.get("browser_actions_succeeded") or 0 for run in runs)
                / sum(run.get("browser_actions_attempted") or 0 for run in runs),
                6,
            )
            if sum(run.get("browser_actions_attempted") or 0 for run in runs)
            else None
        ),
        "stale_count": sum(run.get("stale_count") or 0 for run in runs),
        "steps_recorded": len(steps),
        "backend_operation_ms_total": sum(run.get("backend_operation_ms") or 0 for run in runs),
        "backend_startup_ms_median": median_of([run.get("backend_startup_ms") for run in runs]),
        "backend_cleanup_ms_median": median_of([run.get("backend_cleanup_ms") for run in runs]),
        "failure_types": failure_types(runs),
    }


def failure_types(runs):
    counts = defaultdict(int)
    for run in runs:
        if run.get("validator_pass"):
            continue
        failure = run.get("runner_failure") or {}
        key = failure.get("exception") or run.get("error_type") or str(run.get("status"))
        counts[str(key)] += 1
    return dict(sorted(counts.items()))


def median_of(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return round(statistics.median(values), 3) if values else None


def task_medians(runs):
    """Median wall time per (task, backend) over runs that reached a terminal status."""
    grouped = defaultdict(list)
    counts = defaultdict(int)
    for run in runs:
        counts[(run.get("task"), run.get("backend"))] += 1
        if run.get("status") in TERMINAL_STATUSES:
            value = run.get("wall_elapsed_ms")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                grouped[(run.get("task"), run.get("backend"))].append(value)
    medians = {key: round(statistics.median(values), 3) for key, values in grouped.items() if values}
    return medians, counts


def performance_scores(medians):
    """Per task: the faster median gets 15, the other 15 x fastest / its own."""
    by_task = defaultdict(dict)
    for (task, backend), value in medians.items():
        by_task[task][backend] = value
    per_task = {}
    for task, values in sorted(by_task.items()):
        if len(values) < 2:
            per_task[task] = {"medians": values, "scores": {name: None for name in values},
                              "note": "only one backend produced a terminal run"}
            continue
        fastest = min(values.values())
        per_task[task] = {
            "medians": values,
            "scores": {
                backend: round(15 * fastest / value, 4) if value else None
                for backend, value in values.items()
            },
            "fastest": [name for name, value in values.items() if value == fastest],
        }
    totals = defaultdict(list)
    for entry in per_task.values():
        for backend, score in entry["scores"].items():
            if score is not None:
                totals[backend].append(score)
    return per_task, {backend: round(statistics.fmean(scores), 4) for backend, scores in totals.items()}


EGO_SPACE_PREFIX = "ultrafast-benchmark-ego-"


def expected_space_name(run_id):
    """The TaskSpace name the runner assigns to a run, from its own run id."""
    return f"{EGO_SPACE_PREFIX}{run_id}"


def isolation_report(runs):
    """The isolation/cleanup check per backend, with each failure enumerated.

    A failure is something this benchmark did: a tab it opened and did not
    close, a runtime that outlived ``close()``, a second runtime for one run, or
    a run without its own TaskSpace.  A changed *user* tab set is counted
    separately and not scored: Chrome tab ids also change when the browser
    discards a tab, and a person using the same browser profile during a run is
    not this benchmark's leak.  An unrecorded field stays ``None`` (unknown) and
    is never turned into a failure.
    """
    report = defaultdict(lambda: {"checks": defaultdict(list), "failures": [], "runs": 0, "user_tabs_changed": 0})
    for run in runs:
        backend = run.get("backend")
        item = report[backend]
        item["runs"] += 1
        if backend == "ego":
            space = run.get("ego_task_name")
            spawn = run.get("runtime_spawn_count")
            orphan = run.get("orphan_process_remaining")
            alive = run.get("pid_alive_after_close")
            checks = {
                "new_space_per_run": None if space is None else space == expected_space_name(run.get("run_id")),
                "runtime_spawn_count_is_1": None if spawn is None else spawn == 1,
                "orphan_process_remaining_false": None if orphan is None else orphan is False,
                "pid_alive_after_close_false": None if alive is None else alive is False,
            }
        else:
            created = run.get("created_test_tab")
            closed = run.get("test_tab_closed")
            user_tabs = run.get("existing_user_tabs_unchanged")
            if user_tabs is False:
                item["user_tabs_changed"] += 1
            checks = {
                "created_test_tab": None if created is None else created is True,
                "test_tab_closed": None if closed is None else closed is True,
            }
        for name, value in checks.items():
            item["checks"][name].append(value)
            if value is not True:
                item["failures"].append({"run_id": run.get("run_id"), "check": name, "value": value})
    summary = {}
    for backend, item in report.items():
        known = {name: values for name, values in item["checks"].items() if any(v is not None for v in values)}
        # Only a recorded False is a failure. A run whose evidence does not
        # contain the field at all is unknown, and unknown is not a failure.
        failures = [failure for failure in item["failures"] if failure["value"] is False]
        score = max(0, 10 - 2 * len(failures))
        summary[backend] = {
            "runs": item["runs"],
            "checks": {name: ratio(values) for name, values in known.items()},
            "unknown": {name: sum(1 for value in values if value is None) for name, values in known.items()},
            "user_tabs_changed": item["user_tabs_changed"],
            "failures": failures,
            "failure_count": len(failures),
            "score": score,
        }
    return summary


def build_score(record_root):
    runs = collect(record_root)
    if not runs:
        raise SystemExit(f"No run summaries found under {record_root}")
    backends = sorted({str(run.get("backend")) for run in runs})
    reports = {backend: backend_report([run for run in runs if run.get("backend") == backend]) for backend in backends}
    medians, counts = task_medians(runs)
    per_task, performance = performance_scores(medians)
    isolation = isolation_report(runs)
    scores = {}
    for backend in backends:
        report = reports[backend]
        reliability = round(35 * (report["validator_pass_rate"] or 0), 4)
        operation = round(15 * (report["operation_top1_accuracy"] or 0), 4)
        target = round(15 * (report["target_top1_accuracy"] or 0), 4)
        probability = round(
            5 * (report["mean_correct_operation_probability"] or 0)
            + 5 * (report["mean_correct_target_probability"] or 0),
            4,
        )
        speed = performance.get(backend)
        iso = isolation.get(backend, {}).get("score", 0)
        scores[backend] = {
            "reliability_35": reliability,
            "operation_15": operation,
            "target_15": target,
            "probability_10": probability,
            "performance_15": speed,
            "isolation_10": iso,
            "total": round(
                reliability + operation + target + probability + (speed or 0) + iso, 4
            ),
        }
    return {
        "record_root": str(record_root),
        "run_count": len(runs),
        "backends": backends,
        "reports": reports,
        "task_medians": {f"{task}|{backend}": value for (task, backend), value in sorted(medians.items())},
        "task_run_counts": {f"{task}|{backend}": value for (task, backend), value in sorted(counts.items())},
        "performance_by_task": per_task,
        "isolation": isolation,
        "scores": scores,
    }


def fmt(value, digits=3):
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(payload):
    backends = payload["backends"]
    reports = payload["reports"]
    scores = payload["scores"]
    isolation = payload["isolation"]
    lines = [
        "| 指标 | " + " | ".join(BACKEND_LABELS.get(name, name) for name in backends) + " |",
        "|---|" + "---:|" * len(backends),
    ]
    rows = [
        ("Validator pass rate", lambda b: fmt(reports[b]["validator_pass_rate"])),
        ("Operation top-1 accuracy", lambda b: fmt(reports[b]["operation_top1_accuracy"])),
        ("Target top-1 accuracy", lambda b: fmt(reports[b]["target_top1_accuracy"])),
        ("Correct operation probability", lambda b: fmt(reports[b]["mean_correct_operation_probability"])),
        ("Correct target probability", lambda b: fmt(reports[b]["mean_correct_target_probability"])),
        ("Median wall time (ms, all tasks)", lambda b: fmt(median_wall(payload, b), 0)),
        ("Jev calls", lambda b: reports[b]["jev_calls"]),
        ("Action success rate", lambda b: fmt(reports[b]["action_success_rate"])),
        ("Stale count", lambda b: reports[b]["stale_count"]),
        ("Backend operation time (ms)", lambda b: reports[b]["backend_operation_ms_total"]),
        ("Isolation / cleanup", lambda b: f"{isolation.get(b, {}).get('score', 0)}/10"),
        ("Reliability score /35", lambda b: fmt(scores[b]["reliability_35"], 2)),
        ("Operation score /15", lambda b: fmt(scores[b]["operation_15"], 2)),
        ("Target score /15", lambda b: fmt(scores[b]["target_15"], 2)),
        ("Probability score /10", lambda b: fmt(scores[b]["probability_10"], 2)),
        ("Performance score /15", lambda b: fmt(scores[b]["performance_15"], 2)),
        ("Isolation score /10", lambda b: fmt(scores[b]["isolation_10"], 2)),
        ("**TOTAL /100**", lambda b: f"**{fmt(scores[b]['total'], 1)}**"),
    ]
    for label, getter in rows:
        lines.append("| " + label + " | " + " | ".join(str(getter(name)) for name in backends) + " |")
    return "\n".join(lines)


def median_wall(payload, backend):
    samples = payload["reports"][backend].get("wall_samples") or []
    return statistics.median(samples) if samples else None


def closing_paragraph(payload):
    scores = payload["scores"]
    reports = payload["reports"]
    backends = payload["backends"]
    ordered = sorted(backends, key=lambda name: scores[name]["total"], reverse=True)
    lines = ["最终成绩对比", ""]
    for name in backends:
        score = scores[name]
        report = reports[name]
        lines.append(f"{BACKEND_LABELS.get(name, name)}:")
        lines.append(
            f"- Reliability: {fmt(score['reliability_35'], 1)}/35 "
            f"(validator {report['validator_passed']}/{report['runs']} runs)"
        )
        lines.append(
            f"- Jev operation judgment: {fmt(score['operation_15'], 1)}/15 "
            f"(top-1 {fmt(report['operation_top1_accuracy'])} over {report['operation_steps_scored']} steps)"
        )
        lines.append(
            f"- Jev target selection: {fmt(score['target_15'], 1)}/15 "
            f"(top-1 {fmt(report['target_top1_accuracy'])} over {report['target_steps_scored']} steps)"
        )
        lines.append(
            f"- Probability/confidence: {fmt(score['probability_10'], 1)}/10 "
            f"(correct operation {fmt(report['mean_correct_operation_probability'])}, "
            f"correct target {fmt(report['mean_correct_target_probability'])})"
        )
        lines.append(f"- Performance: {fmt(score['performance_15'], 1)}/15")
        lines.append(f"- Isolation/cleanup: {fmt(score['isolation_10'], 1)}/10")
        lines.append("")
    lines.append("本轮原始结果：")
    for name in backends:
        report = reports[name]
        lines.append(
            f"- {BACKEND_LABELS.get(name, name)}: {report['validator_passed']}/{report['runs']} runs passed"
        )
    for task in sorted({key.split("|")[0] for key in payload["task_medians"]}):
        parts = [
            f"{BACKEND_LABELS.get(name, name)} {fmt(payload['task_medians'].get(f'{task}|{name}'), 0)} ms"
            for name in backends
            if f"{task}|{name}" in payload["task_medians"]
        ]
        if parts:
            lines.append(f"- {task} median wall: " + "; ".join(parts))
    # The comparison is the last thing the reader sees, as the specification
    # requires the report to end with it.
    lines.append("")
    lines.append("最终成绩对比")
    lines.append("")
    for name in backends:
        lines.append(f"{BACKEND_LABELS.get(name, name)}: {fmt(scores[name]['total'], 1)} / 100")
    if len(backends) >= 2:
        lines.append(f"分差: {fmt(abs(scores[ordered[0]]['total'] - scores[ordered[1]]['total']), 1)}")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record-root", default="recordings/final")
    parser.add_argument("--json-out", default=None, help="Defaults to <record-root>/score.json")
    parser.add_argument("--markdown-out", default=None, help="Defaults to <record-root>/score.md")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    payload = build_score(arguments.record_root)
    json_out = Path(arguments.json_out or Path(arguments.record_root) / "score.json")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    markdown = markdown_table(payload) + "\n\n" + closing_paragraph(payload) + "\n"
    markdown_out = Path(arguments.markdown_out or Path(arguments.record_root) / "score.md")
    markdown_out.write_text(markdown)
    if not arguments.quiet:
        print(markdown)
        print(f"\nwrote {json_out} and {markdown_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())