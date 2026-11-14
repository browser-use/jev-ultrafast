"""The one departure date every flights-demo surface must agree on.

Google Flights marks past-month days aria-hidden, and snapshot.js excludes
aria-hidden elements from the agent's action space. A goal naming a fixed
date becomes unreachable the day that date passes. Compute it at runtime
instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DEPARTURE_OFFSET_DAYS = 30

# Hardcoded, not strftime: Google Flights is pinned to English (?hl=en), but
# strftime's %B/%A/%b/%a read the process LC_TIME, so a non-English host
# locale would silently break every check in examples/flights.py's verify().
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class FlightDate:
    """A calendar date rendered in every format the flights demo needs.

    ``reference_today`` is the date the offset was computed from: it decides
    whether the year needs to be spelled out (Google Flights appends it once
    the departure falls in a different year than today).
    """

    date: date
    reference_today: date

    @property
    def iso(self) -> str:
        """``2026-10-21``, as it appears in the Google Flights URL."""
        return self.date.isoformat()

    @property
    def long(self) -> str:
        """``October 21, 2026``, for the goal text."""
        return f"{_MONTH_NAMES[self.date.month - 1]} {self.date.day}, {self.date.year}"

    @property
    def _year_suffix(self) -> str:
        return "" if self.date.year == self.reference_today.year else f", {self.date.year}"

    @property
    def short_weekday(self) -> str:
        """``Wed, Oct 21``, matching the Departure field's rendered value."""
        weekday = _WEEKDAY_ABBR[self.date.weekday()]
        month = _MONTH_ABBR[self.date.month - 1]
        return f"{weekday}, {month} {self.date.day}{self._year_suffix}"

    @property
    def long_weekday(self) -> str:
        """``Wednesday, October 21``, matching a flight result's label."""
        weekday = _WEEKDAY_NAMES[self.date.weekday()]
        month = _MONTH_NAMES[self.date.month - 1]
        return f"{weekday}, {month} {self.date.day}{self._year_suffix}"

    @property
    def goal_text(self) -> str:
        """The flights demo's goal, naming this date."""
        return (
            f"Find one-way flights from Zurich to London on {self.long}, for one adult in "
            "economy. Stop when matching flight options are visible. Do not select or book "
            "a flight."
        )


def flight_departure(today: date | None = None) -> FlightDate:
    """The flights demo's departure date, always far enough ahead to stay indexed.

    Args:
        today: Override for the current date (tests only); defaults to the real date.

    Returns:
        The shared date plus every rendering the demo's surfaces need.
    """
    reference = today or date.today()
    return FlightDate(reference + timedelta(days=DEPARTURE_OFFSET_DAYS), reference)
