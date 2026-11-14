"""Summarize live run directories: per-run rows plus median/min/max per group.

uv run python scripts/live_summary.py recordings/v2 [more-run-dirs ...] [--json]

Numbers only; this script does not rank the backends.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

GROUP_KEYS = ("label", "backend")
SUMMARY_METRIC_KEYS = ("wall_elapsed_ms", "jev_calls", "action_success_rate", "stale_count", "ego_operation_ms")
DEFAULT_GROUP = {"label": "unlabeled", "backend": "unknown"}


def load_run(directory):
    """Read summary.json when present, else the Agent's own metrics.json."""
    directory = Path(directory)
    summary = _read_json(directory / "summary.json")
    metrics = _read_json(directory / "metrics.json")
    version = _read_json(directory / "version_manifest.json")
    if summary is None and metrics is None:
        return None
    row = {
        "run_dir": str(directory),
        "run_id": str(summary.get("run_id") if summary else directory.name),
        "label": (summary or {}).get("label", DEFAULT_GROUP["label"]),
        "backend": (summary or metrics or {}).get("backend", DEFAULT_GROUP["backend"]),
        "status": (summary or metrics or {}).get("status"),
        "error_type": (summary or metrics or {}).get("error_type"),
        "source": "summary.json" if summary else "metrics.json",
        "metrics_path": str((directory / "metrics.json") if (directory / "metrics.json").exists() else ""),
    }
    for key in SUMMARY_METRIC_KEYS:
        row[key] = _metric(summary, metrics, key)
    for key in ("pid_alive_after_close", "orphan_process_remaining", "runner_failure"):
        row[key] = (summary or {}).get(key)
    for key in ("git_commit", "implementation_hash"):
        row[key] = (summary or {}).get(key, (version or {}).get(key))
    return row


def _metric(summary, metrics, key):
    for source in (summary, metrics):
        if isinstance(source, dict) and key in source:
            return source[key]
    if key == "ego_operation_ms" and isinstance(metrics, dict):
        return metrics.get("ego_total_operation_ms")
    if key == "action_success_rate" and isinstance(metrics, dict):
        attempted = metrics.get("browser_actions_attempted")
        succeeded = metrics.get("browser_actions_succeeded")
        if isinstance(attempted, int) and isinstance(succeeded, int):
            return round(succeeded / attempted, 6) if attempted else 0.0
    return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def discover_runs(root):
    """Every immediate child directory of a record root, sorted by name."""
    root = Path(root)
    if (root / "summary.json").exists() or (root / "metrics.json").exists():
        return [root]
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


def aggregate(rows):
    """Median, min, and max per metric; a single sample and an empty group both work."""
    aggregate_rows = {}
    for key in SUMMARY_METRIC_KEYS:
        values = [
            row[key] for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
        ]
        if values:
            aggregate_rows[key] = {
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }
        else:
            aggregate_rows[key] = {"median": None, "min": None, "max": None, "count": 0}
    return aggregate_rows


def group_rows(rows):
    grouped = {}
    for row in rows:
        key = (str(row.get("label", DEFAULT_GROUP["label"])), str(row.get("backend", DEFAULT_GROUP["backend"])))
        grouped.setdefault(key, []).append(row)
    return grouped


def build_report(rows):
    return {
        f"{label}\t{backend}": {"label": label, "backend": backend, "rows": group, "aggregate": aggregate(group)}
        for (label, backend), group in sorted(group_rows(rows).items())
    }


def _number(value):
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.3f}"
    return str(value)


def print_report(rows):
    for (label, backend), group in sorted(group_rows(rows).items()):
        print(f"{label} / {backend}  ({len(group)} runs)")
        header = f"{'run':<28} {'status':<8} " + " ".join(f"{key:>18}" for key in SUMMARY_METRIC_KEYS)
        print(header)
        for row in group:
            cells = " ".join(f"{_number(row.get(key)):>18}" for key in SUMMARY_METRIC_KEYS)
            print(f"{row['run_id'][:28]:<28} {str(row.get('status'))[:8]:<8} {cells}")
        aggregate_rows = aggregate(group)
        for name in ("median", "min", "max"):
            cells = " ".join(f"{_number(aggregate_rows[key][name]):>18}" for key in SUMMARY_METRIC_KEYS)
            print(f"{name:<28} {'':<8} {cells}")
        print()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="A record root or one or more run directories.")
    parser.add_argument("--json", action="store_true", help="Emit the same data as JSON.")
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    rows = []
    for value in arguments.paths:
        for directory in discover_runs(value):
            row = load_run(directory)
            if row is not None:
                rows.append(row)
    if arguments.json:
        print(json.dumps({"rows": rows, "aggregate": build_report(rows)}, indent=2, sort_keys=True, default=str))
    else:
        print_report(rows)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())