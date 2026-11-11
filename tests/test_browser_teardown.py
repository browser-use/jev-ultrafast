"""Teardown only: what a tab release owes the caller when the browser is already gone.

The live arms (D:/ML_Projects/_scratch/jev_leave_live.py, F and H) reproduce the shape these tests
pin: the launcher's ``leave()`` shuts the browser down, then interpreter shutdown still runs
``demo.py``'s ``close_browser`` -> ``Agent.close()`` -> ``Browser.close()``, and ``Target.closeTarget``
is asked of a browser that no longer answers.
"""

from unittest.mock import Mock

import pytest


def browser_module():
    import jev_ultrafast.browser as module

    return module


def closed_over(monkeypatch, error=None, result=None):
    """A Browser whose only live part is a tab id, reading CDP off a scripted answer."""
    module = browser_module()
    b = module.Browser.__new__(module.Browser)
    b.target = "T1"
    sent = Mock(side_effect=error, return_value=result or {"result": {}})
    monkeypatch.setattr(module, "cdp", sent)
    return module, b, sent


def test_a_gone_browser_does_not_make_the_tab_release_raise(monkeypatch, capsys):
    module, b, sent = closed_over(monkeypatch, RuntimeError("no close frame received or sent"))
    b.close()  # interpreter shutdown is not a place to raise from
    assert sent.call_args.args[0] == "Target.closeTarget"
    assert b.target is None, "the tab id must be dropped even when the release failed"


def test_the_failing_release_reports_one_line_naming_the_error(monkeypatch, capsys):
    _, b, _ = closed_over(monkeypatch, RuntimeError("no close frame received or sent"))
    b.close()
    err = capsys.readouterr().err
    assert "Tab release did not land" in err
    assert "no close frame received or sent" in err, "the harness' own words must survive into the line"
    assert "Traceback" not in err, "one line, not a 13-line 'Exception ignored' trace"


def test_an_unreachable_daemon_is_reported_the_same_way(monkeypatch, capsys):
    _, b, _ = closed_over(monkeypatch, ConnectionRefusedError("daemon is not listening"))
    b.close()
    err = capsys.readouterr().err
    assert "ConnectionRefusedError" in err, "which failure it was stays readable, they are not one bucket"


def test_a_release_that_lands_reports_nothing(monkeypatch, capsys):
    _, b, sent = closed_over(monkeypatch)
    b.close()
    assert sent.call_count == 1
    assert capsys.readouterr().err == "", "the skipped-release line is not a closing greeting"


def test_a_second_close_asks_the_browser_nothing(monkeypatch):
    _, b, sent = closed_over(monkeypatch, RuntimeError("no close frame received or sent"))
    b.close()
    b.close()
    assert sent.call_count == 1, "the tab is gone either way; teardown must not re-ask"


def test_an_unexpected_error_is_not_swept_into_teardown(monkeypatch):
    """The catch is sized for a dead browser and a dead socket, not for every RuntimeError sibling."""
    _, b, _ = closed_over(monkeypatch, ValueError("unknown operation"))
    with pytest.raises(ValueError):
        b.close()
