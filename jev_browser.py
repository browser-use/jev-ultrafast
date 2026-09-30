"""Launcher: ensure a debuggable Edge instance, point browser-harness at it, run the jev demo.

Leaving the demo (Ctrl+C) also closes the debug instance -- but only the one this launcher started.

Usage:
  uv run python jev_browser.py            # start the demo server (same as `uv run jev`)
  uv run python jev_browser.py exec ...   # run any command with BU_CDP_WS set
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 9223
DEMO_PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
PROXY = os.environ.get("JEV_PROXY", "http://127.0.0.1:7897")
PROFILE = Path(__file__).parent / ".edge-profile"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def port_busy(port):
    # An unlisted port on this host times out instead of refusing, so anything but 0 means free.
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def version_info():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
            return json.load(r)
    except Exception:
        return None


def ensure_browser():
    info = version_info()
    if info:
        # An instance this launcher did not start is left up on purpose: whoever started it owns its shutdown.
        return info, None
    proc = subprocess.Popen(
        [EDGE, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "--disable-gpu",
         f"--proxy-server={PROXY}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(15):
        time.sleep(1)
        if (info := version_info()):
            return info, proc
    sys.exit(f"Edge debug instance on port {PORT} did not come up")


def close_instance(url):
    """Ask Edge to exit itself, so it flushes .edge-profile instead of dying mid-write."""
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    try:
        with connect(url, max_size=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
    except (OSError, WebSocketException):
        # The socket goes down with the browser; whether it worked is judged by the port, not here.
        pass


def leave(info, proc):
    if proc is None:
        print(f"Left the Edge instance on port {PORT} up; this launcher did not start it.", flush=True)
        return
    close_instance(info["webSocketDebuggerUrl"])
    for _ in range(10):
        time.sleep(0.5)
        if not port_busy(PORT):
            print(f"Edge debug instance on port {PORT} closed.", flush=True)
            return
    # Still listening means the polite request never landed, so stop the process we started ourselves.
    proc.terminate()
    print(f"Edge still held port {PORT}, sent terminate() to the instance this launcher started.", flush=True)


def main():
    info, proc = ensure_browser()
    os.environ["BU_CDP_WS"] = info["webSocketDebuggerUrl"]
    # The model leg needs the same egress as the browser; setting it here keeps a bare restart working.
    os.environ.setdefault("HTTPS_PROXY", PROXY)
    os.environ.setdefault("HTTP_PROXY", PROXY)
    print(f"BU_CDP_WS={os.environ['BU_CDP_WS']}")
    if len(sys.argv) > 1 and sys.argv[1] == "exec":
        sys.exit(subprocess.run(sys.argv[2:], env=os.environ).returncode)
    try:
        if port_busy(DEMO_PORT):
            sys.exit(
                f"Port {DEMO_PORT} already serves a demo panel. Each process mints its own token, so a "
                "second one makes the panel fail at random -- stop the first instead."
            )
        from jev_ultrafast.demo import main as demo_main
        demo_main()
    finally:
        leave(info, proc)


if __name__ == "__main__":
    main()
