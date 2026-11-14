"""Offline contracts for the bounded, redacted diagnostic timeline."""

from __future__ import annotations

import json

from jev_ultrafast.timeline import Timeline


def test_recent_events_are_kept_in_memory_and_on_disk(tmp_path):
    timeline = Timeline(tmp_path)
    timeline.record("observe", generation=1, url="https://example.test/", actions=3, unmapped_targets=0)
    timeline.record("decision", generation=1, index="e2", ref="@38", operation="CLICK")
    timeline.record("act", generation=1, index="e2", ref="@38", operation="click", result="success")
    timeline.record("stale", generation=1, source="act", reason="fresh_check_failed")
    timeline.close()

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [item["event"] for item in events] == ["observe", "decision", "act", "stale"]
    assert events[1]["ref"] == "@38" and events[1]["operation"] == "CLICK"
    assert all("ts_ms" in item for item in events)
    assert timeline.snapshot() == events


def test_none_fields_are_dropped_and_long_text_is_truncated(tmp_path):
    timeline = Timeline(tmp_path)
    entry = timeline.record("act", detail="x" * 500, index=None, ref="@3")
    timeline.close()
    assert "index" not in entry
    assert len(entry["detail"]) == 200
    assert entry["ref"] == "@3"


def test_credentials_never_reach_the_timeline(tmp_path):
    timeline = Timeline(tmp_path)
    entry = timeline.record(
        "error",
        url="https://example.test/cb?token=sk-live-abcdef123456",
        detail="request failed apiKey=sk-live-abcdef123456 and password: hunter2",
    )
    timeline.close()
    written = (tmp_path / "events.jsonl").read_text()
    for secret in ("sk-live-abcdef123456", "hunter2"):
        assert secret not in written
    assert "<redacted>" in entry["url"] and "<redacted>" in entry["detail"]


def test_timeline_is_bounded_and_optional(tmp_path):
    timeline = Timeline(None)
    for index in range(450):
        timeline.record("act", index=str(index))
    timeline.close()
    events = timeline.snapshot()
    assert len(events) == 400
    assert events[0]["index"] == "50" and events[-1]["index"] == "449"
    assert not (tmp_path / "events.jsonl").exists()


def test_close_is_idempotent_and_a_bad_path_does_not_raise():
    timeline = Timeline("/proc/definitely/not/writable")
    timeline.record("observe", generation=1)
    timeline.close()
    timeline.close()
    assert timeline.snapshot()[0]["generation"] == 1