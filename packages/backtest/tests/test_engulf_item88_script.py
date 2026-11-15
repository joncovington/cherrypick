"""Item 88's study script (packages/backtest/scripts/engulf_item88.py): the sweep-and-engulf signal,
its levels and exits, one position at a time, the money, the pooled rows, the control's judgement, the
gold roll schedule and 4-hour bars. Prices are invented; no input file is read."""

from __future__ import annotations

import importlib.util
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.backtest.study import rule as kit_rule

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "packages" / "backtest" / "scripts" / "engulf_item88.py"
FETCH = ROOT / "scripts" / "fetch_item88_bars.py"
ET = ZoneInfo("America/New_York")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e88 = _load(SCRIPT, "engulf_item88")
STATED = e88.SPEC["rule"]
BARE = {"colour": "any", "ema": None, "stop": "candle", "r": 2.0}


def bar(o, h, low, c, day="2020-01-02"):
    return {"o": o, "h": h, "l": low, "c": c, "day": day}


def test_the_rule_is_declared_and_the_kit_accepts_it():
    assert kit_rule.check_rule(e88.RULE)
    assert e88.RULE["splits"] == [e88.CONTROL_RULE["split"]]
    assert e88.CONTROL_RULE["halves"] < e88.CONTROL_RULE["split"]
    assert STATED == {"colour": "same", "ema": 200, "stop": "candle", "r": 2.0}


def test_the_funds_are_the_fetch_scripts():
    assert e88.ETFS == _load(FETCH, "fetch_item88_bars").ETFS


def test_a_sweep_below_and_a_close_above_the_previous_high_is_long_and_the_mirror_short():
    prev = bar(10, 11, 9, 10.5)  # an up bar
    assert e88.signal(prev, bar(10.4, 11.6, 8.8, 11.2), None, BARE) == 1
    assert e88.signal(prev, bar(10.4, 11.6, 8.8, 10.9), None, BARE) is None  # no close above 11
    assert e88.signal(prev, bar(10.4, 11.2, 9.1, 11.1), None, BARE) is None  # no sweep below 9
    down = bar(10.5, 11, 9, 10)
    assert e88.signal(down, bar(10, 11.3, 8.9, 8.8), None, BARE) == -1


def test_same_colour_and_the_ema_side_filter_the_signal():
    up_prev, down_prev = bar(10, 11, 9, 10.5), bar(10.5, 11, 9, 10)
    long_bar = bar(10.4, 11.6, 8.8, 11.2)
    same = {**BARE, "colour": "same"}
    assert e88.signal(up_prev, long_bar, None, same) == 1
    assert e88.signal(down_prev, long_bar, None, same) is None  # a long after a down bar
    ema_rule = {**BARE, "ema": 200}
    assert e88.signal(up_prev, long_bar, 11.0, ema_rule) == 1
    assert e88.signal(up_prev, long_bar, 11.5, ema_rule) is None  # close under the EMA
    assert e88.signal(up_prev, long_bar, None, ema_rule) is None  # no EMA yet, no signal


def test_levels_by_candle_or_atr_and_no_trade_past_the_stop():
    sig = bar(10.4, 11.6, 8.8, 11.2)
    assert e88.levels(1, 11.0, sig, None, BARE) == (8.8, pytest.approx(11.0 + 2 * 2.2))
    assert e88.levels(-1, 11.0, sig, None, BARE) == (11.6, pytest.approx(11.0 - 2 * 0.6))
    assert e88.levels(1, 8.5, sig, None, BARE) is None  # opened under the long's stop
    atr_rule = {**BARE, "stop": ("atr", 2.0), "r": 1.5}
    assert e88.levels(1, 11.0, sig, 0.5, atr_rule) == (10.0, pytest.approx(12.5))
    assert e88.levels(1, 11.0, sig, None, atr_rule) is None


