"""Contracts for the task orchestrator (MVP-3)."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import orchestrator, providers
from jev_ultrafast.orchestrator import route_task, run_orchestration
from jev_ultrafast.tools import ToolBox


@pytest.fixture(autouse=True)
def planner_env(monkeypatch):
    """A configured planner role; tests patch providers.chat itself."""
    for name in ("PLANNER_PROVIDER", "PLANNER_MODEL", "PLANNER_API_KEY", "PLANNER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLANNER_PROVIDER", "groq")
    monkeypatch.setenv("PLANNER_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    yield


class ScriptedChat:
    """Replies in order; records every (system, conversation) it was shown."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, provider, system, conversation, max_tokens=1024, on_delta=None):
        self.calls.append((system, conversation))
        reply = self.replies.pop(0) if self.replies else '{"final": "done"}'
        return reply, {"usage": {"input_tokens": 10, "output_tokens": 5}}


def tool_reply(tool, **args):
    return json.dumps({"tool": tool, "args": args})


# ── routing ──────────────────────────────────────────────────────────────────


def test_route_task_browser_for_plain_browsing(monkeypatch):
    monkeypatch.setenv("JEV_LAYA", "off")  # pin the keyword router for these tests
    assert route_task("Find one-way flights from Zurich to London on the airline page") == "browser"
    assert route_task("Click the login button") == "browser"


def test_route_task_orchestrated_for_tools(monkeypatch):
    monkeypatch.setenv("JEV_LAYA", "off")
    assert route_task("Download the PDF report and summarize it") == "orchestrated"
    assert route_task("Create a file notes.txt with the summary") == "orchestrated"
    assert route_task("Run the tests and fix failures") == "orchestrated"
    assert route_task("Investiga y compara precios en la web") == "orchestrated"
    assert route_task("Descarga el archivo y guardalo") == "orchestrated"


# ── the tool loop ────────────────────────────────────────────────────────────


def test_run_orchestration_executes_tools_then_finishes(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat([
        tool_reply("write_file", path="note.txt", content="hello from the orchestrator"),
        tool_reply("read_file", path="note.txt"),
        '{"final": "The file note.txt contains: hello from the orchestrator"}',
    ])
    monkeypatch.setattr(providers, "chat", script)
    steps_seen = []
    result = run_orchestration("create and verify note.txt", box, on_step=steps_seen.append)

    assert result["final"] == "The file note.txt contains: hello from the orchestrator"
    assert (tmp_path / "ws" / "note.txt").read_text() == "hello from the orchestrator"
    assert len(steps_seen) == 3
    assert steps_seen[0]["tool"] == "write_file"
    assert "Wrote" in steps_seen[0]["result"]
    assert steps_seen[-1]["final"].startswith("The file")
    assert result["usage"]["input_tokens"] == 30  # aggregated over 3 calls
    assert result["latency_ms"] >= 0
    # the loop fed the tool result back to the model
    assert "TOOL RESULT (write_file)" in script.calls[1][1]
    # the system prompt carries the tool list and the mission
    assert "AVAILABLE TOOLS" in script.calls[0][0]
    assert "- web_search(" in script.calls[0][0]
    assert "MISSION: create and verify note.txt" in script.calls[0][1]


def test_run_orchestration_recovers_from_tool_errors(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat([
        tool_reply("read_file", path="ghost.txt"),  # tool error
        '{"final": "The file was missing, so nothing to read."}',
    ])
    monkeypatch.setattr(providers, "chat", script)
    result = run_orchestration("read ghost.txt", box)
    assert "TOOL ERROR" in script.calls[1][1]
    assert result["steps"][0]["error"]
    assert result["final"] == "The file was missing, so nothing to read."


def test_run_orchestration_retries_invalid_json_then_stops(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat(["I will help you!", "Sure thing!", "Still not json"])
    monkeypatch.setattr(providers, "chat", script)
    result = run_orchestration("do something", box)
    assert "could not produce valid tool calls" in result["final"]
    assert "Invalid reply" in script.calls[1][1]  # the corrective nudge was sent


def test_run_orchestration_unknown_tool_is_corrected(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat([
        tool_reply("delete_everything"),
        '{"final": "I used a wrong tool name; nothing needed deleting."}',
    ])
    monkeypatch.setattr(providers, "chat", script)
    result = run_orchestration("noop", box)
    assert "unknown tool 'delete_everything'" in script.calls[1][1]
    assert result["final"].startswith("I used a wrong tool")


def test_run_orchestration_stops_at_max_steps(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat([tool_reply("list_files", path=".")] * 10)
    monkeypatch.setattr(providers, "chat", script)
    result = run_orchestration("loop forever", box, max_steps=3)
    assert "maximum number of steps" in result["final"]
    assert [s["tool"] for s in result["steps"][:3]] == ["list_files"] * 3
    assert result["steps"][-1]["final"] == result["final"]  # the forced-stop entry


def test_run_orchestration_includes_selected_skill_instructions(tmp_path, monkeypatch):
    box = ToolBox(tmp_path / "ws")
    script = ScriptedChat(['{"final": "done"}'])
    monkeypatch.setattr(providers, "chat", script)
    monkeypatch.setattr(orchestrator, "select_skills", lambda goal, available_tools=None: [
        {"id": "documents", "_instructions_path": None}
    ])
    monkeypatch.setattr(orchestrator, "skill_instructions", lambda selected: "SKILL: extract page by page")
    run_orchestration("analyze the pdf", box)
    assert "SKILL: extract page by page" in script.calls[0][0]


def test_tool_section_empty_box_is_still_valid(tmp_path):
    empty = Mock(spec=ToolBox)
    empty.tool_descriptions.return_value = ""
    empty.registry = {"web_search": 1}
    assert orchestrator._tool_section(empty) == "AVAILABLE TOOLS: none"
