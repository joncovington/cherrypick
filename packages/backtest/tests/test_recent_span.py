"""`fetch --recent N`: the last N trading days through today, as the nightly job asks."""

from __future__ import annotations

from datetime import date

from cherrypick.backtest.__main__ import recent_span


def test_the_last_n_trading_days_through_today():
    assert recent_span(3, today=date(2026, 10, 16)) == ("2026-10-14", "2026-10-16")


def test_a_weekend_ends_on_the_last_session_and_a_holiday_is_skipped():
    assert recent_span(1, today=date(2026, 10, 17)) == ("2026-10-16", "2026-10-17")
    assert recent_span(2, today=date(2026, 11, 27))[0] == "2026-11-25", "Thanksgiving is no session"
