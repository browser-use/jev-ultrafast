"""Offline contracts for the MCP stdio server. No browser, no paid APIs."""

import io
import json
import os
from pathlib import Path

import pytest

from jev_ultrafast import mcp_server


def frames(stdout: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def serve(messages: list[dict], tmp_path: Path | None = None) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    stdout = io.StringIO()
    trace = mcp_server.TraceLog(tmp_path / "nested" / "trace.jsonl") if tmp_path else mcp_server.TraceLog("")
    assert mcp_server.serve(stdin=stdin, stdout=stdout, trace=trace) == 0
    return frames(stdout)


def request(identifier, method, params=None):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_initialize_negotiates_a_supported_protocol_version():
    [response] = serve([request(1, "initialize", {"protocolVersion": "2024-11-05"})])
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "jev-ultrafast"
    assert "tools" in response["result"]["capabilities"]


def test_initialize_falls_back_on_an_unknown_protocol_version():
    [response] = serve([request(1, "initialize", {"protocolVersion": "1999-01-01"})])
    assert response["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION


def test_tools_list_advertises_schemas_without_local_handlers():
    [response] = serve([request(2, "tools/list")])
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert set(tools) == {"run_goal", "browser_status"}
    assert "handler" not in tools["run_goal"]
    assert tools["run_goal"]["inputSchema"]["required"] == ["url", "goal"]
    assert tools["run_goal"]["inputSchema"]["properties"]["backend"]["enum"] == ["browser_harness", "ego"]


def test_notifications_get_no_response():
    assert serve([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) == []


def test_unknown_method_is_a_jsonrpc_error():
    [response] = serve([request(3, "resources/list")])
    assert response["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_invalid_json_is_a_parse_error():
    stdin = io.StringIO("{not json\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout, trace=mcp_server.TraceLog(""))
    [response] = frames(stdout)
    assert response["error"]["code"] == mcp_server.PARSE_ERROR


def test_missing_arguments_are_reported_without_starting_a_browser():
    [response] = serve([request(4, "tools/call", {"name": "run_goal", "arguments": {"url": "https://example.test"}})])
    assert response["result"]["isError"] is True
    assert "goal is required" in response["result"]["content"][0]["text"]


def test_unknown_tool_is_reported_as_a_tool_error():
    [response] = serve([request(5, "tools/call", {"name": "drive_everything", "arguments": {}})])
    assert response["result"]["isError"] is True
    assert "unknown tool" in response["result"]["content"][0]["text"]


def test_browser_status_reports_configuration_without_paid_calls(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    [response] = serve([request(6, "tools/call", {"name": "browser_status", "arguments": {}})])
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["backend"] == "browser_harness"
    assert payload["ready"] is True
    assert payload["checks"]["jev_api"] == "configured"


def test_browser_status_rejects_an_unknown_backend(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    [response] = serve([request(7, "tools/call", {"name": "browser_status", "arguments": {"backend": "firefox"}})])
    assert response["result"]["isError"] is True
    assert "backend must be one of" in response["result"]["content"][0]["text"]


def test_stdout_stays_protocol_only_when_a_tool_prints():
    def noisy(_arguments):
        print("browser-harness: chatter that must never reach stdout")
        return {"ok": True}

    original = dict(mcp_server.TOOLS_BY_NAME["browser_status"])
    mcp_server.TOOLS_BY_NAME["browser_status"] = {**original, "handler": noisy}
    try:
        [response] = serve([request(8, "tools/call", {"name": "browser_status", "arguments": {}})])
    finally:
        mcp_server.TOOLS_BY_NAME["browser_status"] = original
    assert json.loads(response["result"]["content"][0]["text"]) == {"ok": True}


def test_trace_log_records_both_directions(tmp_path):
    path = tmp_path / "nested" / "trace.jsonl"
    serve([request(9, "tools/list")], tmp_path)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [entry["direction"] for entry in entries] == ["request", "response"]
    assert [entry["seq"] for entry in entries] == [1, 2]
    assert entries[0]["pid"] == entries[1]["pid"] == os.getpid()
    assert entries[0]["payload"]["method"] == "tools/list"


def test_trace_log_is_optional():
    log = mcp_server.TraceLog("")
    log.record("request", {"method": "tools/list"})
    assert log.path is None


def test_env_file_loads_missing_keys_without_overriding(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nTYPESAFE_API_KEY="from-file"\nTEXT_MODEL=from-file\nexport EXTRA=1\n\nmalformed\n')
    # The mapping passed in is the one being populated: existing keys always win.
    environ = {"TEXT_MODEL": "from-environment"}
    loaded = mcp_server.load_env_file(env_file, environ)
    assert loaded == {"TYPESAFE_API_KEY": "from-file", "EXTRA": "1"}
    assert environ["TEXT_MODEL"] == "from-environment"
    assert environ["TYPESAFE_API_KEY"] == "from-file"


def test_env_file_is_optional(tmp_path):
    assert mcp_server.load_env_file(tmp_path / "missing.env", {}) == {}


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, mcp_server.DEFAULT_RUN_TIMEOUT_SECONDS), ("nonsense", mcp_server.DEFAULT_RUN_TIMEOUT_SECONDS),
     (0, mcp_server.DEFAULT_RUN_TIMEOUT_SECONDS), (-5, mcp_server.DEFAULT_RUN_TIMEOUT_SECONDS),
     (30, 30.0), (10_000, mcp_server.MAX_RUN_TIMEOUT_SECONDS)],
)
def test_run_budget_is_bounded(given, expected):
    assert mcp_server._bounded_ms(given) == expected


def test_step_view_drops_typed_values_and_usage():
    step = {"kind": "fill", "text": "secret query", "usage": {"tokens": 1}, "text_helper": "m", "action": "Search"}
    assert mcp_server._step_view(step) == {"kind": "fill", "action": "Search"}