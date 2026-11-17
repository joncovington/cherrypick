"""Item 89's study script (packages/backtest/scripts/trend_indicators_item89.py): the three indicators'
states, stop-and-reverse and retest trades, the video's flip-back measure, the money, the pooled rows
and the control. Prices are invented; no input file is read."""

from __future__ import annotations

import importlib.util
import math
import random
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "packages" / "backtest" / "scripts" / "trend_indicators_item89.py"
FETCH = ROOT / "scripts" / "fetch_item88_bars.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


t89 = _load(SCRIPT, "trend_indicators_item89")


def bar(o, h, low, c, day):
    return {"o": o, "h": h, "l": low, "c": c, "day": day}


def days(n):
    """n invented session dates, one a calendar day apart from 2001-01-01."""
    from datetime import date, timedelta

    return [(date(2001, 1, 1) + timedelta(days=k)).isoformat() for k in range(n)]


def path_bars(closes):
    """Bars whose open is the previous close and whose range is a point either side."""
    ds = days(len(closes))
    out, prev = [], closes[0]
    for c, d in zip(closes, ds, strict=True):
        out.append(bar(prev, max(prev, c) + 1, min(prev, c) - 1, c, d))
        prev = c
    return out


def wave(n, period=120, amp=30.0, base=200.0):
    return [base + amp * math.sin(2 * math.pi * k / period) for k in range(n)]


def test_the_rule_is_declared_and_the_kit_accepts_it():
    assert kit_rule.check_rule(t89.RULE)
    assert t89.RULE["splits"] == [t89.CONTROL_RULE["split"]]
    assert t89.CONTROL_RULE["halves"] < t89.CONTROL_RULE["split"]
    assert t89.RULE["t_bar"] == t89.CONTROL_RULE["t_bar"] and t89.RULE["delta"] is False
    assert t89.ARMS == ("zlts", "gpr", "zltl")


def test_the_settings_are_the_makers_defaults():
    assert t89.SPEC["zlts"] == {"length": 70, "mult": 1.2}
    assert t89.SPEC["gpr"] == {"window": 100, "sigma": 0.01, "mult": 2.0, "forecast": 0}
    assert t89.SPEC["zltl"] == {"length": 15, "atr": 200}


def test_the_funds_are_in_item_88s_fetch():
    fetched = set(_load(FETCH, "fetch_item88_bars").ETFS)
    assert set(t89.FUNDS) | set(t89.OTHER_FUNDS) == fetched
    assert not set(t89.FUNDS) & set(t89.OTHER_FUNDS)


def test_crosses_are_pines():
    assert t89.crossed_above(1.0, 2.0, 1.5, 1.5) and not t89.crossed_above(2.0, 3.0, 1.5, 1.5)
    assert t89.crossed_below(2.0, 1.0, 1.5, 1.5) and not t89.crossed_above(None, 2.0, 1.5, 1.5)


def test_highest_needs_a_full_window():
    assert t89.highest([1, 3, None, 2, 5, 4], 2) == [None, 3, None, None, 5, 5]


def test_the_gp_weights_are_a_fixed_filter_summing_to_about_one_and_leaning_on_the_last_bars():
    w = t89.gpr_weights(100, 0.01, 0)
    assert len(w) == 100 and sum(w) == pytest.approx(1.0, abs=0.01)
    assert w[-1] == max(w) and sum(w[-20:]) > 0.9
    # a straight line is followed exactly: the fit recovers a trend's last value
    line = [2.0 * k for k in range(100)]
    m = sum(line) / 100
    assert m + sum(a * (x - m) for a, x in zip(w, line, strict=True)) == pytest.approx(line[-1], abs=0.5)


def test_cholesky_solves_a_small_system():
    x = t89.cholesky_solve([[4.0, 2.0], [2.0, 3.0]], [2.0, 1.0])
    assert x == pytest.approx([0.5, 0.0])


def test_each_indicator_turns_up_in_a_rally_and_down_in_a_fall():
    # the zero-lag band needs ATR(70) then 210 of them: 280 bars before it exists, as in Pine
    closes = [100.0] * 400 + [100.0 + 2 * k for k in range(1, 81)] + [260.0 - 2 * k for k in range(1, 121)]
    bars = path_bars(closes)
    zl = t89.zlts_states(bars, 70, 1.2)
    gp = t89.gpr_states(bars, 100, 0.01, 2.0, 0)
    for states in (zl, gp):
        assert states[475] == 1 and states[-1] == -1
    ev = t89.zltl_events(bars, 15, 200)
    assert ev[440]["trend"] == 1 and ev[-1]["trend"] == -1


