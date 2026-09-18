"""Offline contracts for the demo's start-page selection. No network, no browser."""

import pytest

from jev_ultrafast.demo import ORIGIN, start_url


def test_preset_flights_is_the_real_site():
    assert start_url("flights", None) == "https://www.google.com/travel/flights?hl=en"


@pytest.mark.parametrize("scenario", ["travel", "research"])
def test_preset_fixtures_stay_local(scenario):
    assert start_url(scenario, "") == f"{ORIGIN}/fixture.html?scenario={scenario}"


def test_custom_url_wins_and_is_trimmed():
    url = "  https://kyfw.12306.cn/otn/leftTicket/init  "
    assert start_url("flights", url) == "https://kyfw.12306.cn/otn/leftTicket/init"


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.test/x",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "example.test/x",
        "https:///nohost",
        "/relative",
    ],
)
def test_unsupported_urls_are_rejected(value):
    with pytest.raises(ValueError):
        start_url("flights", value)


def test_overlong_url_is_rejected():
    with pytest.raises(ValueError):
        start_url("flights", "https://example.test/" + "a" * 2000)
