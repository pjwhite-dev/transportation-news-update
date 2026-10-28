"""Deterministic calendars: U.S. federal holidays, NYSE sessions, and the story-freshness window.

Nothing here calls a model or the network. Dates are America/New_York calendar dates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def federal_holidays(year: int) -> set[date]:
    """Observed federal holidays that fall in ``year``."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    days = {_observed(day) for day in fixed}
    days |= {
        _nth_weekday(year, 1, 0, 3),    # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),    # Washington's Birthday
        _last_weekday(year, 5, 0),      # Memorial Day
        _nth_weekday(year, 9, 0, 1),    # Labor Day
        _nth_weekday(year, 10, 0, 2),   # Columbus Day
        _nth_weekday(year, 11, 3, 4),   # Thanksgiving
    }
    return {day for day in days if day.year == year}


def is_federal_holiday(day: date) -> bool:
    # A New Year's Day observed on Dec 31 belongs to the next year's set.
    return day in federal_holidays(day.year) or day in federal_holidays(day.year + 1)


def nyse_holidays(year: int) -> set[date]:
    """NYSE full-day closures in ``year``. A Saturday holiday closes Friday; Sunday closes Monday.

    New Year's Day on a Saturday is the one exception: the exchange stays open the Friday before.
    """
    days: set[date] = set()
    for day in (date(year, 6, 19), date(year, 7, 4), date(year, 12, 25)):
        days.add(_observed(day))
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    days |= {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
    }
    return {day for day in days if day.year == year}


def is_nyse_session(day: date) -> bool:
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def last_completed_nyse_session(now: datetime, close_hour: int = 16, close_minute: int = 30) -> date:
    """The most recent session whose close (plus a settling margin) is already past at ``now`` (ET)."""
    local = now.astimezone(EASTERN)
    day = local.date()
    closed_today = (local.hour, local.minute) >= (close_hour, close_minute)
    if not (is_nyse_session(day) and closed_today):
        day -= timedelta(days=1)
    while not is_nyse_session(day):
        day -= timedelta(days=1)
    return day


def session_label(day: date, kind: str = "Close") -> str:
    """'Close, Mon., Oct. 26'."""
    weekday = ("Mon.", "Tue.", "Wed.", "Thu.", "Fri.", "Sat.", "Sun.")[day.weekday()]
    month = ("Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")[day.month - 1]
    return f"{kind}, {weekday}, {month} {day.day}"


def is_non_publishing_day(day: date) -> bool:
    return day.weekday() >= 5 or is_federal_holiday(day)


def freshness_hours(edition: datetime, *, normal: int = 48, extended: int = 72) -> int:
    """48 hours normally; 72 when the edition day or the day before is a weekend or federal holiday.

    Monday editions (the weekend before), a Tuesday after a Monday holiday, and weekend editions all
    get the longer window, so stories from the last working day are not lost to the calendar.
    """
    day = edition.astimezone(EASTERN).date()
    if is_non_publishing_day(day) or is_non_publishing_day(day - timedelta(days=1)):
        return extended
    return normal
