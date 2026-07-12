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
