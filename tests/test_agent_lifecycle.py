"""Offline ownership and exception contracts for Agent initialization."""

import base64
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from jev_ultrafast import agent, browser


@pytest.fixture
def owned(monkeypatch):
    instance = Mock()
    instance.observe.return_value = {"screenshot": base64.b64encode(b"frame").decode()}
    monkeypatch.setattr(agent, "Browser", Mock(return_value=instance))
    return instance


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failed_first_observation_closes_owned_browser(owned, error_type):
    failure = error_type("Observation failed")
    owned.observe.side_effect = failure
    with pytest.raises(BaseException) as caught:
        agent.Agent("https://example.test/", "Read page")
    assert caught.value is failure
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_ordinary_cleanup_failure_preserves_initialization_error(owned, error_type):
    failure = error_type("Observation failed")
    owned.observe.side_effect = failure
    owned.close.side_effect = TimeoutError("Close timed out")
    with pytest.raises(BaseException) as caught:
        agent.Agent("https://example.test/", "Read page")
    assert caught.value is failure
    assert any("Close timed out" in note for note in caught.value.__notes__)
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_fresh_cleanup_interrupt_propagates_with_initialization_context(owned, error_type, interrupt_type):
    failure = error_type("Observation failed")
    interrupt = interrupt_type(73) if interrupt_type is SystemExit else interrupt_type("Cleanup interrupted")
    owned.observe.side_effect = failure
    owned.close.side_effect = interrupt
    with pytest.raises(BaseException) as caught:
        agent.Agent("https://example.test/", "Read page")
    assert caught.value is interrupt
    assert interrupt.__context__ is failure
    assert not interrupt.__suppress_context__
    owned.close.assert_called_once_with()


def test_record_directory_creation_failure_closes_browser(owned, tmp_path):
    blocked = tmp_path / "recording"
    blocked.write_text("Existing file")
    with pytest.raises(FileExistsError):
        agent.Agent("https://example.test/", "Read page", record_dir=blocked)
    assert blocked.read_text() == "Existing file"
    owned.close.assert_called_once_with()


def test_first_record_write_failure_closes_browser(owned, tmp_path, monkeypatch):
    failure = PermissionError("Record write denied")
    monkeypatch.setattr(Path, "write_bytes", Mock(side_effect=failure))
    with pytest.raises(PermissionError) as caught:
        agent.Agent("https://example.test/", "Read page", record_dir=tmp_path / "recording")
    assert caught.value is failure
    owned.close.assert_called_once_with()


def test_invalid_record_path_does_not_create_browser(owned):
    with pytest.raises(TypeError):
        agent.Agent("https://example.test/", "Read page", record_dir=object())
    agent.Browser.assert_not_called()
    owned.close.assert_not_called()


def test_browser_creation_failure_does_not_trigger_another_cleanup(owned):
    failure = RuntimeError("Browser construction failed")
    agent.Browser.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        agent.Agent("https://example.test/", "Read page")
    assert caught.value is failure
    owned.close.assert_not_called()


@pytest.mark.parametrize("record", [False, True])
def test_successful_initialization_keeps_browser_and_optional_first_frame(owned, tmp_path, record):
    record_dir = tmp_path / "recording" if record else None
    instance = agent.Agent("https://example.test/", "Read page", record_dir=record_dir)
    owned.observe.assert_called_once_with(screenshot=record)
    owned.close.assert_not_called()
    assert instance.browser is owned
    assert instance.state["status"] == "ready"
    assert instance.state["record"] is record
    if record:
        assert (record_dir / "000000.jpg").read_bytes() == b"frame"
    instance.close()
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_context_cleanup_failure_preserves_task_error(owned, error_type):
    failure = error_type("Task failed")
    owned.close.side_effect = TimeoutError("Close timed out")
    with pytest.raises(BaseException) as caught:
        with agent.Agent("https://example.test/", "Read page"):
            raise failure
    assert caught.value is failure
    assert any("Close timed out" in note for note in caught.value.__notes__)
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("error_type", [None, RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_fresh_context_cleanup_interrupt_propagates(owned, error_type, interrupt_type):
    failure = error_type("Task failed") if error_type else None
    interrupt = interrupt_type("Cleanup interrupted")
    owned.close.side_effect = interrupt
    with pytest.raises(BaseException) as caught:
        with agent.Agent("https://example.test/", "Read page"):
            if failure is not None:
                raise failure
    assert caught.value is interrupt
    assert interrupt.__context__ is failure
    owned.close.assert_called_once_with()


def test_context_close_failure_without_task_error_propagates(owned):
    failure = TimeoutError("Close timed out")
    owned.close.side_effect = failure
    with pytest.raises(TimeoutError) as caught:
        with agent.Agent("https://example.test/", "Read page"):
            pass
    assert caught.value is failure
    owned.close.assert_called_once_with()


def test_context_success_closes_browser_once(owned):
    with agent.Agent("https://example.test/", "Read page") as instance:
        assert instance.browser is owned
        owned.close.assert_not_called()
    owned.close.assert_called_once_with()


@pytest.mark.parametrize("stage", ["attach", "navigate", "observe", "record", "body"])
@pytest.mark.parametrize("cleanup", ["success", "timeout", "interrupt"])
def test_ownership_across_browser_agent_and_context(monkeypatch, tmp_path, stage, cleanup):
    failure = RuntimeError("Operation failed")
    cleanup_failure = TimeoutError("Close timed out") if cleanup == "timeout" else KeyboardInterrupt("Stop now")
    frame = {"screenshot": base64.b64encode(b"frame").decode()}
    blocked = tmp_path / "recording"
    blocked.write_text("Existing file")

    def respond(method, **_params):
        if method == "Target.createTarget":
            return {"targetId": "owned-tab"}
        if method == "Target.attachToTarget":
            if stage == "attach":
                raise failure
            return {"sessionId": "owned-session"}
        if method == "Page.navigate" and stage == "navigate":
            return {"frameId": "owned-frame", "errorText": "net::ERR_NAME_NOT_RESOLVED"}
        if method == "Runtime.evaluate":
            return {"result": {"value": "complete"}}
        if method == "Target.closeTarget" and cleanup != "success":
            raise cleanup_failure
        return {}

    cdp = Mock(side_effect=respond)
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser.Browser, "observe", Mock(
        return_value=frame, side_effect=failure if stage == "observe" else None,
    ))
    with pytest.raises(BaseException) as caught:
        with agent.Agent("https://example.test/", "Read page", record_dir=blocked if stage == "record" else None):
            if stage == "body":
                raise failure

    primary_error = caught.value.__context__ if cleanup == "interrupt" else caught.value
    if stage == "record":
        assert isinstance(primary_error, FileExistsError)
        assert blocked.read_text() == "Existing file"
    elif stage == "navigate":
        assert isinstance(primary_error, RuntimeError)
        assert "net::ERR_NAME_NOT_RESOLVED" in str(primary_error)
    else:
        assert primary_error is failure
    if cleanup == "interrupt":
        assert caught.value is cleanup_failure
    elif cleanup == "timeout":
        assert any("Close timed out" in note for note in caught.value.__notes__)
    assert cdp.call_args_list.count(call("Target.createTarget", url="about:blank", background=True)) == 1
    closes = [c for c in cdp.call_args_list if c.args[0] == "Target.closeTarget"]
    assert closes == [call("Target.closeTarget", targetId="owned-tab")]
    assert cdp.call_args_list[-1] == closes[0]
