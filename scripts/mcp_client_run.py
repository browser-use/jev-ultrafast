"""Talk to the Jev Ultrafast MCP server the way a host does, and keep the frames.

This is a minimal MCP stdio client: it spawns the exact command the DSH profile
registers, performs the initialize handshake, lists tools, calls one tool, and
prints every frame. ``--trace`` is handed to the server as ``JEV_MCP_TRACE_PATH``,
so the JSONL on disk is the server's own record of the session.

    # configuration only: no browser, no paid call
    python scripts/mcp_client_run.py --check

    # live run through the MCP tool (paid APIs, real Chrome)
    python scripts/mcp_client_run.py \
      --url https://www.google.com \
      --goal "Search Google for 'deepseek harness' ..." \
      --trace artifacts/mcp-trace.jsonl --record-dir artifacts/mcp-run

Live runs make paid API calls; ``--check`` does not.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_VERSION = "2025-06-18"
TOOL_TIMEOUT_SECONDS = 900.0


class Client:
    """One stdio MCP session: newline-delimited JSON-RPC in, same out."""

    def __init__(self, *, trace: Path | None = None, echo: bool = True):
        env = dict(os.environ)
        if trace is not None:
            env["JEV_MCP_TRACE_PATH"] = str(trace)
        self.echo = echo
        self.process = subprocess.Popen(
            [sys.executable, "-m", "jev_ultrafast.mcp_server"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.frames: list[dict] = []
        self.stderr: list[str] = []
        self._lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._identifier = 0

    def _pump_stdout(self) -> None:
        for line in self.process.stdout or ():
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        for line in self.process.stderr or ():
            self.stderr.append(line.rstrip())

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self._identifier += 1
            message["id"] = self._identifier
        payload = json.dumps(message)
        self.frames.append({"direction": "request", "payload": message})
        if self.echo:
            print(f"--> {payload}", flush=True)
        assert self.process.stdin is not None
        self.process.stdin.write(payload + "\n")
        self.process.stdin.flush()
        if notify:
            return {}
        return self.await_response(timeout=TOOL_TIMEOUT_SECONDS)

    def await_response(self, *, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response after {timeout:.0f}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"no response after {timeout:.0f}s") from None
            if line is None:
                raise RuntimeError("server closed stdout")
            line = line.strip()
            if not line:
                continue
            response = json.loads(line)
            self.frames.append({"direction": "response", "payload": response})
            if self.echo:
                print(f"<-- {line}", flush=True)
            return response

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()


def tool_text(response: dict) -> dict:
    """Parse the JSON payload a tool wrapped in MCP text content."""
    result = response.get("result") or {}
    blocks = result.get("content") or []
    text = blocks[0].get("text") if blocks else ""
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"text": text, "is_error": bool(result.get("isError"))}


def build_goal(term: str) -> str:
    return (
        f"Search Google for {term!r} and stop when the search results for that query are visible. "
        "Do not open any result."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only call browser_status; no browser, no paid call")
    parser.add_argument("--url", default="https://www.google.com/", help="page to open first")
    parser.add_argument("--goal", default=None, help="natural-language goal; defaults to a Google search")
    parser.add_argument("--term", default="deepseek harness", help="search term when --goal is omitted")
    parser.add_argument("--backend", default="browser_harness", choices=["browser_harness", "ego"])
    parser.add_argument("--run-timeout-seconds", type=float, default=180.0, help="budget handed to the tool")
    parser.add_argument("--client-timeout-seconds", type=float, default=TOOL_TIMEOUT_SECONDS)
    parser.add_argument("--trace", type=Path, default=None, help="JSONL trace written by the server")
    parser.add_argument("--record-dir", type=Path, default=None,
                        help="artifacts: metrics.json, trace.json, screenshots")
    parser.add_argument("--quiet", action="store_true", help="do not echo frames")
    arguments = parser.parse_args(argv)
    if arguments.trace is not None:
        arguments.trace.parent.mkdir(parents=True, exist_ok=True)

    client = Client(trace=arguments.trace, echo=not arguments.quiet)
    try:
        handshake = client.send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-client-run", "version": "0.1.0"},
        })
        client.send("notifications/initialized", notify=True)
        tools = client.send("tools/list")
        names = [tool["name"] for tool in (tools.get("result") or {}).get("tools", [])]

        if arguments.check:
            call = client.send("tools/call", {"name": "browser_status", "arguments": {"backend": arguments.backend}})
        else:
            goal = arguments.goal or build_goal(arguments.term)
            payload: dict = {
                "url": arguments.url,
                "goal": goal,
                "backend": arguments.backend,
                "timeout_seconds": arguments.run_timeout_seconds,
            }
            if arguments.record_dir is not None:
                arguments.record_dir.mkdir(parents=True, exist_ok=True)
                payload["record_dir"] = str(arguments.record_dir)
            call = client.send("tools/call", {"name": "run_goal", "arguments": payload},
                               )

        summary = {
            "protocol_version": (handshake.get("result") or {}).get("protocolVersion"),
            "server_info": (handshake.get("result") or {}).get("serverInfo"),
            "tools": names,
            "tool_result": tool_text(call),
            "trace": str(arguments.trace) if arguments.trace else None,
            "server_stderr_tail": client.stderr[-5:],
        }
        print("\n=== MCP session summary ===")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if not (call.get("result") or {}).get("isError") else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())