"""Navigation settling is deferred until after input, without replaying mutations."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser


def test_link_wait_is_deferred_to_observation(monkeypatch):
    instance = browser.Browser.__new__(browser.Browser)
    instance.session = "test"
    instance.fresh = Mock(return_value=True)
    instance.call = Mock(return_value={})
    operation = Mock(side_effect=[{"executed": "e1"}, {"url": "https://example.test/next"}])
    monkeypatch.setattr(browser, "browser_operation", operation)
    action = {"id": "e1", "kind": "click", "navigation": {"url": "https://example.test/next", "target": "_self"}}
    page = {"url": "https://example.test/", "marker": [1234]}

    instance.act(action, page)
    instance.call.assert_not_called()
    assert "source_url" not in action
    assert instance.after_input["source_url"] == page["url"]
    assert instance.after_input["source_document"] == 1234
    assert instance.observe(screenshot=False)["url"] == "https://example.test/next"
    assert [c.args[0]["operation"] for c in operation.call_args_list] == ["act", "observe"]
    assert instance.after_input is None
    instance.call.assert_called_once()


@pytest.mark.parametrize("error", [RuntimeError("Connection lost"), browser.StalePage("Target changed")])
def test_failed_link_input_does_not_schedule_a_wait_or_retry(monkeypatch, error):
    instance = browser.Browser.__new__(browser.Browser)
    instance.session = "test"
    instance.fresh = Mock(return_value=True)
    instance.call = Mock()
    operation = Mock(side_effect=error)
    monkeypatch.setattr(browser, "browser_operation", operation)
    action = {"id": "e1", "kind": "click", "navigation": {"url": "https://example.test/next", "target": "_self"}}
    with pytest.raises(type(error)):
        instance.act(action, {"url": "https://example.test/", "marker": [1234]})
    operation.assert_called_once()
    instance.call.assert_not_called()
    assert getattr(instance, "after_input", None) is None
