"""MCP stdio server: one goal in, one verified browser run out.

Speaks newline-delimited JSON-RPC 2.0 on stdio with the standard library only, so
installing the server adds no dependency. Everything the agent or its browser
backend prints during a run is redirected to stderr: stdout carries protocol
frames and nothing else.

Credentials stay in the project's ignored ``.env``; this server reads that file
itself because the MCP host scrubs the ambient environment it spawns children in.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "jev-ultrafast"
SERVER_VERSION = "0.1.0"
DEFAULT_RUN_TIMEOUT_SECONDS = 180.0
MAX_RUN_TIMEOUT_SECONDS = 900.0
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

BACKENDS = ("browser_harness", "ego")


class ToolError(ValueError):
    """A tool refused its arguments. Reported to the caller, never fatal."""


def default_env_path() -> Path:
    """The project's ignored ``.env``; ``JEV_ENV_FILE`` overrides it."""
    raw = (os.environ.get("JEV_ENV_FILE") or "").strip()
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: str | Path | None = None, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines without overriding anything already configured."""
    target = Path(path) if path is not None else default_env_path()
    env = os.environ if environ is None else environ
    if not target.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in target.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in env:
            continue
        loaded[key] = value
    env.update(loaded)
    return loaded


class TraceLog:
    """Append-only JSONL record of the protocol frames a run produced."""

    def __init__(self, path: str | Path | None = None):
        raw = str(path) if path is not None else (os.environ.get("JEV_MCP_TRACE_PATH") or "").strip()
        self.path = Path(raw).expanduser() if raw else None
        self.pid = os.getpid()
        self._lock = threading.Lock()
        self._sequence = 0
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, direction: str, payload: Any) -> None:
        if self.path is None:
            return
        with self._lock:
            self._sequence += 1
            entry = {
                # The file is append-only across server processes; the pid separates sessions.
                "pid": self.pid,
                "seq": self._sequence,
                "at": round(time.time(), 6),
                "direction": direction,
                "payload": payload,
            }
            with self.path.open("a") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _bounded_ms(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RUN_TIMEOUT_SECONDS
    if seconds <= 0:
        return DEFAULT_RUN_TIMEOUT_SECONDS
    return min(seconds, MAX_RUN_TIMEOUT_SECONDS)


def browser_status(arguments: dict[str, Any]) -> dict[str, Any]:
    """Report non-secret prerequisites without starting a browser or calling a model."""
    from .preflight import preflight

    backend = str(arguments.get("backend") or "browser_harness").strip().lower()
    if backend not in BACKENDS:
        raise ToolError(f"backend must be one of {', '.join(BACKENDS)}")
    load_env_file()
    status: dict[str, Any] = {"backend": backend, "env_file": str(default_env_path())}
    try:
        status["checks"] = preflight(backend)
        status["ready"] = True
    except (RuntimeError, ValueError) as error:
        status["ready"] = False
        status["detail"] = str(error)
    return status


def _step_view(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the observable shape of a step; drop typed values and usage payloads."""
    return {key: value for key, value in item.items() if key not in {"text", "usage", "text_helper"}}


def _final_view(agent: Any, last_page: dict[str, Any] | None) -> dict[str, Any]:
    """Re-read the live page instead of trusting the model's DONE choice."""
    view: dict[str, Any] = {
        "observed_url": (last_page or {}).get("url"),
        "observed_title": (last_page or {}).get("title"),
    }
    try:
        fresh = agent.browser.observe(screenshot=False)
    except BaseException as error:  # a closed or navigating page is not a failed run
        view["independent_check"] = "unavailable"
        view["independent_detail"] = type(error).__name__
        return view
    view["independent_check"] = "page_read"
    view["url"] = fresh.get("url")
    view["title"] = fresh.get("title")
    view["elements"] = len(fresh.get("actions") or [])
    view["page_changed_since_decision"] = bool(last_page) and fresh.get("fingerprint") != last_page.get("fingerprint")
    return view