def test_the_stop_wins_a_tie_and_a_gap_fills_at_the_open():
    bars = [bar(10, 10.5, 9.5, 10), bar(10, 13, 8, 11), bar(12, 12, 11, 11.5), bar(7, 8, 6.5, 7)]
    assert e88.walk(1, 9.0, 12.5, bars, 1) == (1, 9.0, "stop")  # both touched in the entry bar
    assert e88.walk(1, 6.0, 12.5, bars, 2)[2] == "end"
    assert e88.walk(1, 7.5, 13.5, bars, 2) == (3, 7, "stop_gap")  # opened under the stop
    assert e88.walk(-1, 13.0, 11.5, bars, 2) == (2, 11.5, "target")
    assert e88.walk(1, 5.0, 11.9, [bar(11, 11.5, 10.8, 11.2), bar(12.2, 12.4, 12, 12.1)], 0) == (
        1,
        12.2,
        "target_gap",
    )


def test_money_charges_the_sell_fill_and_two_sides_of_slippage():
    long_ = e88.stock_money(1, 100, 50.0, 52.0, 0.01)
    assert long_["gross"] == pytest.approx(200.0) and long_["slippage"] == pytest.approx(2.0)
    assert long_["fees"] > 0 and long_["net"] == pytest.approx(200.0 - long_["fees"] - 2.0)
    short = e88.stock_money(-1, 100, 50.0, 52.0, 0.01)
    assert short["gross"] == pytest.approx(-200.0) and short["fees"] > 0  # the opening sell pays


def test_one_position_per_fund_and_skips_are_counted():
    flat = [bar(10, 10.4, 9.6, 10.2, f"2020-01-{i + 1:02d}") for i in range(3)]
    sig1 = bar(10.1, 10.8, 9.4, 10.7, "2020-01-04")  # long: stop 9.4
    held = bar(10.6, 10.9, 10.3, 10.5, "2020-01-05")  # entry at 10.6, target 13.0
    sig2 = bar(10.5, 11.2, 10.2, 11.1, "2020-01-06")  # a second signal while the first is held
    tail = [bar(11.1, 13.2, 11.0, 13.1, "2020-01-07")]
    trades, skipped = e88.run_fund("XYZ", [*flat, sig1, held, sig2, *tail], BARE)
    assert len(trades) == 1 and skipped["in_trade"] == 1
    t = trades[0]
    assert t["date"] == "2020-01-05" and t["entry"] == 10.6 and t["why"] == "target"
    assert t["exit"] == pytest.approx(13.0) and t["exit_date"] == "2020-01-07"
    assert t["bp"] == t["shares"] * t["entry"] and t["days"] == 2


def test_pooled_rows_charge_each_trades_own_bill():
    trades = [
        {"date": "2020-01-02", "net": 10.0, "bp": 1000.0, "days": 3},
        {"date": "2020-01-02", "net": -4.0, "bp": 3000.0, "days": 10},
        {"date": "2020-01-09", "net": 7.0, "bp": 2000.0, "days": 1},
    ]
    rows = e88.session_rows(trades, lambda d: 0.05)
    assert [r["trades"] for r in rows] == [2, 1]
    first = rows[0]
    pooled = first["bp"] * first["rate"] * first["days"] / 365
    each = sum(t["bp"] * 0.05 * t["days"] / 365 for t in trades[:2])
    assert first["net"] == 6.0 and pooled == pytest.approx(each)


def test_the_control_needs_the_design_mean_t_and_both_halves_then_the_holdout():
    rule = {**e88.CONTROL_RULE, "min_n": 4, "t_bar": 2.0, "split": "2016-01-01", "halves": "2008-01-01"}
    good = [{"date": f"200{y}-03-01", "diff": 5 + y % 2} for y in range(2, 10)]
    good += [{"date": "2018-01-01", "diff": 1.0}]
    assert e88.judge_control(good, rule)["verdict"] == "pass"
    one_half = [{"date": f"200{y}-03-01", "diff": (6 if y >= 8 else -1)} for y in range(2, 10)]
    assert e88.judge_control(one_half, rule)["verdict"] == "fail"
    assert e88.judge_control(good[:3], rule)["verdict"] == "no verdict"
    assert e88.both("pass", "fail") == "fail" and e88.both("pass", "no verdict") == "no verdict"


def test_a_placebo_keeps_the_fund_the_stop_in_atr_units_and_the_r():
    bars = [bar(10 + i * 0.01, 10.5 + i * 0.01, 9.5 + i * 0.01, 10 + i * 0.01) for i in range(40)]
    atr = e88.atr_series(bars, 14)
    eligible = list(range(20, 38))
    nets = {e88.placebo(bars, atr, eligible, 1, 1.0, BARE, random.Random(s)) for s in range(5)}
    assert all(n is not None for n in nets)


