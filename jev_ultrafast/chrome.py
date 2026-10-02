"""Start the dedicated automation Chrome that BU_CDP_URL points at."""

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_PROFILE = Path.home() / ".config" / "browser-harness" / "chrome"
WINDOWS_PATHS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Google\Chrome Beta\Application\chrome.exe",
    r"Chromium\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)
MAC_APPS = ("Google Chrome", "Chromium", "Brave Browser", "Microsoft Edge")
POSIX_COMMANDS = ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def binary():
    """A Chromium-family executable, honouring the overrides browser-harness accepts."""
    for key in ("BH_CHROME_PATH", "CHROME_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).expanduser().is_file():
            return Path(raw).expanduser()
    if sys.platform == "win32":
        roots = (os.environ.get(name) for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"))
        for root in filter(None, roots):
            for relative in WINDOWS_PATHS:
                if (path := Path(root) / relative).is_file():
                    return path
    elif sys.platform == "darwin":
        for app in MAC_APPS:
            if (path := Path("/Applications") / f"{app}.app/Contents/MacOS" / app).is_file():
                return path
    else:
        for command in POSIX_COMMANDS:
            if path := shutil.which(command):
                return Path(path)
    return None


def responds(host, port, timeout=1.0):
    """True when something answers the DevTools endpoint. host is URL-safe, IPv6 already bracketed."""
    try:
        urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=timeout).close()
        return True
    except urllib.error.HTTPError as error:
        return error.code == 403
    except Exception:
        return False


def detach():
    """Popen flags so Chrome outlives this process and this terminal.

    CREATE_NEW_PROCESS_GROUP keeps a Ctrl-C in our terminal from reaching Chrome.
    DETACHED_PROCESS is deliberately omitted: it overrides CREATE_NO_WINDOW on Windows.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def launch(argv):
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach())


def ensure_automation_chrome(start_url=None):
    """Bring up the dedicated Chrome behind a local BU_CDP_URL, once. Returns a line to log.

    Never blocks on readiness: browser-harness already waits 30s for BU_CDP_URL to answer,
    which is also why a cold start here costs nothing but the fork.
    """
    raw = (os.environ.get("BU_CDP_URL") or "").strip()
    if not raw:
        return "not configured — set BU_CDP_URL to run a dedicated automation browser"
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK or not parsed.port:
        return f"{raw} is not a local http endpoint; leaving browser startup alone"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if responds(host, parsed.port):
        if start_url:
            ensure_daemon()
            cdp("Target.createTarget", url=start_url)
        return f"already listening on {host}:{parsed.port}"
    executable = binary()
    if executable is None:
        return f"nothing on {host}:{parsed.port} and no Chrome/Chromium found; start one manually"
    profile = Path(os.environ.get("JEV_CHROME_PROFILE") or DEFAULT_PROFILE).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    # A non-default profile sidesteps the default-profile lockdown that disables /json/*
    # discovery on current Chrome, which is what makes --remote-debugging-port usable here.
    arguments = [
        str(executable),
        f"--remote-debugging-port={parsed.port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if start_url:
        arguments.append(start_url)
    launch(arguments)
    return f"started {executable.name} on {host}:{parsed.port} (profile {profile})"
