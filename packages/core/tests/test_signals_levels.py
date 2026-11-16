"""core.signals: a source trending for N bars, the close against a fixed price, and the reference levels
(the previous session's, week's and month's high and low, and the opening range's high, low and
midpoint). A level whose period the bars do not wholly hold is undefined, never a partial extreme."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_signals import bars, fired, mirror

from cherrypick.core import calendar as cal
from cherrypick.core import signals as sg

CLOSE = {"name": "close"}
DAY1, DAY2 = "2026-01-05", "2026-01-06"


def daily(days, highs=None, closes=None):
    """One daily bar a session: high `highs[i]` (default 100), low 90, close `closes[i]` (default 95)."""
    rows = []
    for i, d in enumerate(days):
        h = highs[i] if highs else 100.0
        c = closes[i] if closes else 95.0
        rows.append({"session": d, "open": 95.0, "high": max(h, c), "low": 90.0, "close": c})
    return sg.daily(rows)


def trading_days(first: str, last: str) -> list[str]:
    d, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while d <= end:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# --- trend ------------------------------------------------------------------------------------------
TREND = {"kind": "trend", "a": CLOSE, "direction": "rising", "n": 3, "seed": True}


def test_a_trend_fires_after_n_rising_bars_and_not_on_its_mirror():
    bs = bars([100, 100, 99, 100, 101, 102, 101, 102, 103, 104])
    assert fired(TREND, bs) == [5, 9]
    assert fired(TREND, mirror(bs)) == []
    assert fired({**TREND, "direction": "falling"}, mirror(bs)) == [5, 9]
    (first, *_) = sg.firings(TREND, bs)
    assert first.value == pytest.approx(3.0)  # the change over the three bars
    assert fired(TREND, bars([100, 99, 100, 100, 101])) == []  # a flat bar is not a rising one


def test_a_trend_without_seed_is_counted_within_each_session():
    # The rise runs across the close: the next session's first bar is still short of three (it reads
    # the fall before), its second has three only counting the bar before the close.
    bs = bars([100, 99, 100]) + bars([101, 102], session=DAY2, opens=100)
    assert fired(TREND, bs) == [4]
    assert fired({**TREND, "seed": False}, bs) == []


def test_a_trend_reads_an_indicator_too():
    closes = [100, 99, 98, 97, 96, 97, 99, 101, 103, 105]
    spec = {**TREND, "a": {"name": "sma", "n": 2}, "n": 2}
    out = fired(spec, bars(closes))
    assert out and all(i >= 6 for i in out)
    # A source anchored on an event reads the caller's dates through the trend.
    vwap = {"name": "vwap", "anchor": {"event": "cpi"}, "price": "close", "weight": "equal"}
    anchored = {**TREND, "a": vwap, "n": 2}
    assert sg.firings(anchored, bars(closes), events={"cpi": [DAY1]})


# --- change from a fixed price ---------------------------------------------------------------------
def test_a_change_from_a_fixed_price_fires_past_the_percent_and_rearms():
    bs = bars([100, 100.5, 101.2, 100.8, 101.5])
    spec = {"kind": "change_from", "price": 100.0, "direction": "up", "pct": 1.0}
    assert fired(spec, bs) == [2, 4]
    assert fired(spec, mirror(bs)) == []
    assert fired({**spec, "direction": "down"}, mirror(bs)) == [2, 4]


# --- reference levels -------------------------------------------------------------------------------
def test_the_previous_weeks_high_is_read_only_when_that_week_is_whole():
    week1 = trading_days("2026-01-05", "2026-01-09")
    week2 = trading_days("2026-01-12", "2026-01-16")
    bs = daily(week1 + week2, highs=[100, 104, 101, 102, 100] + [100] * 5)
    level = sg.source({"name": "prior_period", "period": "week", "side": "high"}, bs, seed=False)
    assert level[:5] == [None] * 5 and level[5:] == [104.0] * 5
    low = sg.source({"name": "prior_period", "period": "week", "side": "low"}, bs, seed=False)
    assert low[5:] == [90.0] * 5
    gappy = [b for b in bs if b.session != "2026-01-07"]  # a day of week 1 missing
    assert (
        sg.source({"name": "prior_period", "period": "week", "side": "high"}, gappy, seed=False)[4:]
        == [None] * 5
    )
    skipped = [b for b in bs[:5]] + daily(trading_days("2026-01-20", "2026-01-23"))  # week 2 missing
    assert (
        sg.source({"name": "prior_period", "period": "week", "side": "high"}, skipped, seed=False)[5:]
        == [None] * 4
    )


def test_the_previous_months_low_and_a_cross_of_it():
    dec = trading_days("2025-12-01", "2025-12-31")
    jan = trading_days("2026-01-02", "2026-01-09")
    closes = [95.0] * len(dec) + [95.0, 89.0, 95.0, 88.0, 95.0, 95.0]
    bs = daily(dec + jan, closes=closes)
    spec = {
        "kind": "cross",
        "a": CLOSE,
        "b": {"name": "prior_period", "period": "month", "side": "low"},
        "direction": "below",
        "seed": True,
    }
    first_jan = len(dec)
    assert fired(spec, bs) == [first_jan + 1, first_jan + 3]
    assert fired(spec, bs[1:]) == []  # December is not whole without its first day


def test_the_previous_session_level_needs_the_session_just_before():
    bs = daily([DAY1, DAY2, "2026-01-07"], highs=[100, 103, 100])
    level = sg.source({"name": "prior_period", "period": "session", "side": "high"}, bs, seed=False)
    assert level == [None, 100.0, 103.0]
    apart = [bs[0], bs[2]]
    assert sg.source({"name": "prior_period", "period": "session", "side": "high"}, apart, seed=False) == [
        None,
        None,
    ]


def test_the_opening_range_midpoint_as_a_level():
    closes = [100, 101, 99, 100, 100, 100, 99.5, 100.4, 100.6]  # 30-minute range 99..101, mid 100
    bs = bars(closes, opens=100)
    mid = sg.source({"name": "opening_range", "minutes": 30, "level": "mid"}, bs, seed=False)
    assert mid[:6] == [None] * 6 and mid[6:] == [100.0] * 3
    hi = sg.source({"name": "opening_range", "minutes": 30, "level": "high"}, bs, seed=False)
    assert hi[6:] == [101.0] * 3
    spec = {"kind": "cross", "a": CLOSE, "b": {"name": "opening_range", "minutes": 30, "level": "mid"}}
    assert fired({**spec, "direction": "above", "seed": True}, bs) == [7]
    assert sg.source({"name": "opening_range", "minutes": 32, "level": "mid"}, bs, seed=False) == [None] * 9


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({**TREND, "direction": "up"}, "direction"),
        ({**TREND, "n": 0}, "n"),
        ({"kind": "change_from", "price": 0, "direction": "up", "pct": 1}, "price"),
        ({"kind": "change_from", "price": 100, "direction": "up", "pct": -1}, "pct"),
        ({"kind": "cross", "a": CLOSE, "b": {"name": "prior_period", "period": "year", "side": "high"},
          "direction": "above", "seed": True}, "period"),
        ({"kind": "cross", "a": CLOSE, "b": {"name": "prior_period", "period": "week", "side": "mid"},
          "direction": "above", "seed": True}, "side"),
        ({"kind": "cross", "a": CLOSE, "b": {"name": "opening_range", "minutes": 30, "level": "top"},
          "direction": "above", "seed": True}, "level"),
        ({"kind": "cross", "a": CLOSE, "b": {"name": "opening_range", "minutes": 0, "level": "mid"},
          "direction": "above", "seed": True}, "minutes"),
    ],
)  # fmt: skip
def test_a_malformed_spec_is_refused(spec, match):
    with pytest.raises(ValueError, match=match):
        sg.validate(spec)
