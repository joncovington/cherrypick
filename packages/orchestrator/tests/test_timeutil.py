"""Unit tests for orchestrator.timeutil trading-calendar logic (clock injected)."""

from datetime import datetime

import pytest

from cherrypick.orchestrator import timeutil

pytestmark = pytest.mark.unit

# A known 2026 holiday set (subset) to prove holiday gating without reading MEIC config.
HOLIDAYS = {"2026-07-03", "2026-12-25"}


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timeutil._tz("America/New_York"))


def test_weekend_is_not_a_trading_day():
    assert timeutil.is_trading_day(_et(2026, 7, 11, 10, 0)) is False  # Saturday
    assert timeutil.is_trading_day(_et(2026, 7, 12, 10, 0)) is False  # Sunday


def test_weekday_is_a_trading_day():
    assert timeutil.is_trading_day(_et(2026, 7, 10, 10, 0)) is True  # Friday


def test_holiday_is_not_a_trading_day():
    assert timeutil.is_trading_day(_et(2026, 7, 3, 10, 0), HOLIDAYS) is False


def test_market_hours_boundaries():
    assert timeutil.is_market_hours(_et(2026, 7, 10, 9, 29)) is False  # before open
    assert timeutil.is_market_hours(_et(2026, 7, 10, 9, 30)) is True  # open
    assert timeutil.is_market_hours(_et(2026, 7, 10, 16, 0)) is True  # close edge
    assert timeutil.is_market_hours(_et(2026, 7, 10, 16, 1)) is False  # after close


def test_session_window_starts_before_open():
    # Session window opens at 09:15 (services warm up before the bell), market hours at 09:30.
    dt = _et(2026, 7, 10, 9, 20)
    assert timeutil.is_session_window(dt) is True
    assert timeutil.is_market_hours(dt) is False


def test_off_hours_not_in_session():
    assert timeutil.is_session_window(_et(2026, 7, 10, 3, 30)) is False


# --------------------------------------------------------------------------- the holiday source
def test_load_holidays_returns_real_dates():
    """The regression this exists for: `load_holidays` used to scan MEIC's config for
    `nyse_holidays_<year>` keys, which were retired when the calendar moved into cherrypick.core.
    The scan matched nothing, so every caller ran with an EMPTY set and `doctor` printed
    `holidays_loaded=0` — read as a known gap rather than the live fault it was."""
    holidays = timeutil.load_holidays([2026])
    assert holidays, "an empty set is the bug this replaced"
    assert "2026-09-07" in holidays  # Labor Day
    assert "2026-12-25" in holidays  # Christmas
    assert "2026-07-04" not in holidays  # observed on the 3rd in 2026, not the Saturday itself


def test_load_holidays_spans_the_year_boundary():
    """Defaults to this year AND next, so a check on 31 December still knows about 1 January."""
    both = timeutil.load_holidays([2026, 2027])
    assert "2026-01-01" in both and "2027-01-01" in both


def test_a_market_holiday_is_not_a_trading_day_end_to_end():
    """The consequence that matters. is_trading_day() consumes this set, and the watchdog consumes
    is_trading_day() — so an empty set made a closed market look like an ordinary session and had
    the watchdog expect fresh module data on a day nothing traded."""
    holidays = timeutil.load_holidays([2026])
    labor_day = _et(2026, 9, 7, 10, 0)
    assert timeutil.is_trading_day(labor_day, holidays) is False
    assert timeutil.is_market_hours(labor_day, holidays) is False
    assert timeutil.is_session_window(labor_day, holidays) is False
    # ...and the next day is open again, so this isn't blanket-blocking September.
    assert timeutil.is_trading_day(_et(2026, 9, 8, 10, 0), holidays) is True


def test_a_calendar_failure_degrades_to_weekday_only(monkeypatch):
    """This sits on the watchdog's path: a calendar hiccup must return the old empty set, never
    raise. Degrading to weekday-only gating is the pre-existing behaviour, not a new failure."""
    import cherrypick.core.calendar as cal

    monkeypatch.setattr(cal, "nyse_holidays", lambda year: (_ for _ in ()).throw(RuntimeError("boom")))
    assert timeutil.load_holidays([2026]) == set()
