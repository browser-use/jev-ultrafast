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
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failed_initialization_closes_only_owned_target(cdp, stage, error_type):
    respond = cdp.side_effect
    failure = error_type("Initialization failed")

    def fail(method, **params):
        if method == stage:
            raise failure
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(error_type) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is failure
    closes = [c for c in cdp.call_args_list if c.args[0] == "Target.closeTarget"]
    assert closes == [call("Target.closeTarget", targetId="owned-tab")]
    assert cdp.call_args_list[-1] == closes[0]
    assert sum(c.args[0] == stage for c in cdp.call_args_list) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_cleanup_failure_preserves_initialization_error(cdp, error_type):
    respond = cdp.side_effect
    failure = error_type("Attach failed")

    def fail(method, **params):
        if method == "Target.attachToTarget":
            raise failure
        if method == "Target.closeTarget":
            raise TimeoutError("Close timed out")
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(BaseException) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is failure
    assert any("Close timed out" in note for note in caught.value.__notes__)
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_fresh_cleanup_interrupt_propagates_with_initialization_context(cdp, error_type, interrupt_type):
    respond = cdp.side_effect
    failure = error_type("Attach failed")
    interrupt = interrupt_type(73) if interrupt_type is SystemExit else interrupt_type("Cleanup interrupted")

    def fail(method, **params):
        if method == "Target.attachToTarget":
            raise failure
        if method == "Target.closeTarget":
            raise interrupt
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(BaseException) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is interrupt
    assert interrupt.__context__ is failure
    assert not interrupt.__suppress_context__
    if interrupt_type is SystemExit:
        assert interrupt.code == 73
    closes = [c for c in cdp.call_args_list if c.args[0] == "Target.closeTarget"]
    assert closes == [call("Target.closeTarget", targetId="owned-tab")]
    assert cdp.call_args_list[-1] == closes[0]


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_cleanup_interrupt_escapes_callers_retrying_ordinary_errors(cdp, interrupt_type):
    respond = cdp.side_effect
    failure = RuntimeError("Attach failed")
    interrupt = interrupt_type("Cleanup interrupted")

    def fail(method, **params):
        if method == "Target.attachToTarget":
            raise failure
        if method == "Target.closeTarget":
            raise interrupt
        return respond(method, **params)

    cdp.side_effect = fail
    with pytest.raises(interrupt_type) as caught:
        for _attempt in range(2):
            try:
                browser.Browser("https://example.test/")
            except Exception:
                continue
            break

    assert caught.value is interrupt
    assert interrupt.__context__ is failure
    assert cdp.call_args_list.count(call("Target.createTarget", url="about:blank", background=True)) == 1
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failed_creation_does_not_close_unknown_targets(cdp, error_type):
    failure = error_type("Create failed")
    cdp.side_effect = failure
    with pytest.raises(error_type) as caught:
        browser.Browser("https://example.test/")
    assert caught.value is failure
    cdp.assert_called_once_with("Target.createTarget", url="about:blank", background=True)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_interrupt_during_readiness_sleep_closes_owned_target(cdp, monkeypatch, error_type):
    respond = cdp.side_effect
    failure = error_type("Readiness wait interrupted")

    def loading(method, **params):
        if method == "Runtime.evaluate":
            return {"result": {"value": "loading"}}
        return respond(method, **params)

    def interrupt(_delay):
        raise failure

    cdp.side_effect = loading
    monkeypatch.setattr(browser.time, "sleep", interrupt)
    with pytest.raises(error_type) as caught:
        browser.Browser("https://example.test/")

    assert caught.value is failure
    closes = [c for c in cdp.call_args_list if c.args[0] == "Target.closeTarget"]
    assert closes == [call("Target.closeTarget", targetId="owned-tab")]
    assert cdp.call_args_list[-1] == closes[0]


def test_successful_initialization_keeps_target_until_close(cdp):
    instance = browser.Browser("https://example.test/")
    assert instance.target == "owned-tab"
    assert instance.session == "owned-session"
    assert not any(c.args[0] == "Target.closeTarget" for c in cdp.call_args_list)
    instance.close()
    instance.close()
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1


@pytest.mark.parametrize("error_text", ["net::ERR_NAME_NOT_RESOLVED", "net::ERR_BLOCKED_BY_CLIENT", ""])
def test_navigation_error_response_closes_owned_target(cdp, error_text):
    respond = cdp.side_effect

    def navigation_error(method, **params):
        if method == "Page.navigate":
            return {"frameId": "owned-frame", "errorText": error_text}
        return respond(method, **params)

    cdp.side_effect = navigation_error
    with pytest.raises(RuntimeError, match="Navigation failed") as caught:
        browser.Browser("https://example.invalid/")

    assert error_text in str(caught.value)
    assert cdp.call_args_list.count(call("Target.closeTarget", targetId="owned-tab")) == 1
    assert cdp.call_args_list[-1] == call("Target.closeTarget", targetId="owned-tab")
    assert not any(c.args[0] == "Runtime.evaluate" for c in cdp.call_args_list)