def test_the_gp_state_needs_the_line_to_slope_its_way():
    # a close well above a line that still falls is not an up turn (a large jump would turn the line)
    closes = [300.0 - k for k in range(250)]
    closes.append(closes[-1] - 1 + 5)
    bars = path_bars(closes)
    states = t89.gpr_states(bars, 100, 0.01, 2.0, 0)
    assert states[-1] != 1


def test_stop_and_reverse_enters_each_turn_at_the_next_open_and_skips_a_state_set_before_the_warm_up():
    bars = path_bars([100.0 + k for k in range(12)])
    states = [1, 1, 1, 1, -1, -1, 1, 1, 1, -1, -1, -1]
    trades = t89.reversal_trades("XYZ", bars, states, 2, t89.SPEC)
    assert [(t["side"], t["date"], t["exit_date"]) for t in trades] == [
        (-1, bars[5]["day"], bars[7]["day"]),
        (1, bars[7]["day"], bars[10]["day"]),
        (-1, bars[10]["day"], bars[11]["day"]),
    ]
    assert trades[0]["entry"] == bars[5]["o"] and trades[0]["exit"] == bars[7]["o"]
    assert trades[-1]["why"] == "end" and trades[-1]["exit"] == bars[-1]["c"]
    longs = t89.reversal_trades("XYZ", bars, states, 2, t89.SPEC, long_only=True)
    assert [t["side"] for t in longs] == [1]


def test_a_retest_is_entered_and_ends_on_a_break_or_the_next_cross():
    bars = path_bars([100.0 + k for k in range(10)])
    base = {"trend": 1, "cross": False, "retest": False, "broken": False}
    ev = [dict(base) for _ in bars]
    ev[2]["retest"] = True
    ev[3]["retest"] = True  # held: counted, not taken
    ev[5]["broken"] = True
    ev[6] = {**base, "trend": -1, "cross": True}
    ev[7] = {**base, "trend": -1, "retest": True}
    trades, skipped = t89.retest_trades("XYZ", bars, ev, 1, t89.SPEC)
    assert [(t["side"], t["date"], t["exit_date"]) for t in trades] == [
        (1, bars[3]["day"], bars[6]["day"]),
        (-1, bars[8]["day"], bars[9]["day"]),
    ]
    assert skipped["in_trade"] == 1
    _, sk = t89.retest_trades("XYZ", bars, ev, 1, t89.SPEC, long_only=True)
    assert sk["short_flat"] == 1


def test_an_up_level_is_retested_when_price_leaves_it_and_broken_on_a_close_under_it():
    closes = [100.0] * 220 + [100.0 + 3 * k for k in range(1, 15)]
    bars = path_bars(closes)
    ev = t89.zltl_events(bars, 15, 200)
    cross = next(i for i in range(len(ev)) if ev[i]["cross"] and ev[i]["trend"] == 1)
    assert not any(e["retest"] for e in ev[: cross + 1])
    # a dip into the level then a bar whose low clears its top is a retest; a close under it breaks it
    ev2 = t89.zltl_events(
        bars[: cross + 1]
        + [
            bar(bars[cross]["c"], bars[cross]["c"] + 1, 0.0, bars[cross]["c"], "2099-01-01"),
            bar(1e4, 1e4 + 1, 1e4 - 1, 1e4, "2099-01-02"),
        ],  # fmt: skip
        15,
        200,
    )
    assert ev2[-1]["retest"]
    # a collapse ends the level either way: broken, or a down cross that replaces it
    ev3 = t89.zltl_events(bars[: cross + 1] + [bar(1.0, 2.0, 0.5, 1.0, "2099-01-01")], 15, 200)
    assert ev3[-1]["broken"] or (ev3[-1]["cross"] and ev3[-1]["trend"] == -1)


def test_flip_back_counts_an_opposite_signal_or_an_end_within_n_bars():
    sig = [(10, 1), (12, -1), (30, 1), (50, -1)]
    assert t89.flip_back(sig, [], 5) == {"signals": 4, "flip_back_share": 0.25}
    assert t89.flip_back([(10, 1)], [14], 5)["flip_back_share"] == 1.0
    assert t89.flip_back([], [], 5) == {"signals": 0}
    assert t89.turns([0, 0, 1, 1, -1, -1, 1], 1) == [(2, 1), (4, -1), (6, 1)]


