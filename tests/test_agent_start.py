"""Starting a run cleans up after itself. No browser process or model calls."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop


def starting(monkeypatch, observe_error, close_error=None):
    """An Agent whose first observation fails, over a Browser whose tab close may also fail."""
    browser = Mock()
    browser.observe.side_effect = observe_error
    browser.close.side_effect = close_error
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    return browser


def test_an_interrupt_during_the_first_observation_closes_the_tab(monkeypatch):
    """Nothing holds the Agent yet, so this is the only place that can still close the tab."""
    browser = starting(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        loop.Agent("https://example.test/", "Find a book")
    browser.close.assert_called_once()


def test_a_failed_close_does_not_hide_why_the_first_observation_failed(monkeypatch):
    starting(monkeypatch, RuntimeError("page crashed"), close_error=RuntimeError("No target with given id found"))
    with pytest.raises(RuntimeError, match="page crashed") as caught:
        loop.Agent("https://example.test/", "Find a book")
    assert any("No target with given id found" in note for note in caught.value.__notes__)


def test_a_failed_first_observation_still_closes_the_tab(monkeypatch):
    browser = starting(monkeypatch, RuntimeError("page crashed"))
    with pytest.raises(RuntimeError, match="page crashed"):
        loop.Agent("https://example.test/", "Find a book")
    browser.close.assert_called_once()
