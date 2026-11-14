"""Indicators, strike rules and the event calendar. Every reading is shown to fire and to fail: a gate that
cannot block reads as coverage it is not."""

from __future__ import annotations

from datetime import date

import pytest
from market import rows, spx_snapshot

from cherrypick.ensemble import events, strikes
from cherrypick.ensemble import indicators as ind
from cherrypick.ensemble.model import Chain


# --- bars, RSI, stochastic ------------------------------------------------------------------------
def test_bars_complete_only():
    tr = [(570 + i, 100.0 + i) for i in range(12)]
    b = ind.bars(tr, 5, until=580)
    assert [x["start"] for x in b] == [570, 575]
    assert b[0]["open"] == 100.0 and b[0]["close"] == 104.0 and b[0]["high"] == 104.0
    assert len(ind.bars(tr, 5, until=579)) == 1  # the second bar is not complete at 579


def test_rsi_reads_a_rising_and_a_falling_trail():
    up = [(570 + i, 100.0 + i) for i in range(120)]
    down = [(570 + i, 200.0 - i) for i in range(120)]
    assert ind.intraday_rsi(up, 5, 14, until=690) == 100.0
    assert ind.intraday_rsi(down, 5, 14, until=690) == pytest.approx(0.0, abs=1e-9)
    assert ind.intraday_rsi(up, 5, 14, until=600) is None  # six bars: not enough


def test_stochastic_known_values():
    up = [{"high": 10 + i, "low": 9 + i, "close": 9.5 + i} for i in range(30)]
    k, d = ind.stochastic(up, 14, 3, 3)
    assert k == pytest.approx(100.0 * 13.5 / 14)  # a window of 14 bars spans 14 points
    assert d == pytest.approx(k)  # a straight line: the average of equals
    flat = [{"high": 5.0, "low": 5.0, "close": 5.0}] * 30
    assert ind.stochastic(flat) == (50.0, 50.0)
    assert ind.stochastic(up[:10]) is None


def test_opening_range_waits_for_the_range_to_finish():
    tr = [(570 + i, 100.0 + (i % 7)) for i in range(61)]
    assert ind.opening_range(tr[:59], 60) is None
    r = ind.opening_range(tr, 60)
    assert r["high"] == 106.0 and r["done_at"] == 630


def test_sd_change_scales_with_vix():
    s = ind.daily_sigma(6000.0, 16.0)
    assert s == pytest.approx(6000 * 0.16 / 252**0.5)
    assert ind.sd_change(6000 + s, 6000.0, 16.0) == pytest.approx(1.0)
    assert ind.sd_change(6000.0, 6000.0, None) is None


def test_average_gate_input():
    closes = [float(i) for i in range(1, 41)]
    assert ind.above_average(closes, 41.0, "ema", 10) is True
    assert ind.above_average(closes, 10.0, "sma", 30) is False
    assert ind.above_average(closes[:5], 41.0, "sma", 30) is None


# --- strikes --------------------------------------------------------------------------------------
def chain(spot=6000.0, years=6 / 8760, step=5.0):
    return Chain("2026-11-16", rows(spot, years, 0.2, step=step))


def test_nearest_strike_modes():
    c = chain()
    assert c.nearest_strike(6002.4) == 6000.0
    assert c.nearest_strike(6002.5) == 6000.0  # a tie goes to the lower
    assert c.nearest_strike(5997.4, mode="down") == 5995.0
    assert c.nearest_strike(5997.4, mode="up") == 6000.0
    assert c.nearest_strike(10_000.0, mode="up") is None


