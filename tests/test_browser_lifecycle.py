"""Offline lifecycle checks; no browser process or model calls."""

from unittest.mock import Mock, call

import pytest

from jev_ultrafast import browser


@pytest.fixture
def cdp(monkeypatch):
    def respond(method, **_params):
        if method == "Target.createTarget":
            return {"targetId": "owned-tab"}
        if method == "Target.attachToTarget":
            return {"sessionId": "owned-session"}
        if method == "Runtime.evaluate":
            return {"result": {"value": "complete"}}
        return {}

    mock = Mock(side_effect=respond)
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", mock)
    return mock


@pytest.mark.parametrize("stage", [
    "Target.attachToTarget",
    "Emulation.setDeviceMetricsOverride",
    "Emulation.setFocusEmulationEnabled",
    "Page.navigate",
    "Runtime.evaluate",
])
def test_failed_initialization_closes_only_owned_target(cdp, stage):
    respond = cdp.side_effect
    failure = RuntimeError("Initialization failed")

    def fail(method, **params):
        if method == stage:
            raise failure
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(RuntimeError) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is failure
    closes = [c for c in cdp.call_args_list if c.args[0] == "Target.closeTarget"]
    assert closes == [call("Target.closeTarget", targetId="owned-tab")]
    assert cdp.call_args_list[-1] == closes[0]
    assert sum(c.args[0] == stage for c in cdp.call_args_list) == 1


def test_cleanup_failure_preserves_initialization_error(cdp):
    respond = cdp.side_effect
    failure = RuntimeError("Attach failed")

    def fail(method, **params):
        if method == "Target.attachToTarget":
            raise failure
        if method == "Target.closeTarget":
            raise TimeoutError("Close timed out")
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(RuntimeError) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is failure
    assert any("Close timed out" in note for note in caught.value.__notes__)
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1


def test_failed_creation_does_not_close_unknown_targets(cdp):
    failure = RuntimeError("Create failed")
    cdp.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        browser.Browser("https://example.test/")
    assert caught.value is failure
    cdp.assert_called_once_with("Target.createTarget", url="about:blank", background=True)


def test_successful_initialization_keeps_target_until_close(cdp):
    instance = browser.Browser("https://example.test/")
    assert instance.target == "owned-tab"
    assert instance.session == "owned-session"
    assert not any(c.args[0] == "Target.closeTarget" for c in cdp.call_args_list)
    instance.close()
    instance.close()
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1
