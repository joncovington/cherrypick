"""The dead-spot clock follows the session's own close, and a trading day (core.calendar)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.streamer import daemon as _daemon

ET = ZoneInfo("America/New_York")


def _at(y, m, d, hh, mm) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=ET).timestamp()


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # An ordinary Friday: 09:35 through 15:55.
        ((2026, 11, 20, 9, 34), False),
        ((2026, 11, 20, 9, 35), True),
        ((2026, 11, 20, 15, 55), True),
        ((2026, 11, 20, 15, 56), False),
        # The day after Thanksgiving closes at 13:00: the window ends at 12:55, not 15:55.
        ((2026, 11, 27, 10, 0), True),
        ((2026, 11, 27, 12, 55), True),
        ((2026, 11, 27, 12, 56), False),
        ((2026, 11, 27, 13, 35), False),
        ((2026, 11, 27, 15, 15), False),
        # A weekend and a holiday (Thanksgiving) are never in it.
        ((2026, 11, 21, 11, 0), False),
        ((2026, 11, 26, 11, 0), False),
    ],
)
def test_in_rth_clock_follows_the_session_close(when, expected):
    assert _daemon._in_rth_clock(_at(*when)) is expected


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ((2026, 12, 24, 12, 55), True),  # another early close
        ((2026, 12, 24, 12, 56), False),
        ((2026, 7, 3, 12, 56), False),
    ],
)
def test_in_rth_clock_other_early_closes(when, expected):
    assert _daemon._in_rth_clock(_at(*when)) is expected


def test_in_rth_clock_unreadable_calendar_keeps_detection_on(monkeypatch):
    """A calendar that raises must not turn dead-spot detection off: fall back to a weekday and
    the regular 15:55."""
    from cherrypick.core import calendar as _calendar

    def boom(_d):
        raise RuntimeError("calendar unreadable")

    monkeypatch.setattr(_calendar, "is_trading_day", boom)
    assert _daemon._in_rth_clock(_at(2026, 11, 20, 11, 0)) is True
    assert _daemon._in_rth_clock(_at(2026, 11, 20, 15, 56)) is False
    assert _daemon._in_rth_clock(_at(2026, 11, 21, 11, 0)) is False
