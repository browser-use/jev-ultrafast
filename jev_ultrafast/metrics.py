"""Small, backend-independent run metrics collector."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


class RunMetrics:
    """Collect timing and outcome counters without retaining page or credential data."""

    def __init__(self, backend: str):
        self.backend = backend
        self.wall_started_at = time.perf_counter()
        self.policy_started_at: float | None = None
        self.wall_elapsed_ms = 0
        self.policy_elapsed_ms = 0
        self.jev_calls = 0
        self.jev_total_latency_ms = 0
        self.browser_actions_attempted = 0
        self.browser_actions_succeeded = 0
        self.stale_count = 0
        self.backend_operation_ms = 0
        self.ego_operation_ms = 0
        self.ego_observe_ms = 0
        self.ego_act_ms = 0
        self.ego_fresh_ms = 0
        self.ego_wait_ms = 0
        self._ego_operation_count = 0
        self._ego_operation_max_ms = 0
        self._ego_counts: dict[str, int] = defaultdict(int)
        self.text_helper_calls = 0
        self.text_helper_total_ms = 0
        self.backend_startup_ms = 0
        self.backend_cleanup_ms = 0
        self.runtime_spawn_count: int | None = None
        self.orphan_process_remaining: bool | None = None
        self.graceful_exit: bool | None = None
        self.forced_terminate: bool | None = None
        self.forced_kill: bool | None = None
        self.actions_by_kind: dict[str, dict[str, int]] = defaultdict(
            lambda: {"attempted": 0, "succeeded": 0}
        )
        self.status = "running"
        self.error_type: str | None = None
        self._finished_at: float | None = None

    def start_policy(self) -> None:
        if self.policy_started_at is None:
            self.policy_started_at = time.perf_counter()

    def record_jev(self, latency_ms: int | float | None = None) -> None:
        self.jev_calls += 1
        if latency_ms is not None:
            self.jev_total_latency_ms += max(0, round(latency_ms))

    def record_action_attempt(self, kind: str) -> None:
        self.browser_actions_attempted += 1
        self.actions_by_kind[kind]["attempted"] += 1

    def record_action_success(self, kind: str) -> None:
        self.browser_actions_succeeded += 1
        self.actions_by_kind[kind]["succeeded"] += 1

    def record_stale(self) -> None:
        self.stale_count += 1

    def record_backend_operation(self, operation: str, elapsed_ms: int | float) -> None:
        elapsed = max(0, round(elapsed_ms))
        self.backend_operation_ms += elapsed
        if self.backend == "ego":
            self.ego_operation_ms += elapsed
            self._ego_operation_count += 1
            self._ego_counts[operation] += 1
            self._ego_operation_max_ms = max(self._ego_operation_max_ms, elapsed)
            if operation == "observe":
                self.ego_observe_ms += elapsed
            elif operation == "act":
                self.ego_act_ms += elapsed
            elif operation == "fresh":
                self.ego_fresh_ms += elapsed
            elif operation == "wait":
                self.ego_wait_ms += elapsed

    def record_text_helper(self, elapsed_ms: int | float) -> None:
        self.text_helper_calls += 1
        self.text_helper_total_ms += max(0, round(elapsed_ms))

    def finish(self, status: str, error: BaseException | None = None) -> dict[str, Any]:
        self.status = status
        if self._finished_at is None:
            self._finished_at = time.perf_counter()
            self.wall_elapsed_ms = round((self._finished_at - self.wall_started_at) * 1000)
            self.policy_elapsed_ms = (
                round((self._finished_at - self.policy_started_at) * 1000)
                if self.policy_started_at is not None
                else 0
            )
        if error is not None:
            self.error_type = type(error).__name__
        return self.snapshot()

    def merge_backend(self, backend: object) -> None:
        """Copy optional backend diagnostics while keeping the core interface tiny."""
        backend_metrics = getattr(backend, "metrics", None)
        if not isinstance(backend_metrics, dict):
            backend_metrics = {}
        value = backend_metrics.get("runtime_spawn_count", getattr(backend, "runtime_spawn_count", None))
        if value is not None:
            self.runtime_spawn_count = value
        cleanup = backend_metrics.get("cleanup_status", getattr(backend, "cleanup_status", None))
        if isinstance(cleanup, dict):
            for name in ("orphan_process_remaining", "graceful_exit", "forced_terminate", "forced_kill"):
                if cleanup.get(name) is not None:
                    setattr(self, name, cleanup[name])
        for name in ("orphan_process_remaining", "graceful_exit", "forced_terminate", "forced_kill"):
            value = getattr(backend, name, None)
            if value is not None:
                setattr(self, name, value)
        timings = backend_metrics.get("ego_timings", getattr(backend, "ego_timings", None))
        if timings is None and any(name in backend_metrics for name in ("observe", "fresh", "act")):
            timings = backend_metrics
        if isinstance(timings, dict):
            totals = {
                "observe": "ego_observe_ms",
                "fresh": "ego_fresh_ms",
                "act": "ego_act_ms",
            }
            for operation, field in totals.items():
                item = timings.get(operation, {})
                if isinstance(item, dict) and isinstance(item.get("total_ms"), (int, float)):
                    setattr(self, field, round(item["total_ms"]))
            total = timings.get("ego_total_operation_ms", timings.get("total_ms"))
            if isinstance(total, (int, float)):
                self.ego_operation_ms = round(total)
            elif self.ego_observe_ms or self.ego_act_ms or self.ego_fresh_ms:
                self.ego_operation_ms = self.ego_observe_ms + self.ego_act_ms + self.ego_fresh_ms
            self._ego_operation_count = sum(
                item.get("count", 0)
                for item in timings.values()
                if isinstance(item, dict) and isinstance(item.get("count"), int)
            )
            self._ego_counts = defaultdict(
                int,
                {
                    operation: item.get("count", 0)
                    for operation, item in timings.items()
                    if isinstance(item, dict) and isinstance(item.get("count"), int)
                },
            )
            self._ego_operation_max_ms = max(
                (item.get("max_ms", 0) for item in timings.values() if isinstance(item, dict)),
                default=0,
            )
        values = backend_metrics.get("timings", getattr(backend, "timings", None))
        if values is None and any(name in backend_metrics for name in ("startup_ms", "cleanup_ms")):
            values = backend_metrics
        if isinstance(values, dict):
            for name in ("startup_ms", "cleanup_ms"):
                value = values.get(name)
                if isinstance(value, (int, float)):
                    setattr(self, "backend_" + name, round(value))

    def snapshot(self) -> dict[str, Any]:
        attempted = self.browser_actions_attempted
        success_rate = self.browser_actions_succeeded / attempted if attempted else 0.0
        operations = self._ego_operation_count
        return {
            "backend": self.backend,
            "status": self.status,
            "wall_elapsed_ms": self.wall_elapsed_ms,
            "policy_elapsed_ms": self.policy_elapsed_ms,
            "jev_calls": self.jev_calls,
            "browser_actions_attempted": attempted,
            "browser_actions_succeeded": self.browser_actions_succeeded,
            "action_success_rate": round(success_rate, 6),
            "stale_count": self.stale_count,
            "backend_operation_ms": self.backend_operation_ms,
            "ego_operation_ms": self.ego_operation_ms,
            "ego_total_operation_ms": self.ego_operation_ms,
            "ego_observe_ms": self.ego_observe_ms,
            "ego_act_ms": self.ego_act_ms,
            "ego_fresh_ms": self.ego_fresh_ms,
            "ego_wait_ms": self.ego_wait_ms,
            "ego_average_operation_ms": round(self.ego_operation_ms / operations, 3) if operations else 0,
            "ego_average_observe_ms": round(self.ego_observe_ms / self._ego_counts["observe"], 3)
            if self._ego_counts["observe"]
            else 0,
            "ego_average_act_ms": round(self.ego_act_ms / self._ego_counts["act"], 3)
            if self._ego_counts["act"]
            else 0,
            "ego_max_operation_ms": self._ego_operation_max_ms,
            "ego_max_single_operation_ms": self._ego_operation_max_ms,
            "text_helper_calls": self.text_helper_calls,
            "text_helper_total_ms": self.text_helper_total_ms,
            "jev_total_latency_ms": self.jev_total_latency_ms,
            "backend_startup_ms": self.backend_startup_ms,
            "backend_cleanup_ms": self.backend_cleanup_ms,
            "actions_by_kind": {kind: dict(counts) for kind, counts in self.actions_by_kind.items()},
            "runtime_spawn_count": self.runtime_spawn_count,
            "graceful_exit": self.graceful_exit,
            "forced_terminate": self.forced_terminate,
            "forced_kill": self.forced_kill,
            "orphan_process_remaining": self.orphan_process_remaining,
            "error_type": self.error_type,
        }

    def write(self, path: str | Path) -> dict[str, Any]:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = self.snapshot()
        destination.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return data
