"""Item 83's script (ema_vwap_cross_item83.py): invented sessions and quotes only, the store never read."""

from __future__ import annotations

import importlib.util
import random
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "ema_vwap_cross_item83", Path(__file__).resolve().parents[1] / "scripts" / "ema_vwap_cross_item83.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ET = ZoneInfo("America/New_York")
DAY = date(2024, 3, 4)


def _session(prices: list[float], day: date = DAY) -> list[tuple[float, float]]:
    start = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).timestamp()
    return [(start + 60 * i, p) for i, p in enumerate(prices)]


def _quotes(samples: list[tuple[float, float]], *, extra: float = 2.0) -> dict[float, dict]:
    """A toy chain each stamp: 5-point strikes around spot, delta linear in moneyness, mid = intrinsic
    + `extra`, a 0.10 wide market."""
    out = {}
    for ts, spot in samples:
        q = {}
        for k in range(int(spot) // 5 * 5 - 50, int(spot) // 5 * 5 + 55, 5):
            dc = min(max(0.5 + (spot - k) / 40.0, 0.01), 0.99)
            for side, delta, intr in ((m.CALL, dc, max(spot - k, 0)), (m.PUT, dc - 1.0, max(k - spot, 0))):
                mid = intr + extra
                q[(side, float(k))] = {"bid": mid - 0.05, "ask": mid + 0.05, "delta": delta}
        out[ts] = q
    return out


def _prepared(prices: list[float]):
    samples = _session(prices)
    bars = m.bars_of(samples, m.SPEC)
    m.annotate(bars, None, m.SPEC["ema"])
    quotes = _quotes(samples)
    return samples, bars, sorted(quotes), quotes, dict(samples)


def test_the_rules_are_complete_and_agree():
    assert kit_rule.check_rule(m.RULE)
    assert m.RULE["delta"] is False and m.RULE["t_bar"] >= 2.24
    c = m.CONTROL_RULE
    assert c["split"] == m.RULE["splits"][0] and c["halves"] < c["split"] and c["t_bar"] == m.RULE["t_bar"]
    assert set(m.SPEC["arms"]) == {"allday", "first60"}
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_bars_are_three_stamps_from_0930_closing_at_their_last():
    s = _session([100.0 + i for i in range(389)])
    bars = m.bars_of(s, m.SPEC)
    assert len(bars) == 130  # 129 whole bars and 15:58-15:59
    b = bars[0]
    assert (b["open"], b["high"], b["low"], b["close"]) == (100.0, 102.0, 100.0, 102.0)
    assert b["ts"] == s[2][0] and b["ohlc4"] == (100 + 102 + 100 + 102) / 4


def test_vwap_is_the_equal_weight_running_mean_and_the_ema_carries_in():
    bars = m.bars_of(_session([100.0] * 3 + [110.0] * 3), m.SPEC)
    last = m.annotate(bars, 90.0, 9)
    assert bars[0]["vwap"] == 100.0 and bars[1]["vwap"] == 105.0
    assert bars[0]["ema"] == 90.0 + 0.2 * (100.0 - 90.0)
    assert last == bars[1]["ema"]


def test_a_cross_is_read_both_ways_and_never_on_a_touch():
    up = m.cross({"ema": 1.0, "vwap": 2.0}, {"ema": 3.0, "vwap": 2.0})
    down = m.cross({"ema": 3.0, "vwap": 2.0}, {"ema": 1.0, "vwap": 2.0})
    assert (up, down) == (m.CALL, m.PUT)
    assert m.cross({"ema": 1.0, "vwap": 2.0}, {"ema": 2.0, "vwap": 2.0}) is None


def _v(n_flat: int, rise: int, fall: int) -> list[float]:
    px = [5000.0] * n_flat
    px += [px[-1] + 1.0 * (i + 1) for i in range(rise)]
    px += [px[-1] - 1.0 * (i + 1) for i in range(fall)]
    return px + [px[-1]] * (389 - len(px))


def test_an_up_cross_buys_the_call_at_the_next_stamp_and_a_close_under_the_ema_exits():
    samples, bars, stamps, quotes, spot = _prepared(_v(30, 60, 60))
    trades, _ = m.day_trades(bars, stamps, quotes, spot, m.SPEC, last_signal=m.hhmm("15:30"), stop_frac=None)
    t = trades[0]
    assert t["side"] == "call"
    signal = next(b for i, b in enumerate(bars[1:], 1) if m.cross(bars[i - 1], b) == m.CALL)
    assert t["entry_ts"] == m.next_after(stamps, signal["ts"]) > signal["ts"]  # no look-ahead
    assert t["why"] == "trend" and t["points"] > 0


def test_the_stop_fires_on_the_mid_at_seventy_percent():
    samples, bars, stamps, quotes, spot = _prepared(_v(30, 60, 0))
    t0 = stamps[100]
    q0 = {"bid": 9.95, "ask": 10.05, "delta": 0.5}
    marks = {t: {(m.CALL, 5000.0): {"bid": 9.95, "ask": 10.05, "delta": 0.5}} for t in stamps}
    marks[stamps[110]] = {(m.CALL, 5000.0): {"bid": 6.95, "ask": 7.05, "delta": 0.3}}  # mid 7.00
    out = m.hold(
        m.CALL, 5000.0, q0, t0, stamps, marks, spot, stop_frac=0.30, exit_at=stamps[-1], trend_exits=set()
    )
    assert out["why"] == "stop" and out["exit_ts"] == stamps[110]
    marks[stamps[110]] = {(m.CALL, 5000.0): {"bid": 7.00, "ask": 7.10, "delta": 0.3}}  # mid 7.05: above
    out = m.hold(
        m.CALL, 5000.0, q0, t0, stamps, marks, spot, stop_frac=0.30, exit_at=stamps[-1], trend_exits=set()
    )
    assert out["why"] == "time"


def test_signals_after_the_arms_window_are_not_taken():
    samples, bars, stamps, quotes, spot = _prepared(_v(150, 60, 60))  # the cross comes after 10:30
    trades, skipped = m.day_trades(
        bars, stamps, quotes, spot, m.SPEC, last_signal=m.hhmm("10:30"), stop_frac=0.3
    )
    assert trades == [] and skipped["late"] >= 1


def test_a_trade_pays_the_suites_costs_both_ways():
    samples, bars, stamps, quotes, spot = _prepared(_v(30, 60, 60))
    t = m.day_trades(bars, stamps, quotes, spot, m.SPEC, last_signal=m.hhmm("15:30"), stop_frac=None)[0][0]
    q = {"bid": 1.0, "ask": 1.1}
    each_way = m.fees.slippage_dollars([q], 1, {}, symbol="SPX")
    assert t["costs"] > 2 * each_way > 0


def test_the_placebo_keeps_side_and_minutes_and_enters_inside_the_window():
    samples, bars, stamps, quotes, spot = _prepared(_v(30, 60, 60))
    t = m.day_trades(bars, stamps, quotes, spot, m.SPEC, last_signal=m.hhmm("15:30"), stop_frac=None)[0][0]
    rng = random.Random(1)
    p = m.placebo(t, stamps, quotes, spot, m.SPEC, first_entry=stamps[5], last_entry=stamps[60], rng=rng,
                  stop_frac=None)  # fmt: skip
    assert p["side"] == t["side"] and p["minutes"] == t["minutes"]
    assert stamps[5] <= p["entry_ts"] <= stamps[60]


def _diffs(values: list[float], start: date = date(2022, 6, 1)) -> list[dict]:
    out, d = [], start.toordinal()
    for v in values:
        out.append({"date": date.fromordinal(d).isoformat(), "diff": v})
        d += 2
    return out


def test_the_control_needs_its_floor_then_both_halves():
    c = {**m.CONTROL_RULE, "min_n": 50}
    assert m.judge_control(_diffs([1.0] * 10), c)["verdict"] == m.NO_VERDICT
    rng = random.Random(3)
    good = m.judge_control(_diffs([5 + rng.gauss(0, 1) for _ in range(500)]), c)
    assert good["design"]["verdict"] == m.PASS
    # Negative in the first half: the design fails and the holdout is never read.
    bad = _diffs([-5 + rng.gauss(0, 1) for _ in range(220)] + [20 + rng.gauss(0, 1) for _ in range(280)])
    out = m.judge_control(bad, c)
    assert out["design"]["checks"]["halves"] is False and out["holdout"]["verdict"] == m.NOT_READ


def test_an_arm_passes_only_on_both_rules():
    assert m.both(m.PASS, m.PASS) == m.PASS
    assert m.both(m.PASS, m.FAIL) == m.both(m.FAIL, m.NO_VERDICT) == m.FAIL
    assert m.both(m.PASS, m.NO_VERDICT) == m.NO_VERDICT
