"""Item 82's script (meic_wide_first_hour_item82.py): invented trades and sessions, the stores never read."""

from __future__ import annotations

import importlib.util
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "meic_wide_first_hour_item82",
    Path(__file__).resolve().parents[1] / "scripts" / "meic_wide_first_hour_item82.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ET = ZoneInfo("America/New_York")


def test_the_rules_are_complete_and_the_chain_is_one_study_row():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["delta"] is False  # a condor is a neutral structure
    g = m.GATE_RULE
    assert math.isclose(g["skip_w_at_or_above"], math.log(1.5), abs_tol=1e-6)
    assert g["split"] == m.RULE["splits"][0] and g["halves"] < g["split"]
    assert set(m.RUNS) == {"design", "holdout"}
    assert [s["step"] for s in m.CHAIN] == ["study"]


def _session(day: date, prices: list[float]) -> list[tuple[float, float]]:
    start = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).timestamp()
    return [(start + 60 * i, p) for i, p in enumerate(prices)]


def test_w_needs_20_prior_first_hours_and_is_the_log_ratio_to_their_median():
    sessions, d = {}, date(2024, 1, 1)
    i = 0
    while len(sessions) < 22:
        if d.weekday() < 5:
            width = 4.0 if len(sessions) < 21 else 6.0  # the last day's first hour is 1.5x the others
            path = [5000.0 + (width if k % 2 else 0.0) for k in range(59)] + [5000.0] * 330
            sessions[d.isoformat()] = _session(d, path)
            i += 1
        d += timedelta(days=1)
    w = m.first_hour_widths(sessions, m.GATE_RULE)
    days = sorted(sessions)
    assert list(w) == [days[-1]]  # day 0 has no prior close; days 1-20 build the 20 first hours
    assert math.isclose(w[days[-1]], math.log(1.5), rel_tol=1e-9)


def test_the_gap_is_kept_mean_less_skipped_mean():
    trades = [{"date": "a", "net": 10.0}, {"date": "b", "net": 20.0}, {"date": "c", "net": -30.0}]
    assert m.gap(trades, {"c"}) == 15.0 - (-30.0)
    assert m.gap(trades, set()) is None


def _trades(skip_effect: float, n: int = 800, seed: int = 5):
    rng = random.Random(seed)
    trades, w, d = [], {}, date(2022, 6, 1)
    for _ in range(n):
        ds = d.isoformat()
        w[ds] = rng.gauss(0, 0.4)
        wide = w[ds] >= m.GATE_RULE["skip_w_at_or_above"]
        trades.append(
            {
                "date": ds,
                "net": rng.gauss(60, 150) - (skip_effect if wide else 0.0),
                "bp": 800.0,
                "days": 1,
                "rate": 0.04,
            }
        )
        d += timedelta(days=2)
    return trades, w


def test_losing_wide_days_pass_the_gate_and_read_the_holdout():
    trades, w = _trades(250.0)
    block = m.judge_gate(trades, w, m.GATE_RULE, draws=500)
    assert block["design"]["verdict"] == "pass", block["design"]
    assert block["holdout"]["verdict"] == "pass"


def test_no_effect_fails_the_gate_and_never_reads_the_holdout():
    trades, w = _trades(0.0)
    block = m.judge_gate(trades, w, m.GATE_RULE, draws=500)
    assert block["design"]["verdict"] == "fail"
    assert block["holdout"]["verdict"] == "not read"


def test_wide_days_that_lose_less_than_others_but_still_earn_fail_the_zero_check():
    trades, w = _trades(50.0)  # skipped days still average about +10: skipping gives up money
    block = m.judge_gate(trades, w, m.GATE_RULE, draws=500)
    assert block["design"]["checks"]["skipped_below_zero"] is False
    assert block["verdict"] == "fail"


def test_too_few_skipped_days_is_no_verdict():
    trades, w = _trades(250.0, n=150)
    assert m.judge_gate(trades, w, m.GATE_RULE, draws=50)["verdict"] == "no verdict"


def test_the_gated_arm_drops_wide_days_and_days_without_a_w():
    trades = [{"date": "a", "net": 1.0}, {"date": "b", "net": 2.0}, {"date": "c", "net": 3.0}]
    assert [t["date"] for t in m.gated(trades, {"a": 0.0, "b": 0.5}, 0.405465)] == ["a"]


def test_the_permutation_p_is_small_for_a_planted_gap_and_large_for_none():
    trades, w = _trades(250.0)
    skip = {d for d, v in w.items() if v >= m.GATE_RULE["skip_w_at_or_above"]}
    assert m.permutation_p(trades, skip, draws=500, seed=1) < 0.01
    trades0, w0 = _trades(0.0)
    skip0 = {d for d, v in w0.items() if v >= m.GATE_RULE["skip_w_at_or_above"]}
    assert m.permutation_p(trades0, skip0, draws=500, seed=1) > 0.05


def test_a_condors_buying_power_is_the_wider_wing_less_its_credit():
    row = {
        "put_wing": 10.0,
        "call_wing": 15.0,
        "wing_width": 10.0,
        "net_credit": 2.0,
        "dollar_multiplier": 100.0,
        "quantity": 2,
    }
    assert m.condor_bp([row]) == (15.0 - 2.0) * 100.0 * 2
    assert m.condor_bp([{**row, "net_credit": None}]) is None
