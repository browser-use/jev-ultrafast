#!/usr/bin/env python3
"""Jev Ultrafast Model Context Protocol (MCP) Server.

Exposes fast browser automation powered by SemIf to Claude Desktop, Claude Code,
Codex CLI, and Antigravity via standard JSON-RPC 2.0 stdio MCP.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path so jev_ultrafast can be imported directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if present
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from jev_ultrafast import model  # noqa: E402
from jev_ultrafast.agent import Agent  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("jev-mcp")

TOOLS = [
    {
        "name": "open_browse",
        "description": (
            "Autonomously navigate and interact with web pages to achieve a specific goal using "
            "ultra-low latency semantic decisions powered by the local SemIf model. "
            "Inspects page elements, decides optimal actions (clicks, text entry, selects), "
            "executes them in Chrome, and returns complete execution history and page outcome."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Initial target URL to navigate to (e.g. 'https://www.google.com/travel/flights?hl=en').",
                },
                "goal": {
                    "type": "string",
                    "description": "Natural language task or objective to accomplish on the page.",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum number of browser action steps to execute before stopping (default: 10).",
                    "default": 10,
                },
            },
            "required": ["url", "goal"],
        },
    },
    {
        "name": "open_decide",
        "description": (
            "Evaluate a page state (observed elements, text, and history) against a goal, "
            "and instantaneously predict the optimal browser action and target element using SemIf."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "object",
                    "description": "Observed page state containing url, title, text, and indexed actions.",
                },
                "goal": {
                    "type": "string",
                    "description": "Goal or instruction to achieve.",
                },
                "history": {
                    "type": "array",
                    "description": "Recent actions executed so far.",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["page", "goal"],
        },
    },
    {
        "name": "jev_browse",
        "description": "Alias for open_browse (for backwards compatibility).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Initial target URL to navigate to.",
                },
                "goal": {
                    "type": "string",
                    "description": "Natural language task or objective to accomplish on the page.",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum number of browser action steps to execute before stopping (default: 10).",
                    "default": 10,
                },
            },
            "required": ["url", "goal"],
        },
    },
    {
        "name": "jev_decide",
        "description": "Alias for open_decide (for backwards compatibility).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "object",
                    "description": "Observed page state containing url, title, text, and indexed actions.",
                },
                "goal": {
                    "type": "string",
                    "description": "Goal or instruction to achieve.",
                },
                "history": {
                    "type": "array",
                    "description": "Recent actions executed so far.",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["page", "goal"],
        },
    },
]


def execute_browse(url: str, goal: str, max_steps: int = 10) -> dict:
    """Run the complete autonomous agent loop."""
    logger.info("Starting autonomous run for goal: %s at %s", goal, url)
    step_records = []
    final_status = "unknown"
    final_page = {}
    elapsed_total_ms = 0

    try:
        max_steps = int(max_steps)
    except (TypeError, ValueError):
        max_steps = 10

    if max_steps <= 0:
        return {
            "status": "stopped",
            "verified": False,
            "goal": goal,
            "total_steps": 0,
            "elapsed_ms": 0,
            "final_page": {},
            "history": [],
            "note": "Stopped before starting because max_steps is 0 or negative.",
        }

    try:
        with Agent(url, goal) as agent:
            iteration_count = 0
            max_iterations = max(max_steps * 2, 10)
            for snapshot in agent.run():
                iteration_count += 1
                history = snapshot.get("history", [])
                while len(step_records) < len(history):
                    if len(step_records) >= max_steps:
                        break
                    entry = history[len(step_records)]
                    step_records.append({
                        "step": entry.get("step", len(step_records) + 1),
                        "action": entry.get("action"),
                        "operation": entry.get("operation"),
                        "choice": entry.get("choice"),
                        "text": entry.get("text"),
                        "confidence": entry.get("confidence"),
                        "elapsed_ms": entry.get("elapsed_ms", snapshot.get("elapsed_ms")),
                        "backend": entry.get("backend", "semif"),
                        "model": entry.get("model"),
                        "question_spec_hash": entry.get("question_spec_hash"),
                        "decision_source": entry.get("decision_source"),
                        "provenance": entry.get("provenance"),
                    })
                final_status = snapshot.get("status")
                final_page = {
                    "url": snapshot.get("page", {}).get("url"),
                    "title": snapshot.get("page", {}).get("title"),
                    "text_snippet": (snapshot.get("page", {}).get("text") or "")[:2000],
                }
                elapsed_total_ms = snapshot.get("elapsed_ms", 0)

                if len(step_records) >= max_steps or iteration_count >= max_iterations:
                    logger.info(
                        "Reached maximum step or iteration limit (steps=%d, iterations=%d, max_steps=%d)",
                        len(step_records),
                        iteration_count,
                        max_steps,
                    )
                    if final_status not in {"done", "blocked"}:
                        final_status = "stopped"
                    break

        return {
            "status": final_status,
            "verified": False,
            "backend": "semif",
            "calibration_surface": "semif_local",
            "provenance": {
                "backend": "semif",
                "calibration_surface": "semif_local",
            },
            "outcome_verification": (
                "unverified - DONE choice by model without independent contract assertion"
                if final_status == "done"
                else "not_applicable"
            ),
            "goal": goal,
            "total_steps": len(step_records),
            "elapsed_ms": elapsed_total_ms,
            "final_page": final_page,
            "history": step_records,
        }
    except Exception as e:
        logger.exception("Error executing browse")
        return {
            "status": "error",
            "verified": False,
            "backend": "semif",
            "calibration_surface": "semif_local",
            "provenance": {
                "backend": "semif",
                "calibration_surface": "semif_local",
            },
            "error": str(e),
            "goal": goal,
            "total_steps": len(step_records),
            "history": step_records,
        }


def execute_decide(page: dict, goal: str, history: list | None = None) -> dict:
    """Decide next action using SemIf without browser execution."""
    res = model.choose(page, goal, history or [])
    res.setdefault("backend", "semif")
    res.setdefault("calibration_surface", "semif_local")
    return res


def handle_request(req: dict) -> dict | None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "jev-ultrafast",
                    "version": "0.1.0",
                },
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS,
            },
        }

    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name in {"open_browse", "jev_browse"}:
            url = arguments.get("url")
            goal = arguments.get("goal")
            raw_max = arguments.get("max_steps")
            try:
                max_steps = int(raw_max) if raw_max is not None else 10
            except (TypeError, ValueError):
                max_steps = 10

            if not url or not goal:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "Missing required arguments: url, goal"}],
                    },
                }
            res = execute_browse(url, goal, max_steps)
            is_error = res.get("status") == "error"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": is_error,
                    "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
                },
            }

        elif tool_name in {"open_decide", "jev_decide"}:
            page = arguments.get("page")
            goal = arguments.get("goal")
            history = arguments.get("history", [])
            if not page or not goal:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "Missing required arguments: page, goal"}],
                    },
                }
            try:
                res = execute_decide(page, goal, history)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Decision error: {e}"}],
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        }

    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            continue

        try:
            resp = handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            logger.exception("Error processing MCP message: %s", e)
            if isinstance(req, dict) and req.get("id") is not None:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "error": {"code": -32603, "message": f"Internal error: {e}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
