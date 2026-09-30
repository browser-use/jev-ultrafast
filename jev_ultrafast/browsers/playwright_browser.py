"""
Playwright Chromium browser adapter for OmniFast Browser.
Runs zero-friction headless or headful browser automation without Chrome remote debugging popups.
"""

import base64
import os
import time
from pathlib import Path
from typing import Any, Dict
from playwright.sync_api import sync_playwright

SNAP_SCRIPT = (Path(__file__).parent.parent / "snapshot.js").read_text(encoding="utf-8")


class PlaywrightBrowser:
    """Standard Playwright Chromium browser adapter."""

    def __init__(self, url: str, headless: bool = True):
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=headless if os.environ.get("HEADLESS", "1") == "1" else False,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        self.page = self.browser.new_page(viewport={"width": 1120, "height": 780})
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(0.5)

    def observe(self, screenshot: bool = False) -> Dict[str, Any]:
        """Injects snapshot.js and extracts actionable DOM nodes."""
        raw = self.page.evaluate(f"() => {{ {SNAP_SCRIPT}; return state; }}")
        if not raw:
            # Fallback observation
            elements = self.page.eval_on_selector_all(
                "a, button, input, select, textarea",
                """nodes => nodes.slice(0, 30).map((n, i) => ({
                    id: 'act_' + (i + 1),
                    kind: n.tagName === 'INPUT' || n.tagName === 'TEXTAREA' ? 'fill' : 'click',
                    label: (n.innerText || n.getAttribute('placeholder') || n.getAttribute('aria-label') || n.value || 'Link').trim().slice(0, 40),
                    node: i + 1,
                    role: n.tagName.toLowerCase(),
                    value: n.value || ''
                })).filter(e => e.label.length > 0)"""
            )
            raw = {
                "url": self.page.url,
                "title": self.page.title(),
                "text": self.page.inner_text("body")[:3000],
                "actions": elements
            }

        if screenshot:
            buf = self.page.screenshot(type="jpeg", quality=60)
            raw["screenshot"] = base64.b64encode(buf).decode("utf-8")
        return raw

    def evaluate(self, expr: str) -> Any:
        return self.page.evaluate(expr)

    def close(self):
        try:
            self.browser.close()
            self._playwright.stop()
        except Exception:
            pass