def run_goal(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one natural-language goal against one URL and return a bounded trace."""
    from .agent import Agent

    url = str(arguments.get("url") or "").strip()
    goal = str(arguments.get("goal") or "").strip()
    backend = str(arguments.get("backend") or "browser_harness").strip().lower()
    record_dir = arguments.get("record_dir")
    if not url:
        raise ToolError("url is required")
    if not goal:
        raise ToolError("goal is required")
    if backend not in BACKENDS:
        raise ToolError(f"backend must be one of {', '.join(BACKENDS)}")
    load_env_file()
    budget = _bounded_ms(arguments.get("timeout_seconds"))
    started = time.perf_counter()
    status = "closed"
    last_page: dict[str, Any] | None = None
    with Agent(url, goal, backend=backend, record_dir=record_dir) as agent:
        deadline = time.monotonic() + budget
        for state in agent.run():
            last_page = state.get("page") or last_page
            if time.monotonic() >= deadline:
                status = "timeout"
                break
        else:
            status = agent.state["status"]
        final = _final_view(agent, last_page)
        history = list(agent.state["history"])
        metrics = agent.metrics.snapshot()
        backend_name = agent.backend_name
        decisions = agent.state["decisions"]
        snapshot = {
            "status": status,
            "agent_status": agent.state["status"],
            "backend": backend_name,
            "url": url,
            "goal": goal,
            "steps": len(history),
            "history": [_step_view(item) for item in history],
            "decisions": len(decisions),
            "final": final,
            "metrics": metrics,
        }
    snapshot["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    snapshot["budget_seconds"] = round(budget, 3)
    return snapshot


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "run_goal",
        "description": (
            "Drive a real browser with the Jev Ultrafast agent: give one URL and one natural-language goal. "
            "The agent observes the page, chooses an operation and an observed element, and stops on DONE, "
            "BLOCKED, or the time budget. Returns status, the executed steps, and an independent re-read of "
            "the final page. The default backend drives Chrome through Browser Harness."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page to open first."},
                "goal": {"type": "string", "description": "One natural-language goal for that page."},
                "backend": {
                    "type": "string",
                    "enum": list(BACKENDS),
                    "description": "browser_harness drives Chrome (default); ego drives a separate ego-lite runtime.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": (
                        f"Wall-clock budget for the run; default {DEFAULT_RUN_TIMEOUT_SECONDS:g}s, "
                        f"max {MAX_RUN_TIMEOUT_SECONDS:g}s."
                    ),
                },
                "record_dir": {
                    "type": "string",
                    "description": "Optional directory for metrics.json, trace.json, and screenshots.",
                },
            },
            "required": ["url", "goal"],
            "additionalProperties": False,
        },
        "handler": run_goal,
    },
    {
        "name": "browser_status",
        "description": (
            "Report whether the browser backend and credentials are configured. "
            "Starts no browser and makes no paid call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "backend": {"type": "string", "enum": list(BACKENDS), "description": "Backend to check."},
            },
            "additionalProperties": False,
        },
        "handler": browser_status,
    },
)

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def list_tools() -> list[dict[str, Any]]:
    """The advertised tool surface, without the local handler callables."""
    return [
        {"name": tool["name"], "description": tool["description"], "inputSchema": tool["inputSchema"]}
        for tool in TOOLS
    ]


def text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    """The single MCP content shape this server returns."""
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True)
    return {"isError": True, "content": [{"type": "text", "text": text}]} if is_error else {
        "content": [{"type": "text", "text": text}]
    }


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    return text_result(tool["handler"](arguments))


def handle_message(message: Any, trace: TraceLog) -> dict[str, Any] | None:
    """Map one JSON-RPC message to one response, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "invalid request"}}
    method = message.get("method")
    identifier = message.get("id")
    if identifier is None:
        return None
    if method == "initialize":
        requested = str((message.get("params") or {}).get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": identifier, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": identifier, "result": {"tools": list_tools()}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments)
        except ToolError as error:
            return {"jsonrpc": "2.0", "id": identifier, "result": text_result(str(error), is_error=True)}
        except BaseException as error:  # one failed run must not end the session
            detail = {"error": type(error).__name__, "detail": str(error)}
            trace.record("tool-error", detail)
            return {"jsonrpc": "2.0", "id": identifier, "result": text_result(detail, is_error=True)}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": METHOD_NOT_FOUND, "message": f"unknown method: {method}"},
    }


def serve(stdin: Any = None, stdout: Any = None, trace: TraceLog | None = None) -> int:
    """Read newline-delimited requests until EOF. One tool call runs at a time."""
    source = sys.stdin if stdin is None else stdin
    sink = sys.stdout if stdout is None else stdout
    log = TraceLog() if trace is None else trace
    write_lock = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        log.record("response", payload)
        with write_lock:
            sink.write(json.dumps(payload) + "\n")
            sink.flush()

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            emit({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "invalid JSON"}})
            continue
        log.record("request", message)
        # A running tool may print freely; only protocol frames may reach stdout.
        with _stdout_to_stderr():
            response = handle_message(message, log)
        if response is not None:
            emit(response)
    return 0


class _stdout_to_stderr:
    """Keep agent and backend chatter off the protocol stream."""

    def __enter__(self):
        self._saved = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *_args):
        sys.stdout = self._saved
        return False


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--print-tools":
        print(json.dumps(list_tools(), indent=2, sort_keys=True))
        return 0
    if arguments and arguments[0] == "--check":
        load_env_file()
        print(json.dumps(browser_status({}), indent=2, sort_keys=True))
        return 0
    if arguments and arguments[0] in {"-h", "--help"}:
        print(f"usage: {Path(sys.argv[0]).name} [--print-tools|--check]", file=sys.stderr)
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())