"""
Lightpanda Zig-based headless browser adapter for OmniFast Browser.
Connects to Lightpanda via Chrome DevTools Protocol (CDP) on port 9222.
Lightpanda is 10x faster and consumes ~90% less RAM than full Chromium.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
import httpx

SNAP_SCRIPT = (Path(__file__).parent.parent / "snapshot.js").read_text(encoding="utf-8")


class LightpandaBrowser:
    """
    Adapter for Lightpanda (lightpanda-io/browser).
    Connects to Lightpanda's CDP endpoint (default http://127.0.0.1:9222).
    """

    def __init__(self, url: str, endpoint: str = None):
        self.endpoint = (endpoint or os.environ.get("LIGHTPANDA_ENDPOINT") or "http://127.0.0.1:9222").rstrip("/")
        self.target_id: Optional[str] = None
        self.ws_url: Optional[str] = None
        self._ensure_server()
        self._create_target(url)

    def _ensure_server(self):
        """Verifies Lightpanda CDP server is running, or attempts to spawn it."""
        try:
            r = httpx.get(f"{self.endpoint}/json/version", timeout=1.5)
            if r.status_code == 200:
                return
        except Exception:
            pass

        # Check if local lightpanda binary exists
        bin_path = os.environ.get("LIGHTPANDA_BIN") or "lightpanda"
        try:
            subprocess.Popen([bin_path, "serve", "--port", "9222"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
        except Exception:
            raise RuntimeError(
                f"Lightpanda browser is not running at {self.endpoint}.\n"
                "To start Lightpanda:\n"
                "  1. Linux / WSL2: lightpanda serve --port 9222\n"
                "  2. Docker: docker run -p 9222:9222 ghcr.io/lightpanda-io/browser:latest\n"
                "Or switch to standard Playwright browser via: export BROWSER_BACKEND=playwright"
            )

    def _create_target(self, url: str):
        """Creates a new page in Lightpanda via CDP JSON HTTP API."""
        r = httpx.put(f"{self.endpoint}/json/new?{url}", timeout=5.0)
        target_info = r.json()
        self.target_id = target_info.get("id")
        self.ws_url = target_info.get("webSocketDebuggerUrl")

    def call_cdp(self, method: str, **params) -> Dict[str, Any]:
        """Calls a CDP method on the active Lightpanda target."""
        # Lightpanda supports CDP command execution via HTTP /json or WebSocket
        payload = {"id": 1, "method": method, "params": params}
        r = httpx.post(f"{self.endpoint}/cdp/{self.target_id}", json=payload, timeout=10.0)
        return r.json().get("result", {})

    def evaluate(self, expression: str) -> Any:
        res = self.call_cdp("Runtime.evaluate", expression=expression, returnByValue=True)
        return res.get("result", {}).get("value")

    def observe(self, screenshot: bool = False) -> Dict[str, Any]:
        """Executes snapshot.js inside Lightpanda DOM and returns observed elements."""
        raw_state = self.evaluate(f"(() => {{ {SNAP_SCRIPT}; return state; }})()")
        if not raw_state:
            raw_state = {
                "url": self.evaluate("window.location.href") or "",
                "title": self.evaluate("document.title") or "",
                "text": self.evaluate("document.body.innerText") or "",
                "actions": [],
            }
        return raw_state

    def close(self):
        if self.target_id:
            try:
                httpx.get(f"{self.endpoint}/json/close/{self.target_id}", timeout=2.0)
            except Exception:
                pass
            self.target_id = None
