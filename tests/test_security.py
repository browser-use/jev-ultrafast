"""Contracts for the security layer: redaction, action audit, resource limits,
and per-task trace ids (production-review points 2, 3 and 10)."""

import json

import pytest

from jev_ultrafast import firefox
from jev_ultrafast.redact import redact
from jev_ultrafast.tools import ToolBox, ToolError

# ── redaction ────────────────────────────────────────────────────────────────


def test_redact_masks_every_known_key_format():
    # synthetic, format-matching fakes only — never real keys (GitHub push
    # protection blocks those, and rightly so)
    samples = [
        "gsk_" + "t0e5s3t" * 2,                 # 14 chars: matches our detector
        "nvapi-" + "t0e5s3t" * 3,               # dashed fake
        "sk-proj-t0e5s3t0123456789",
        "sk-or-v1-t0e5s3t0123456789",
        "sk-ant-api03-t0e5s3t0123456789",
    ]
    for sample in samples:
        out = redact(sample)
        assert sample not in out, sample[:10]
        assert out.endswith("***")


def test_redact_masks_assignments_and_bearers_but_not_plain_text():
    assert redact("GROQ_API_KEY=gsk_abcdef123456") == "GROQ_API_KEY=***"
    assert "gsk_abcdef" not in redact("error: Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.sig")
    # variable NAMES must survive (that is the whole diagnostic value)
    assert "POLICY_API_KEY is not set" == redact("POLICY_API_KEY is not set")
    assert redact("model openai/gpt-oss-20b via groq, HTTP 401 invalid key") == \
        "model openai/gpt-oss-20b via groq, HTTP 401 invalid key"


def test_provider_errors_are_redacted_before_they_ride():
    from jev_ultrafast import model

    class EchoError:
        status_code = 401
        is_error = True
        text = '{"error": "invalid key gsk_ABCDEF123456789 provided"}'

        def json(self):
            return {}

    class Client:
        def post(self, *args, **kwargs):
            return EchoError()

    original = model.CLIENT
    model.CLIENT = Client()
    try:
        with pytest.raises(RuntimeError) as raised:
            model.post_json("https://api.test/v1/chat/completions", "k", {})
        assert "gsk_ABCDEF123456789" not in str(raised.value)
        assert "gsk_***" in str(raised.value)
    finally:
        model.CLIENT = original


# ── action audit log ─────────────────────────────────────────────────────────


@pytest.fixture
def audited_box(tmp_path):
    return ToolBox(tmp_path / "ws", audit_path=tmp_path / "audit.jsonl", trace_id="trace-42")


def read_audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_every_tool_call_is_audited(tmp_path, audited_box):
    audited_box.call("write_file", {"path": "a.txt", "content": "hello"})
    with pytest.raises(ToolError):
        audited_box.call("read_file", {"path": "missing.txt"})
    entries = read_audit(tmp_path)
    assert [e["tool"] for e in entries] == ["write_file", "read_file"]
    assert entries[0]["trace"] == "trace-42" and entries[0]["ok"] is True
    assert entries[0]["duration_ms"] >= 0
    assert entries[1]["ok"] is False and "does not exist" in entries[1]["error"]


def test_unknown_tool_attempts_are_audited(tmp_path, audited_box):
    with pytest.raises(ToolError):
        audited_box.call("destroy_everything", {})
    entries = read_audit(tmp_path)
    assert entries[0]["tool"] == "destroy_everything" and entries[0]["ok"] is False


def test_run_command_audit_records_verdict_and_approval(tmp_path):
    approved = []
    box = ToolBox(
        tmp_path / "ws",
        request_approval=lambda command: approved.append(command) or True,
        audit_path=tmp_path / "audit.jsonl",
        trace_id="t1",
    )
    box.call("run_command", {"command": "echo hi"})  # allow-listed
    with pytest.raises(ToolError):
        box.call("run_command", {"command": "rm -rf /"})  # denied by policy
    box.call("run_command", {"command": "python3 -c 'print(1)'"})  # approved
    entries = read_audit(tmp_path)
    verdicts = [(e["verdict"], e["approved"], e["ok"]) for e in entries]
    assert verdicts == [("allow", True, True), ("deny", False, False), ("approve", True, True)]
    assert entries[0]["exit"] == 0
    assert "echo hi" in entries[0]["args"]


def test_audit_arguments_are_redacted(tmp_path, audited_box):
    audited_box.call("write_file", {"path": "a.txt", "content": "key gsk_ABCDEFGH12345678"})
    entry = read_audit(tmp_path)[0]
    assert "gsk_ABCDEFGH12345678" not in entry["args"]
    assert "gsk_***" in entry["args"]


def test_audit_failure_never_breaks_a_tool(tmp_path):
    # a directory where the audit file should be → unwritable
    box = ToolBox(tmp_path / "ws", audit_path=tmp_path / "blocked")
    (tmp_path / "blocked").mkdir()
    assert "Wrote" in box.call("write_file", {"path": "a.txt", "content": "x"})  # still works


# ── resource limits on approved commands ─────────────────────────────────────


@pytest.mark.skipif(not __import__("os").name == "posix", reason="preexec_fn is POSIX-only")
def test_approved_commands_run_with_cpu_memory_and_file_limits(tmp_path):
    box = ToolBox(
        tmp_path / "ws",
        request_approval=lambda command: True,
        audit_path=tmp_path / "audit.jsonl",
    )
    result = box.call("run_command", {
        "command": "python3 -c 'import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0]); "
                   "print(resource.getrlimit(resource.RLIMIT_CPU)[0])'",
    })
    assert "2147483648" in result  # 2 GB address-space cap
    assert "120" in result  # CPU seconds cap


# ── per-task trace id ────────────────────────────────────────────────────────


class MockBridge:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def broadcast(self, message):
        self.sent.append(message)


def test_task_runner_assigns_a_trace_and_carries_it_everywhere(monkeypatch, tmp_path):
    from jev_ultrafast import orchestrator

    for name in ("PLANNER_PROVIDER", "PLANNER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEV_LAYA", "off")
    monkeypatch.setattr(firefox, "RUNS_LOG", tmp_path / "runs.jsonl")

    def fake_orchestration(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        assert toolbox.trace_id is not None and len(toolbox.trace_id) == 12
        on_step({"step": 1, "tool": "list_files", "args": {}, "result": "(empty)"})
        return {"final": "ok", "steps": [], "usage": {}, "latency_ms": 1}

    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    runner = firefox.TaskRunner(MockBridge(), workspace=tmp_path / "ws")
    assert runner.current_state()["trace"] is None  # no task yet
    runner.start("research a topic and write a project", "https://example.com", 7)

    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (runner.orchestrated or {}).get("status") != "done":
        time.sleep(0.05)
    assert (runner.orchestrated or {}).get("status") == "done"

    trace = runner.current_state()["trace"]
    assert trace and len(trace) == 12
    record = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert record["trace"] == trace  # runs.jsonl ties to the same task