def test_the_front_rolls_to_the_volume_leader_forward_only_from_the_start():
    daily = {
        "/GCJ26:XCEC": {"2026-03-26": 900, "2026-03-27": 400, "2026-03-30": 100, "2026-03-31": 50},
        "/GCM26:XCEC": {"2026-03-26": 300, "2026-03-27": 500, "2026-03-30": 800, "2026-03-31": 900},
        "/GCZ27:XCEC": {"2025-01-02": 5, "2026-03-26": 1, "2026-03-27": 1, "2026-03-30": 1, "2026-03-31": 1},
        "/GCH26:XCEC": {"2026-03-26": 10_000},  # off the cycle: never the front
    }
    front = e88.front_schedule(daily, start="2026-01-01")
    assert "2025-01-02" not in front
    # M26 leads at the 03-27 close, so it is the front from the next session
    assert front == {"2026-03-26": "/GCJ26:XCEC", "2026-03-27": "/GCJ26:XCEC",
                     "2026-03-30": "/GCM26:XCEC", "2026-03-31": "/GCM26:XCEC"}  # fmt: skip
    back = {**daily, "/GCJ26:XCEC": {**daily["/GCJ26:XCEC"], "2026-03-31": 5000}}
    assert e88.front_schedule(back, start="2026-01-01")["2026-03-31"] == "/GCM26:XCEC"  # never back


def _ms(y, m, d, hh, tz=ET):
    return int(datetime(y, m, d, hh, tzinfo=tz).timestamp() * 1000)


def test_four_hour_bars_start_on_utc_hours_or_at_the_1800_session_open():
    from datetime import UTC

    t = _ms(2026, 7, 14, 13, UTC) + 30 * 60_000
    assert e88.bucket(t, "utc") == _ms(2026, 7, 14, 12, UTC)
    # summer: 18:00 ET opens a bar, so 21:00 belongs to it and 22:00 opens the next
    assert e88.bucket(_ms(2026, 7, 14, 21), "session") == _ms(2026, 7, 14, 18)
    assert e88.bucket(_ms(2026, 7, 14, 22), "session") == _ms(2026, 7, 14, 22)
    # winter: the same ET hours, whatever the UTC offset
    assert e88.bucket(_ms(2026, 1, 14, 21), "session") == _ms(2026, 1, 14, 18)
    assert (
        e88.trade_day(_ms(2026, 7, 14, 19)) == "2026-07-15"
        and e88.trade_day(_ms(2026, 7, 14, 16)) == "2026-07-14"
    )


def test_four_hour_bars_never_span_a_contract_change():
    hours = [
        {"ts": _ms(2026, 7, 14, 18) + k * 3_600_000, "o": 10 + k, "h": 11 + k, "l": 9 + k, "c": 10.5 + k,
         "contract": "/GCQ26:XCEC" if k < 2 else "/GCZ26:XCEC"}
        for k in range(4)
    ]  # fmt: skip
    bars = e88.four_hour(hours, "session")
    assert [b["contract"] for b in bars] == ["/GCQ26:XCEC", "/GCZ26:XCEC"]
    assert bars[0]["h"] == 12 and bars[0]["c"] == 11.5 and bars[1]["o"] == 12


def test_a_gold_signal_across_a_roll_is_skipped_and_a_trade_open_at_a_roll_closes():
    rule = {**BARE}
    a, b = "/GCQ26:XCEC", "/GCZ26:XCEC"
    bars = [
        {**bar(10, 10.4, 9.6, 10.2), "contract": a},
        {**bar(10.1, 10.8, 9.4, 10.7), "contract": a},  # a long signal
        {**bar(10.7, 10.9, 10.5, 10.8), "contract": a},  # entry bar, no exit
        {**bar(20.0, 21.0, 8.0, 20.9), "contract": b},  # a "signal" against the old contract's bar
        {**bar(20.9, 21.0, 20.5, 20.6), "contract": b},
    ]
    trades, skipped = e88.run_gold(bars, rule)
    assert len(trades) == 1 and trades[0]["why"] == "roll" and skipped["roll"] == 1
