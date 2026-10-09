"""A past session's official close by date (`core.settlement.dated_index_close`), for the catch-up
settlement of a live book a machine outage left open (2026-10-08)."""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import settlement as s


def _chart():
    # Two daily bars stamped at the 09:30 ET open (13:30 UTC), New York's -4h offset.
    return {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": -14400},
                    "timestamp": [1791379800, 1791466200],  # 2026-10-07 and 2026-10-08, 13:30 UTC
                    "indicators": {"quote": [{"close": [7801.77, 7765.36]}]},
                }
            ]
        }
    }


def test_the_bar_for_that_session_is_the_one_read():
    assert s.daily_close_on(_chart(), "2026-10-08") == 7765.36
    assert s.daily_close_on(_chart(), "2026-10-07") == 7801.77
    assert s.daily_close_on(_chart(), "2026-10-06") is None
    assert s.daily_close_on({"chart": {"result": []}}, "2026-10-08") is None


def test_a_dated_close_is_official_and_never_asked_for_a_session_not_over(monkeypatch):
    import urllib.request

    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or None)
    assert s.is_official_source("yahoo_daily")
    assert s.DatedClose(7765.36).official_settlement_price("SPX") == (7765.36, "yahoo_daily")
    later = (date.today() + timedelta(days=2)).isoformat()
    assert s.dated_index_close("SPX", later) is None and calls == []  # refused before any request