def test_each_strike_rule():
    snap = spx_snapshot(spot=6003.0, prev_close=5987.0)
    c = snap.chains["2026-11-16"]
    pick = lambda rule, right="P", chosen=None: strikes.pick(rule, right, snap, c, chosen or {})  # noqa: E731
    assert pick({"rule": "spot", "points": -5}) == 6000.0
    assert (
        pick({"rule": "spot", "pct": -0.05, "round": "down"}) == 5995.0
    )  # 5999.9985 rounds down to the next listed strike
    assert pick({"rule": "prev_close", "points": 0.01}) == 5985.0
    assert pick({"rule": "from_leg", "leg": "a", "points": -10}, chosen={"a": 6000.0}) == 5990.0
    assert pick({"rule": "from_leg", "leg": "a", "points": -7}, chosen={"a": 6000.0}) is None  # not listed
    assert (
        pick({"rule": "from_leg", "leg": "a", "pct": -0.5, "round": "down"}, chosen={"a": 6000.0}) == 5970.0
    )
    assert pick({"rule": "from_leg", "leg": "zz", "points": 1}) is None
    assert pick({"rule": "same_as", "leg": "a"}, chosen={"a": 6000.0}) == 6000.0
    assert (
        pick(
            {"rule": "match_width", "from": "c", "width_of": ["a", "b"]},
            "C",
            {"a": 6000.0, "b": 5970.0, "c": 6010.0},
        )
        == 6040.0
    )
    with pytest.raises(ValueError):
        pick({"rule": "nope"})


def test_delta_rule_nearest_and_at_most():
    snap = spx_snapshot()
    c = snap.chains["2026-11-16"]
    k = strikes.pick({"rule": "delta", "target": 0.30}, "P", snap, c, {})
    d = abs(next(r["delta"] for r in c.rows if r["strike"] == k and r["right"] == "P"))
    assert abs(d - 0.30) < 0.04
    k2 = strikes.pick({"rule": "delta", "target": 0.10, "at_most": True}, "P", snap, c, {})
    d2 = abs(next(r["delta"] for r in c.rows if r["strike"] == k2 and r["right"] == "P"))
    assert d2 <= 0.10
    # break the invariant: without at_most the pick may exceed the cap, so the flag is what holds it
    k3 = strikes.pick({"rule": "delta", "target": 0.10}, "P", snap, c, {})
    assert abs(next(r["delta"] for r in c.rows if r["strike"] == k3 and r["right"] == "P")) >= d2 - 1e-9


def test_max_abs_gex_picks_among_the_nearest_only():
    snap = spx_snapshot()
    c = snap.chains["2026-11-16"]
    near = strikes.pick({"rule": "max_abs_gex", "strikes": 6}, "P", snap, c, {})
    assert abs(near - 6000.0) <= 15.0
    series = snap.gex["2026-11-16"]
    series[0]["abs_gex"] = 10**15  # a huge wall far away: out of the 6 nearest, never picked
    assert strikes.pick({"rule": "max_abs_gex", "strikes": 6}, "P", snap, c, {}) == near
    assert (
        strikes.pick({"rule": "max_abs_gex", "strikes": len(series)}, "P", snap, c, {}) == series[0]["strike"]
    )


# --- events ---------------------------------------------------------------------------------------
def test_rule_labels():
    assert events.rule_days("PMI-Mfg", 2026, 11) == [date(2026, 11, 2)]
    assert events.rule_days("PMI-Svcs", 2026, 11) == [date(2026, 11, 4)]
    assert events.rule_days("OPEX", 2026, 11) == [date(2026, 11, 20)]
    assert events.rule_days("FirstWeekly", 2026, 11) == [date(2026, 11, 6)]
    assert events.rule_days("MonthEnd", 2026, 10) == [date(2026, 10, 30)]  # the 31st is a Saturday
    assert events.rule_days("QuarterEnd", 2026, 12) == [date(2026, 12, 31)]
    assert events.rule_days("QuarterEnd", 2026, 11) == []
    assert events.rule_days("CPI", 2026, 11) is None


def test_occurs_over_a_span_and_unknown_is_not_quiet():
    cal = events.EventCalendar(data={"CPI": ["2026-11-18"]}, covered={"CPI": [("2026-01-01", "2026-12-31")]})
    assert cal.occurs("CPI", date(2026, 11, 17), date(2026, 11, 19)) is True
    assert cal.occurs("CPI", date(2026, 11, 19), date(2026, 11, 19)) is False
    assert cal.occurs("CPI", date(2027, 1, 4), date(2027, 1, 4)) is None  # outside what the data speaks for
    assert cal.occurs("Sentiment", date(2026, 11, 19), date(2026, 11, 19)) is None
    assert cal.occurs("OPEX", date(2026, 11, 19), date(2026, 11, 20)) is True
    assert cal.occurs("PMI-Mfg", date(2026, 11, 3), date(2026, 11, 3)) is False