def test_money_charges_the_sell_fill_and_two_sides_of_slippage():
    long_ = t89.stock_money(1, 100, 50.0, 52.0, 0.01)
    assert long_["gross"] == pytest.approx(200.0) and long_["slippage"] == pytest.approx(2.0)
    assert long_["fees"] > 0 and long_["net"] == pytest.approx(200.0 - long_["fees"] - 2.0)
    short = t89.stock_money(-1, 100, 50.0, 52.0, 0.01)
    assert short["gross"] == pytest.approx(-200.0) and short["fees"] > 0


def test_pooled_rows_charge_each_trades_own_bill():
    trades = [
        {"date": "2020-01-02", "net": 10.0, "bp": 1000.0, "days": 3},
        {"date": "2020-01-02", "net": -4.0, "bp": 3000.0, "days": 10},
        {"date": "2020-01-09", "net": 7.0, "bp": 2000.0, "days": 1},
    ]
    rows = t89.session_rows(trades, lambda d: 0.05)
    assert [r["trades"] for r in rows] == [2, 1]
    first = rows[0]
    pooled = first["bp"] * first["rate"] * first["days"] / 365
    assert first["net"] == 6.0 and pooled == pytest.approx(
        sum(t["bp"] * 0.05 * t["days"] / 365 for t in trades[:2])
    )


def test_the_control_needs_the_design_mean_t_and_both_halves_then_the_holdout():
    rule = {**t89.CONTROL_RULE, "min_n": 4, "t_bar": 2.0}
    good = [{"date": f"200{y}-03-01", "diff": 5 + y % 2} for y in range(2, 10)]
    good += [{"date": "2018-01-01", "diff": 1.0}]
    assert t89.judge_control(good, rule)["verdict"] == "pass"
    one_half = [{"date": f"200{y}-03-01", "diff": (6 if y >= 8 else -1)} for y in range(2, 10)]
    assert t89.judge_control(one_half, rule)["verdict"] == "fail"
    assert t89.judge_control(good[:3], rule)["verdict"] == "no verdict"
    assert t89.both("pass", "fail") == "fail" and t89.both("pass", "no verdict") == "no verdict"


def test_placebos_draw_side_and_hold_from_the_arm_never_the_trade(monkeypatch):
    bars = path_bars(wave(600))
    funds = {"XYZ": bars}
    trades = t89.reversal_trades("XYZ", bars, t89.zlts_states(bars, 70, 1.2), 250, t89.SPEC)
    assert trades
    seen = []
    real = t89.placebo

    def spy(b, el, side, hold, rng):
        seen.append((side, hold))
        return real(b, el, side, hold, rng)

    monkeypatch.setattr(t89, "placebo", spy)
    diffs = t89.control_diffs(trades, funds, "2099-01-01")
    assert diffs and len(seen) == len(trades) * t89.CONTROL_RULE["placebos"]
    holds = {max(1, t["bars"]) for t in trades}
    assert {h for _, h in seen} <= holds and {s for s, _ in seen} <= {1, -1}
    assert t89.control_diffs(trades, funds, "2099-01-01") == diffs  # seeded: the same draws again


def test_a_placebo_is_held_its_bars_and_left_at_an_open():
    bars = path_bars([100.0 + k for k in range(40)])
    p = t89.placebo(bars, [10], 1, 5, random.Random(1))
    assert p["entry"] == bars[11]["o"] and p["exit"] == bars[16]["o"] and p["bars"] == 5


def test_study_writes_a_verdict_per_arm_on_invented_bars():
    def walk(seed, n=1200):
        rng, x, out = random.Random(seed), 100.0, []
        for _ in range(n):
            x = max(5.0, x + rng.gauss(0.02, 1.5))
            out.append(x)
        return path_bars(out)

    funds = {s: walk(k) for k, s in enumerate(t89.FUNDS)}
    others = {"EFA": walk(99)}
    out = t89.study(funds, others, lambda d: 0.02)
    assert set(out["verdict"]) == set(t89.ARMS)
    assert set(out["reported"]["baselines"]) == set(t89.BASELINES)
    assert set(out["control"]) == set(t89.ARMS)
    assert all(out["reported"]["arms"][a]["arm"]["design"]["trades"] > 0 for a in t89.ARMS)
