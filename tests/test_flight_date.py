"""Offline checks for the flights demo's runtime-computed departure date."""

from datetime import date, timedelta

from jev_ultrafast.flight_date import flight_departure


def test_flight_departure_defaults_to_today_plus_thirty_days():
    today = date.today()
    assert flight_departure(today=today).date == today + timedelta(days=30)


def test_flight_departure_formats_have_no_leading_zero_day():
    departure = flight_departure(today=date(2026, 9, 4))
    expected = date(2026, 10, 4)
    assert departure.date == expected
    assert departure.iso == "2026-10-04"
    assert departure.long == "October 4, 2026"
    # Same year as reference_today, so no year suffix.
    assert departure.short_weekday == "Sun, Oct 4"
    assert departure.long_weekday == "Sunday, October 4"
    assert "October 4, 2026" in departure.goal_text
    assert "2026-10-04" not in departure.goal_text


def test_flight_departure_appends_year_once_it_rolls_over():
    departure = flight_departure(today=date(2026, 12, 5))
    assert departure.date == date(2027, 1, 4)
    assert departure.iso == "2027-01-04"
    assert departure.long == "January 4, 2027"
    assert departure.short_weekday == "Mon, Jan 4, 2027"
    assert departure.long_weekday == "Monday, January 4, 2027"
