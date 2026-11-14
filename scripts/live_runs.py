"""Repeat one live task N times and record process and version evidence per run.

uv run --env-file .env python scripts/live_runs.py \
  --backend ego --url https://en.wikipedia.org/wiki/Main_Page \
  --goal "Find and open the Wikipedia article about Godel's incompleteness theorems." \
  --runs 5 --label wikipedia --record-root recordings/v2 --timeout-seconds 180

Live runs make paid API calls; --dry-run prints the matrix without one.

Each run gets <record-root>/<label>-<n>/ (or <label>-<backend>-<n>/ with --ab)
holding the Agent's metrics.json and trace.json plus summary.json,
process_evidence.json, and version_manifest.json. --timeout-seconds is the
watchdog budget for each phase (construct, run, close), so one run can take up
to about three times that value before it is declared hung.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_TIMEOUT_SECONDS = 240.0
DEFAULT_KILL_GRACE_SECONDS = 5.0
EGO_SCAN_PATTERN = "ego-browser nodejs"
SUMMARY_METRIC_KEYS = (
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
)
EGO_CLOSE_ATTRIBUTES = (
    "runtime_spawn_count",
    "orphan_process_remaining",
    "graceful_exit",
    "forced_terminate",
    "forced_kill",
)
AGGREGATE_KEYS = ("wall_elapsed_ms", "jev_calls", "action_success_rate", "stale_count", "ego_operation_ms")


class RunTimeout(Exception):
    """A watchdog interrupted one run so the next one can start."""


def utc_now():
    """Wall clock timestamp for artifacts and run ids."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return path


def sanitize_slug(text):
    """One filesystem-safe path component, without guessing site-specific values."""
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in str(text).strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "run"


