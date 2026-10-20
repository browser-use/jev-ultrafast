"""Custom tasks choose their starting page without changing the agent policy."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import demo


@pytest.mark.parametrize(
    "url",
    ["https://example.com/search?q=fish%20oil#results", "http://127.0.0.1:8766/fixture.html?scenario=research"],
)
def test_custom_task_uses_the_entered_url_and_goal(monkeypatch, url):
    previous = Mock()
    next_agent = Mock(state={}, snapshot=Mock(return_value={"status": "ready"}))
    create_agent = Mock(return_value=next_agent)
    monkeypatch.setattr(demo, "AGENT", previous)
    monkeypatch.setattr(demo, "Agent", create_agent)

    state = demo.command("reset", {"scenario": "custom", "url": f"  {url}  ", "goal": "  搜索鱼油并打开详情页。  "})

    previous.close.assert_called_once()
    create_agent.assert_called_once_with(url, "搜索鱼油并打开详情页。", screenshots=True, record_dir=None)
    assert state["status"] == "ready"
    assert next_agent.state == {"scenario": "custom", "start_url": url}


@pytest.mark.parametrize(
    "url",
    [
        "", "example.com", "https://", "javascript:alert(1)", "file:///tmp/example.html",
        "data:text/html,hello", "ftp://example.com", "https://example.com/a b",
        "https://[broken", "https://example.com:invalid", "https://example.com:99999",
        "https://example.com/" + "x" * 4096, None, 42,
    ],
)
def test_invalid_url_does_not_close_the_current_task(monkeypatch, url):
    previous = Mock()
    create_agent = Mock()
    monkeypatch.setattr(demo, "AGENT", previous)
    monkeypatch.setattr(demo, "Agent", create_agent)

    with pytest.raises(ValueError, match="starting URL"):
        demo.command("reset", {"scenario": "custom", "url": url, "goal": "Open an article."})

    previous.close.assert_not_called()
    create_agent.assert_not_called()
    assert demo.AGENT is previous


@pytest.mark.parametrize("goal", ["", "   ", "x" * 2001])
def test_invalid_goal_does_not_close_the_current_task(monkeypatch, goal):
    previous = Mock()
    create_agent = Mock()
    monkeypatch.setattr(demo, "AGENT", previous)
    monkeypatch.setattr(demo, "Agent", create_agent)

    with pytest.raises(ValueError, match="characters"):
        demo.command("reset", {"scenario": "custom", "url": "https://example.com/", "goal": goal})

    previous.close.assert_not_called()
    create_agent.assert_not_called()


@pytest.mark.parametrize(
    ("scenario", "url"),
    [
        ("flights", "https://www.google.com/travel/flights?hl=en"),
        ("travel", f"{demo.ORIGIN}/fixture.html?scenario=travel"),
        ("research", f"{demo.ORIGIN}/fixture.html?scenario=research"),
    ],
)
def test_presets_keep_their_starting_pages(monkeypatch, scenario, url):
    next_agent = Mock(state={}, snapshot=Mock(return_value={"status": "ready"}))
    create_agent = Mock(return_value=next_agent)
    monkeypatch.setattr(demo, "AGENT", None)
    monkeypatch.setattr(demo, "Agent", create_agent)

    demo.command("reset", {"scenario": scenario, "goal": "Open an article.", "url": "https://example.com/"})

    create_agent.assert_called_once_with(url, "Open an article.", screenshots=True, record_dir=None)
