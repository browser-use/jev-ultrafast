"""Loopback-only inspector for the Jev browser agent."""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import Agent
from .questions import MAX_STEPS

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENTS = {}
QUEUE = []  # scenarios not started yet, run one at a time to avoid contention on shared Chrome
CURRENT_GOAL = ""
CURRENT_RECORD = False

REAL_WEB = {
    "flights": "https://www.google.com/travel/flights?hl=en",
    "booking": "https://www.booking.com/flights/index.html",
    "expedia": "https://www.expedia.com/Flights",
}
SCENARIOS = {*REAL_WEB, "travel", "research"}


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def response_state():
    return {
        "sites": {scenario: agent.snapshot() for scenario, agent in AGENTS.items()},
        "queued": list(QUEUE),
        "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "max_steps": MAX_STEPS,
    }


def close_all():
    global AGENTS, QUEUE
    for agent in AGENTS.values():
        agent.close()
    AGENTS = {}
    QUEUE = []


def start_next():
    """Open the next queued site's own tab. Only one site opens/works at a time,
    since a shared Chrome instance gets measurably less reliable under concurrent load."""
    global QUEUE
    if not QUEUE:
        return
    scenario = QUEUE[0]
    # Only drop it from the queue once its tab actually opened; a transient CDP failure here
    # (e.g. "No target with given id found") should leave it queued for the next retry, not
    # silently disappear.
    agent = Agent(
        REAL_WEB[scenario] if scenario in REAL_WEB else f"{ORIGIN}/fixture.html?scenario={scenario}",
        CURRENT_GOAL,
        screenshots=True,
        record_dir=Path.cwd() / "artifacts" / "frames" / scenario if CURRENT_RECORD else None,
    )
    QUEUE = QUEUE[1:]
    agent.state["scenario"] = scenario
    AGENTS[scenario] = agent


def advance_queue_if_ready():
    if QUEUE and AGENTS and all(a.state["status"] in {"done", "blocked"} for a in AGENTS.values()):
        start_next()


def command(name, body):
    global CURRENT_GOAL, CURRENT_RECORD, QUEUE
    if name == "reset":
        scenarios = list(dict.fromkeys(body.get("scenarios") or []))
        if not scenarios or any(scenario not in SCENARIOS for scenario in scenarios):
            raise ValueError("Pick at least one known site")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        close_all()
        CURRENT_GOAL = goal
        CURRENT_RECORD = bool(body.get("record"))
        QUEUE = scenarios
        try:
            start_next()
        except RuntimeError as error:
            # The item stays queued; surface the error so the UI can display it.
            result = response_state()
            result["errors"] = {"setup": str(error)}
            return result
        return response_state()
    if name == "tick_all":
        errors = {}
        for scenario, agent in list(AGENTS.items()):
            if agent.state["status"] in {"done", "blocked"}:
                continue
            try:
                agent.command("tick", {})
            except Exception as error:  # one site's failure must not stop the others
                errors[scenario] = str(error)
        advance_queue_if_ready()
        result = response_state()
        if errors:
            result["errors"] = errors
        return result
    scenario = body.get("scenario")
    if scenario not in AGENTS:
        raise ValueError("Start a demo first")
    AGENTS[scenario].command(name, body)
    advance_queue_if_ready()
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text().replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    atexit.register(close_all)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jev Ultrafast: {ORIGIN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
