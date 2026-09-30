"""
Pluggable Browser Engine Factory for OmniFast Browser.
Supports:
1. Playwright Chromium (Standard / Headless & Headful / Cross-platform)
2. Lightpanda (Zig-based / 10x Faster / 90% Less RAM / Pure Headless)
3. Browser Harness (CDP daemon / Chrome Extension connection)
"""

import os
from typing import Optional

from .lightpanda import LightpandaBrowser
from .playwright_browser import PlaywrightBrowser


def get_browser(url: str, backend: Optional[str] = None):
    chosen = (backend or os.environ.get("BROWSER_BACKEND", "")).lower().strip()

    if chosen in ("lightpanda", "panda", "zig"):
        return LightpandaBrowser(url)
    elif chosen in ("harness", "cdp"):
        from ..browser import Browser
        return Browser(url)
    else:
        # Default to robust Playwright
        return PlaywrightBrowser(url)
