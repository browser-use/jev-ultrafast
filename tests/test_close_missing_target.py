"""Closing an already-discarded Chrome tab must not block Start demo. No paid APIs."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser, demo

MISSING = {"code": -32602, "message": "No target with given id found"}


def test_close_ignores_missing_chrome_target(monkeypatch):
    cdp = Mock(side_effect=RuntimeError(MISSING))
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.target = "gone-tab"
    owned.close()
    assert owned.target is None
    cdp.assert_called_once_with("Target.closeTarget", targetId="gone-tab")
    owned.close()
    cdp.assert_called_once()


def test_close_still_raises_other_cdp_errors(monkeypatch):
    cdp = Mock(side_effect=RuntimeError("Close timed out"))
    monkeypatch.setattr(browser, "cdp", cdp)
    owned = browser.Browser.__new__(browser.Browser)
    owned.target = "owned-tab"
    with pytest.raises(RuntimeError, match="Close timed out"):
        owned.close()
    assert owned.target is None


def test_inspector_reset_clears_agent_when_previous_tab_is_gone():
    class FakeAgent:
        def close(self):
            raise RuntimeError(MISSING)

    demo.AGENT = FakeAgent()
    try:
        demo.close_browser()
        assert demo.AGENT is None
    finally:
        demo.AGENT = None


def test_missing_target_helper_reads_cdp_error_payload():
    assert browser.missing_chrome_target(RuntimeError(MISSING))
    assert not browser.missing_chrome_target(RuntimeError("Close timed out"))
