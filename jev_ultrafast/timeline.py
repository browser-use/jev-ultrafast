"""A bounded, redacted event timeline for diagnosing ref and stale lifecycles.

Only code-owned identity is recorded: generations, indices, Ego refs, operations,
freshness verdicts, error classes, and page identity.  Typed text, credentials,
cookies, and page bodies never enter the timeline.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# Anything that looks like a credential is replaced before it can be written.
_SECRET = re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret|bearer|cookie)\s*[:=]?\s*\S+")
_MAX_DETAIL = 200
_MAX_EVENTS = 400


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_DETAIL else value[:_MAX_DETAIL]
    if isinstance(value, (list, tuple)):
        return [_scalar(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _scalar(item) for key, item in list(value.items())[:20]}
    return str(value)[:_MAX_DETAIL]


class Timeline:
    """Append-only diagnostic events, optionally mirrored to ``events.jsonl``."""

    def __init__(self, record_dir: str | Path | None = None) -> None:
        self.started_at = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self.path: Path | None = Path(record_dir) / "events.jsonl" if record_dir else None
        self._handle = None

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"ts_ms": round((time.perf_counter() - self.started_at) * 1000), "event": event}
        for name, value in fields.items():
            if value is None:
                continue
            entry[name] = self._redact(_scalar(value))
        self.events.append(entry)
        if len(self.events) > _MAX_EVENTS:
            del self.events[: len(self.events) - _MAX_EVENTS]
        self._append(entry)
        return entry

    @staticmethod
    def _redact(value: Any) -> Any:
        if isinstance(value, str):
            return _SECRET.sub(r"\1=<redacted>", value)
        if isinstance(value, list):
            return [Timeline._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: Timeline._redact(item) for key, item in value.items()}
        return value

    def _append(self, entry: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8")
            self._handle.write(json.dumps(entry, sort_keys=True) + "\n")
            self._handle.flush()
        except OSError:
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.events)


__all__ = ["Timeline"]