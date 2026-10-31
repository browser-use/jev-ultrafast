"""Mock-only tests for the Browser Use managed-browser integration."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jev_ultrafast import cloud


def fake_client(*, session_id="browser-id", cdp_url="wss://browser.example/devtools"):
    client = Mock()
    client.browsers.create.return_value = SimpleNamespace(id=session_id, cdp_url=cdp_url)
    return client


def run(client, serve=None, ensure=None, stop=None):
    cloud.run_managed_demo(
        client=client,
        serve=serve or Mock(),
        ensure_daemon_fn=ensure or Mock(),
        stop_daemon_fn=stop or Mock(),
    )


def test_managed_browser_connects_jev_and_always_stops():
    client, serve, ensure, stop = fake_client(), Mock(), Mock(), Mock()

    run(client, serve, ensure, stop)

    client.browsers.create.assert_called_once_with(proxy_country_code=None, timeout=15)
    ensure.assert_called_once_with(
        name="jev-cloud",
        env={"BU_CDP_WS": "wss://browser.example/devtools"},
    )
    serve.assert_called_once_with()
    stop.assert_called_once_with("jev-cloud", require_clean=True)
    client.browsers.stop.assert_called_once_with("browser-id")


def test_ambiguous_create_is_not_retried_or_stopped():
    client = fake_client()
    client.browsers.create.side_effect = TimeoutError("unknown create outcome")

    with pytest.raises(TimeoutError, match="unknown create outcome"):
        run(client)

    assert client.browsers.create.call_count == 1
    client.browsers.stop.assert_not_called()


def test_abandoned_demo_stops_daemon_and_browser():
    client, stop = fake_client(), Mock()

    with pytest.raises(KeyboardInterrupt):
        run(client, serve=Mock(side_effect=KeyboardInterrupt), stop=stop)

    stop.assert_called_once_with("jev-cloud", require_clean=True)
    client.browsers.stop.assert_called_once_with("browser-id")


def test_missing_cdp_url_stops_created_browser():
    client = fake_client(cdp_url=None)

    with pytest.raises(cloud.CloudBrowserError, match="CDP URL"):
        run(client)

    client.browsers.stop.assert_called_once_with("browser-id")


def test_cleanup_attempts_browser_stop_even_if_daemon_stop_fails():
    client = fake_client()

    with pytest.raises(cloud.CloudBrowserError, match="cleanup failed"):
        run(client, stop=Mock(side_effect=RuntimeError("daemon stuck")))

    client.browsers.stop.assert_called_once_with("browser-id")


def test_server_key_is_required_before_client_creation(monkeypatch):
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    constructor = Mock()
    monkeypatch.setattr(cloud, "BrowserUse", constructor)

    with pytest.raises(cloud.CloudBrowserError, match="BROWSER_USE_API_KEY"):
        cloud.run_managed_demo()

    constructor.assert_not_called()
