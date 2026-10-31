"""Run the existing Jev loop in a Browser Use managed browser."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from browser_use_sdk.v4 import BrowserUse

CLOUD_DAEMON_NAME = "jev-cloud"
MAX_BROWSER_MINUTES = 15


class CloudBrowserError(RuntimeError):
    """A managed browser could not be started or cleaned up safely."""


def _load_environment() -> None:
    for name in (".env.local", ".env"):
        try:
            lines = open(name, encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        with lines:
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


def _default_daemon_functions() -> tuple[Callable[..., Any], Callable[..., Any]]:
    from browser_harness.admin import ensure_daemon, restart_daemon

    return ensure_daemon, restart_daemon


def _serve_demo() -> None:
    from . import demo

    try:
        demo.main()
    finally:
        demo.close_browser()


def run_managed_demo(
    *,
    client: Any | None = None,
    serve: Callable[[], Any] | None = None,
    ensure_daemon_fn: Callable[..., Any] | None = None,
    stop_daemon_fn: Callable[..., Any] | None = None,
) -> None:
    """Create one managed browser, run Jev against its CDP URL, then stop it.

    Browser creation is attempted exactly once because a transport failure can
    be ambiguous. Browser Use is always asked to enforce a 15-minute lifetime,
    and every browser with a known session id is stopped during cleanup.
    """

    owned_client = client is None
    if owned_client:
        api_key = os.environ.get("BROWSER_USE_API_KEY")
        if not api_key:
            raise CloudBrowserError("BROWSER_USE_API_KEY is required on the server")
        client = BrowserUse(api_key=api_key)

    session = None
    daemon_started = False
    previous = {name: os.environ.get(name) for name in ("BU_NAME", "BU_CDP_WS")}
    try:
        # Never retry this create. A timeout does not prove the server rejected it.
        session = client.browsers.create(
            proxy_country_code=None,
            timeout=MAX_BROWSER_MINUTES,
        )
        session_id = getattr(session, "id", None)
        cdp_url = getattr(session, "cdp_url", None)
        if not session_id:
            raise CloudBrowserError("Browser Use created a browser without a session id")
        if not isinstance(cdp_url, str) or not cdp_url.strip():
            raise CloudBrowserError("Browser Use created a browser without a CDP URL")

        os.environ["BU_NAME"] = CLOUD_DAEMON_NAME
        os.environ["BU_CDP_WS"] = cdp_url
        if ensure_daemon_fn is None or stop_daemon_fn is None:
            default_ensure, default_stop = _default_daemon_functions()
            ensure_daemon_fn = ensure_daemon_fn or default_ensure
            stop_daemon_fn = stop_daemon_fn or default_stop
        ensure_daemon_fn(name=CLOUD_DAEMON_NAME, env={"BU_CDP_WS": cdp_url})
        daemon_started = True
        (serve or _serve_demo)()
    finally:
        cleanup_errors = []
        if daemon_started:
            try:
                stop_daemon_fn(CLOUD_DAEMON_NAME, require_clean=True)
            except Exception as error:
                cleanup_errors.append(error)
        session_id = getattr(session, "id", None)
        if session_id:
            try:
                client.browsers.stop(session_id)
            except Exception as error:
                cleanup_errors.append(error)
        if owned_client:
            client.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if cleanup_errors:
            raise CloudBrowserError("Managed browser cleanup failed") from cleanup_errors[0]


def main() -> None:
    _load_environment()
    run_managed_demo()


if __name__ == "__main__":
    main()