def git_state(root):
    """The worktree revision recorded in every version manifest."""

    def run(*arguments):
        try:
            done = subprocess.run(
                ["git", *arguments], cwd=root, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {"git_commit": commit, "git_dirty": bool(status) if status is not None else None}


def implementation_files(source_root):
    """Every jev_ultrafast Python file plus snapshot.js, in a deterministic order."""
    root = Path(source_root) / "jev_ultrafast"
    if not root.is_dir():
        return []
    files = [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]
    snapshot = root / "snapshot.js"
    if snapshot.is_file():
        files.append(snapshot)
    return sorted(files)


def implementation_hash(source_root):
    """SHA-256 over sorted (relative_path, sha256(content)) pairs of the implementation."""
    root = Path(source_root)
    digest = hashlib.sha256()
    for path in implementation_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
    return digest.hexdigest()


def version_manifest(source_root, *, ego_browser=None):
    """Source revision, implementation hash, interpreter, and Ego executable path."""
    root = Path(source_root)
    return {
        **git_state(root),
        "implementation_hash": implementation_hash(root),
        "python": sys.version.split()[0],
        "ego_browser": str(ego_browser or shutil.which("ego-browser") or "") or None,
    }


def pid_alive(pid, states=None):
    """True only when the process still runs; an unreaped zombie of this process is not alive."""
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    # The harness usually owns the Ego transport, so reap a zombie we started.
    try:
        if os.waitpid(number, os.WNOHANG)[0] == number:
            return False
    except (ChildProcessError, OSError):
        pass
    state = (states or system_process_table([number])).get(number, {}).get("state", "")
    return not state.upper().startswith("Z")


def wait_for_pid_exit(pid, timeout=DEFAULT_KILL_GRACE_SECONDS, interval=0.1):
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(interval)
    return not pid_alive(pid)


def kill_process_tree(pid, grace=DEFAULT_KILL_GRACE_SECONDS, *, reap=False):
    """Best-effort SIGKILL of one pid and its descendants, for hung runs only."""
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 1:
        return False
    for child in process_table(number).get(number, {}).get("children", []):
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.kill(number, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    wait_for_pid_exit(number, grace)
    if reap:
        try:
            os.waitpid(number, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
    return not pid_alive(number)


def system_process_table(pids):
    """pid -> ppid, state, and full command, read from ps without inspecting page data."""
    numbers = sorted({int(pid) for pid in pids if pid})
    if not numbers:
        return {}
    table = {}
    try:
        done = subprocess.run(
            ["ps", "-o", "pid=,ppid=,state=,command=", "-p", ",".join(str(pid) for pid in numbers)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return table
    for line in done.stdout.splitlines():
        fields = line.split(None, 3)
        if len(fields) >= 2 and fields[0].isdigit():
            table[int(fields[0])] = {
                "ppid": int(fields[1]) if fields[1].isdigit() else None,
                "state": fields[2] if len(fields) > 2 else "",
                "command": fields[3].strip() if len(fields) > 3 else "",
            }
    return table


def process_table(pid=None):
    """Include direct children of pid when asking about one process."""
    if not pid:
        return system_process_table([])
    number = int(pid)
    table = system_process_table([number])
    if os.name == "posix":
        try:
            done = subprocess.run(
                ["pgrep", "-P", str(number)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            done = None
        children = sorted({int(line) for line in done.stdout.split() if line.isdigit()}) if done else []
        table.update(system_process_table(set(table) | set(children)))
        if number in table:
            table[number]["children"] = children
    return table


def scan_ego_processes(pattern=EGO_SCAN_PATTERN):
    """Current `pgrep -f` matches for the Ego runtime command line."""
    try:
        done = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode not in (0, 1):
        return []
    return sorted({int(line) for line in done.stdout.split() if line.isdigit()})


def _read_path(value, path):
    current = value
    for step in path:
        if current is None:
            return None
        if isinstance(step, int):
            items = list(current) if isinstance(current, (list, tuple)) else []
            current = items[step] if 0 <= step < len(items) else None
        else:
            current = getattr(current, step, None)
    return current


def _process_pid(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    for attribute in ("pid", "process_id"):
        found = getattr(value, attribute, None)
        if isinstance(found, int) and found > 0:
            return found
    return None


def extract_ego_child_pids(backend):
    """Observe the live Ego runtime pid(s), or an empty list when it does not apply."""
    if backend is None:
        return []
    candidates = []
    for path in (
        ("transport", "process"),
        ("transport",),
        ("process",),
        ("children",),
        ("processes",),
        ("transport", "children"),
    ):
        found = _read_path(backend, path)
        if found is None:
            continue
        items = list(found) if isinstance(found, (list, tuple)) else [found]
        candidates.extend(_process_pid(item) for item in items)
    return sorted({pid for pid in candidates if pid})


def process_group_ids(pids, table=None):
    """Process groups led by these pids; used to reap a hung runtime and its descendants."""
    values = sorted({int(pid) for pid in pids if pid})
    table = table if table is not None else system_process_table(values)
    groups = []
    for pid in values:
        if table.get(pid) is None:
            continue  # no ps on this machine; fall back to single-pid cleanup
        try:
            group = os.getpgid(pid)
        except OSError:
            continue
        if group > 1 and group == pid and group not in groups:
            groups.append(group)
    return groups


def kill_runtime(pids, grace=DEFAULT_KILL_GRACE_SECONDS):
    """Kill a hung Ego runtime: its process group first, then individual survivors with reaping."""
    pids = sorted({int(pid) for pid in pids if pid})
    groups = process_group_ids(pids)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass
    for pid in pids:
        if pid_alive(pid):
            kill_process_tree(pid, grace, reap=True)
    return {"process_groups": groups, "killed_pids": [pid for pid in pids if not pid_alive(pid)]}


def collect_backend_evidence(agent):
    """Ego-facing values observed while the Agent is still alive, before close()."""
    backend = getattr(agent, "browser", None)
    evidence = {"ego_child_pids": extract_ego_child_pids(backend)}
    for name in EGO_CLOSE_ATTRIBUTES:
        evidence[name] = getattr(backend, name, None)
    timings = getattr(backend, "ego_timings", None)
    evidence["ego_timings"] = json.loads(json.dumps(timings, default=str)) if isinstance(timings, dict) else None
    status, error = "unknown", None
    try:
        snapshot = agent.snapshot()
    except Exception as caught:  # noqa: BLE001 - evidence must never break a run
        snapshot = None
        error = f"{type(caught).__name__}: {caught}"
    if isinstance(snapshot, dict):
        status = snapshot.get("status", "unknown")
        metrics = snapshot.get("metrics")
        # Taken before close(), so cleanup counters live in metrics.json instead.
        evidence["metrics_before_close"] = (
            json.loads(json.dumps(metrics, default=str)) if isinstance(metrics, dict) else None
        )
    evidence["final_status"] = status
    evidence["snapshot_error"] = error
    return evidence


def collect_process_evidence(run_id, backend_name, pids, observed_at, metrics=None, baseline_pids=None):
    """Post-close evidence: which runtime pids survived and what pgrep still sees.

    An orphan is only attributed to this run when it is one of the run's tracked
    pids or a process the global scan did not already see before the run started.
    A shared machine keeps unrelated ``ego-browser`` processes around, and this
    benchmark runs one runtime at a time, so the baseline is what makes the
    isolation check honest instead of a false positive.
    """
    observed = sorted({int(pid) for pid in pids if pid})
    baseline = sorted({int(pid) for pid in (baseline_pids or []) if pid})
    states = system_process_table(observed)
    liveness = {}
    notes = []
    for pid in observed:
        alive = pid_alive(pid, states)
        liveness[str(pid)] = alive
        if alive:
            notes.append(f"pid {pid} is still alive after the Agent closed")
    scan = {"pattern": EGO_SCAN_PATTERN, "matches": None, "baseline": baseline, "attributed": []}
    attributed = []
    if backend_name == "ego":
        matches = scan_ego_processes()
        scan["matches"] = matches
        attributed = sorted(set(matches) - set(baseline))
        scan["attributed"] = attributed
        if attributed:
            notes.append(f"pgrep -f {EGO_SCAN_PATTERN!r} shows new matches {attributed}")
        elif matches:
            notes.append(
                f"pgrep -f {EGO_SCAN_PATTERN!r} still matches {matches}, "
                "all of them already present before this run"
            )
    survivors = sorted(pid for pid, alive in liveness.items() if alive)
    if backend_name == "ego":
        orphan = bool(survivors) or bool(attributed)
    else:
        orphan = None
        notes.append("no Ego runtime is started by browser_harness; only this process is observable")
    return {
        "backend": backend_name,
        "run_id": run_id,
        "transport_pids": observed,
        "children": observed,
        "pid_alive_after_close": liveness,
        "surviving_pids": [int(pid) for pid in survivors],
        "orphan_process_remaining": orphan,
        "post_close_scan": scan,
        "observed_at_utc": observed_at,
        "ego_browser": shutil.which("ego-browser"),
        "metrics": metrics,
        "notes": notes,
    }


def pid_alive_after_close(liveness):
    """True when a tracked runtime survived; None when there was no pid to check."""
    if not liveness:
        return None
    return any(liveness.values())


@contextmanager
def watchdog(seconds):
    """In-process SIGALRM deadline; RunTimeout still lets the with-block unwind."""
    if not seconds or seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return
    armed = {"value": True}

    def on_alarm(_signum, _frame):
        if armed["value"]:
            raise RunTimeout(f"run exceeded {seconds:g}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL) if hasattr(signal, "getitimer") else None
    signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        # Disarm this deadline only; a later watchdog installs its own handler and timer.
        armed["value"] = False
        if previous_timer is not None and previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        else:
            signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            signal.signal(signal.SIGALRM, previous_handler)
        except (OSError, ValueError, TypeError):
            pass


def _run_phase(agent_factory, timeout_seconds, evidence, phase):
    """Construct and run one Agent; each phase has its own watchdog deadline."""
    agent = None
    evidence["phase"] = "construct"
    try:
        with watchdog(timeout_seconds):
            agent = agent_factory()
    finally:
        # A constructor that raises part-way still exposes the live backend for evidence.
        evidence["agent"] = agent
        if agent is not None:
            evidence.update(collect_backend_evidence(agent))
    evidence["phase"] = phase
    try:
        with watchdog(timeout_seconds):
            for _state in agent.run():
                pass
    finally:
        try:
            evidence.update(collect_backend_evidence(agent))
        except BaseException:  # noqa: BLE001 - evidence must never mask the run error
            pass
    return agent


def default_agent_factory(run):
    """Construct the real Agent for one plan entry; imported lazily so --dry-run stays free."""
    from jev_ultrafast import Agent

    return Agent(run["url"], run["goal"], backend=run["backend"], record_dir=run["record_dir"])


def run_once(
    run, *, agent_factory=None, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, kill_grace=DEFAULT_KILL_GRACE_SECONDS
):
    """One measured run with its own record directory and evidence artifacts."""
    if agent_factory is None:
        agent_factory = lambda: default_agent_factory(run)  # noqa: E731 - one small closure per run
    record_dir = Path(run["record_dir"])
    record_dir.mkdir(parents=True, exist_ok=True)
    manifest = version_manifest(run["source_root"])
    write_json(record_dir / "version_manifest.json", manifest)
    # What the Ego scan already sees before this run starts, so an unrelated
    # runtime on the same machine can never be reported as this run's orphan.
    process_baseline = scan_ego_processes() if run["backend"] == "ego" else []
    started = time.perf_counter()
    failure = None
    close_started = None
    pids = []
    evidence = {"final_status": "not_started"}
    agent = None
    try:
        agent = _run_phase(agent_factory, timeout_seconds, evidence, "run")
        pids = list(evidence.get("ego_child_pids") or [])
    except BaseException as error:  # noqa: BLE001 - every failure becomes one recorded run
        failure = {
            "run_id": run["run_id"],
            "timeout_seconds": timeout_seconds,
            "phase": evidence.get("phase") or "run",
            "exception": type(error).__name__,
            "message": str(error),
        }
        # The backend was observed while the run failed; its runtime still needs closing.
        agent = evidence.get("agent")
        pids = list(evidence.get("ego_child_pids") or [])
    close_failure = None
    if agent is not None:
        close_started = time.perf_counter()
        try:
            with watchdog(timeout_seconds):
                agent.close()
        except RunTimeout:
            close_failure = f"close exceeded {timeout_seconds:g}s"
            kill_runtime(pids, kill_grace)
        except BaseException as close_error:  # noqa: BLE001 - recorded, not raised
            close_failure = f"{type(close_error).__name__}: {close_error}"
    close_seconds = round((time.perf_counter() - close_started) * 1000) if close_started else None
    observed_at = utc_now()
    metrics = _read_json(record_dir / "metrics.json")
    if isinstance(metrics, dict) and metrics.get("status"):
        status = metrics["status"]
    elif failure and failure["phase"] == "construct":
        status = "not_started"  # the Agent never reached a model outcome
    else:
        status = evidence.get("final_status") or "missing_metrics"
    process_evidence = collect_process_evidence(
        run["run_id"], run["backend"], pids, observed_at, metrics, baseline_pids=process_baseline
    )
    write_json(record_dir / "process_evidence.json", process_evidence)
    summary = {
        # Recorded metrics first; the harness-derived lifecycle fields below win.
        **{key: (metrics.get(key) if isinstance(metrics, dict) else None) for key in SUMMARY_METRIC_KEYS},
        "run_id": run["run_id"],
        "label": run["label"],
        "backend": run["backend"],
        "url": run["url"],
        "goal": run["goal"],
        "run_index": run["run_index"],
        "record_dir": str(record_dir),
        "status": status,
        "orphan_process_remaining": process_evidence["orphan_process_remaining"],
        "pid_alive_after_close": pid_alive_after_close(process_evidence["pid_alive_after_close"]),
        "pid_liveness": process_evidence["pid_alive_after_close"],
        "ego_child_pids": process_evidence["transport_pids"],
        "runner_elapsed_ms": round((time.perf_counter() - started) * 1000),
        "close_elapsed_ms": close_seconds,
        "close_failure": close_failure,
        "timeout_seconds": timeout_seconds,
        "runner_failure": failure,
        "wall_clock_utc": observed_at,
        "git_commit": manifest["git_commit"],
        "git_dirty": manifest["git_dirty"],
        "implementation_hash": manifest["implementation_hash"],
        "version_manifest": str(record_dir / "version_manifest.json"),
        "process_evidence": str(record_dir / "process_evidence.json"),
    }
    if isinstance(evidence.get("ego_timings"), dict):
        summary["ego_timings"] = evidence["ego_timings"]
    write_json(record_dir / "summary.json", summary)
    if failure is not None:
        write_json(record_dir / "runner_failure.json", failure)
    return summary


def run_directory_name(label, backend, index, ab_mode):
    if ab_mode:
        return f"{sanitize_slug(label)}-{sanitize_slug(backend)}-{int(index)}"
    return f"{sanitize_slug(label)}-{int(index)}"


def build_plan(*, url, goal, backends, runs, record_root, label=None, source_root=".", ab=False):
    """The deterministic run matrix; --dry-run prints exactly this."""
    label = sanitize_slug(label or "run")
    record_root = Path(record_root)
    plan = []
    if ab:
        for index in range(1, int(runs) + 1):
            for backend in backends:
                plan.append(
                    {
                        "run_index": index,
                        "run_id": run_directory_name(label, backend, index, True),
                        "backend": backend,
                        "url": url,
                        "goal": goal,
                        "label": label,
                        "record_dir": str(record_root / run_directory_name(label, backend, index, True)),
                        "source_root": source_root,
                    }
                )
    else:
        for index in range(1, int(runs) + 1):
            run_id = run_directory_name(label, backends[0], index, False)
            plan.append(
                {
                    "run_index": index,
                    "run_id": run_id,
                    "backend": backends[0],
                    "url": url,
                    "goal": goal,
                    "label": label,
                    "record_dir": str(record_root / run_id),
                    "source_root": source_root,
                }
            )
    return plan


def aggregate_rows(rows):
    """Median, min, and max per metric over finished runs, skipping missing values."""
    aggregate = {}
    for key in AGGREGATE_KEYS:
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)]
        if values:
            aggregate[key] = {
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }
        else:
            aggregate[key] = {"median": None, "min": None, "max": None, "count": 0}
    return aggregate


def group_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault((str(row.get("label")), str(row.get("backend"))), []).append(row)
    return grouped


def print_aggregate(rows):
    print(f"{'label':<16} {'backend':<16} {'metric':<20} {'n':>2} {'median':>10} {'min':>10} {'max':>10}")
    for (label, backend), group in group_rows(rows).items():
        for key, stats in aggregate_rows(group).items():
            print(
                f"{label:<16} {backend:<16} {key:<20} {stats['count']:>2} "
                f"{_number(stats['median']):>10} {_number(stats['min']):>10} {_number(stats['max']):>10}"
            )


def _number(value):
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.3f}"
    return str(value)


def default_label(url):
    """A readable label from the URL path, else its host, else the raw text."""
    parsed = urlparse(str(url))
    path = parsed.path.strip("/")
    return sanitize_slug(path or parsed.netloc or url)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label")
    parser.add_argument("--record-root", default="recordings/v2")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--kill-grace-seconds", type=float, default=DEFAULT_KILL_GRACE_SECONDS)
    parser.add_argument("--backend", action="append", choices=("browser_harness", "ego"), dest="backend")
    parser.add_argument(
        "--backends",
        dest="backends",
        help="Comma-separated backend order, for example browser_harness,ego.",
    )
    parser.add_argument("--ab", action="store_true", help="Round-robin the backends in time: A, B, A, B, ...")
    parser.add_argument("--interleave", action="store_true", help="Alias for --ab.")
    parser.add_argument("--dry-run", action="store_true", help="Print the run matrix and exit without a browser.")
    parser.add_argument("--force", action="store_true", help="Allow writing into existing run directories.")
    parser.add_argument("--source-root")
    return parser


def resolve_backends(arguments):
    """Backend order from repeated --backend or one comma-separated --backends."""
    backends = []
    for value in arguments.backend or []:
        backends.append(value.strip().lower())
    for value in (arguments.backends or "").split(","):
        if value.strip():
            backends.append(value.strip().lower())
    if not backends:
        backends = [os.environ.get("ULTRAFAST_BROWSER_BACKEND", "browser_harness").strip().lower()]
    unknown = [value for value in backends if value not in {"browser_harness", "ego"}]
    if unknown:
        raise SystemExit(f"Unknown backend(s): {', '.join(unknown)}")
    return backends


def main(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    backends = resolve_backends(arguments)
    ab_mode = bool(arguments.ab or arguments.interleave)
    if ab_mode and len(backends) < 2:
        parser.error("--ab needs two backends, for example --backends browser_harness,ego")
    if not ab_mode and len(backends) > 1:
        parser.error("several backends need --ab; one backend per run keeps A and B interleaved in time")
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    label = arguments.label or default_label(arguments.url)
    if arguments.source_root:
        source_root = Path(arguments.source_root).resolve()
    else:
        source_root = Path(__file__).resolve().parents[1]
    plan = build_plan(
        url=arguments.url,
        goal=arguments.goal,
        backends=backends,
        runs=arguments.runs,
        record_root=arguments.record_root,
        label=label,
        source_root=str(source_root),
        ab=ab_mode,
    )
    if arguments.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ab": ab_mode,
                    "backend_order": backends,
                    "runs": len(plan),
                    "record_root": str(arguments.record_root),
                    "timeout_seconds": arguments.timeout_seconds,
                    "plan": plan,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    existing = [run["record_dir"] for run in plan if Path(run["record_dir"]).exists()]
    if existing and not arguments.force:
        parser.error(f"run directories already exist: {', '.join(existing)}; reuse --force to overwrite")
    rows = []
    for run in plan:
        summary = run_once(
            run,
            timeout_seconds=arguments.timeout_seconds,
            kill_grace=arguments.kill_grace_seconds,
        )
        rows.append(summary)
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "run_id",
                        "label",
                        "backend",
                        "status",
                        "wall_elapsed_ms",
                        "policy_elapsed_ms",
                        "jev_calls",
                        "browser_actions_attempted",
                        "browser_actions_succeeded",
                        "action_success_rate",
                        "stale_count",
                        "ego_operation_ms",
                        "pid_alive_after_close",
                        "orphan_process_remaining",
                        "error_type",
                        "runner_failure",
                    )
                },
                sort_keys=True,
            ),
            flush=True,
        )
    print_aggregate(rows)
    return 1 if any(row["status"] not in {"done", "blocked"} for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())