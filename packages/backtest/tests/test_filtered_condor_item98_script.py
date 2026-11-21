"""Item 98's script (filtered_condor_item98.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "filtered_condor_item98", Path(__file__).resolve().parents[1] / "scripts" / "filtered_condor_item98.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == [m.FILTER_RULE["split"]]
    assert m.FILTER_RULE["halves"] < m.FILTER_RULE["split"]


def test_the_chain_smokes_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["smoke", "study"]


def test_every_arm_is_a_valid_automation():
    from cherrypick.automations import arms

    for a in m.ARMS.values():
        assert arms.validate(a) == [], a["name"]


def test_the_judged_arm_is_the_source_as_stated():
    a = m.ARMS["ic60"]
    assert a["entry"] == {"kind": "time", "at": "10:30", "window": 1}
    assert a["exit"] == {"profit_target_pct": 75.0}
    assert a["skip_events"]["labels"] == ["MonthEnd", "PMI-Mfg"]
    strikes = {g["name"]: g["strike"] for g in a["legs"]}
    assert strikes["short_put"]["points"] == -60.0 and strikes["short_call"]["points"] == 60.0
    assert strikes["long_put"]["points"] == -15.0 and strikes["long_call"]["points"] == 15.0
    assert [g["gate"] for g in a["gates"]] == ["vix_level", "change_pct", "open_change_pct", "reward_risk"]


def test_the_every_day_arm_drops_only_the_filters_and_the_event_skips():
    a, e = m.ARMS["ic60"], m.ARMS["ic60-every-day"]
    assert e["gates"] == [m.CREDIT_FLOOR] and "skip_events" not in e
    for k in ("legs", "entry", "exit", "size", "limits", "expiry", "weekdays"):
        assert e[k] == a[k]


def test_the_hold_arm_drops_only_the_target():
    a, h = m.ARMS["ic60"], m.ARMS["ic60-hold"]
    assert h["exit"] == {}
    for k in ("legs", "entry", "gates", "skip_events", "size", "limits", "expiry", "weekdays"):
        assert h[k] == a[k]


def _trade(legs, credit, qty=1):
    return {"legs": legs, "entry_mid": credit, "quantity": qty}


def test_max_loss_is_the_widest_wing_less_the_credit():
    condor = [
        {"right": "P", "side": -1, "strike": 5940.0},
        {"right": "P", "side": 1, "strike": 5925.0},
        {"right": "C", "side": -1, "strike": 6060.0},
        {"right": "C", "side": 1, "strike": 6075.0},
    ]
    assert m.max_loss_dollars(_trade(condor, 1.1)) == 1390.0


def test_position_delta_signs_each_leg_and_refuses_a_missing_one():
    legs = [{"right": "P", "side": -1, "strike": 5940.0}, {"right": "P", "side": 1, "strike": 5925.0}]
    deltas = {(5940.0, "P"): -0.05, (5925.0, "P"): -0.03}
    assert m.position_delta(_trade(legs, 0.5), deltas) == pytest.approx(2.0)
    assert m.position_delta(_trade(legs, 0.5), {(5940.0, "P"): -0.05}) is None


def _days(start, n):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _rows(dates, net):
    return [{"date": d, "net": net(i)} for i, d in enumerate(dates)]


def test_skipped_days_are_the_every_day_trades_off_the_kept_sessions():
    kept = _rows(["2024-01-02", "2024-01-04"], lambda i: 1.0)
    every = _rows(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"], lambda i: 2.0)
    assert [r["date"] for r in m.skipped(kept, every)] == ["2024-01-03", "2024-01-05"]


def test_the_filter_passes_kept_days_clearly_above_skipped_days_in_every_part():
    design = _days("2022-06-01", 200) + _days("2023-11-01", 200)
    holdout = _days("2025-08-01", 100)
    kept = _rows(design + holdout, lambda i: 60.0 + (i % 7))
    skip = _rows(design + holdout, lambda i: -5.0 + (i % 5))
    got = m.judge_filter(kept, skip, m.FILTER_RULE)
    assert got["verdict"] == m.PASS and got["holdout"]["verdict"] == m.PASS


def test_the_filter_fails_kept_days_above_skipped_days_in_one_half_only():
    first, second = _days("2022-06-01", 200), _days("2023-11-01", 200)
    kept = _rows(first, lambda i: 80.0 + (i % 3)) + _rows(second, lambda i: -10.0 + (i % 3))
    skip = _rows(first + second, lambda i: 0.0 + (i % 3))
    got = m.judge_filter(kept, skip, m.FILTER_RULE)
    assert got["verdict"] == m.FAIL and got["holdout"] == {"verdict": m.NOT_READ}


def test_the_filter_gives_no_verdict_under_its_design_floor():
    kept = _rows(_days("2022-06-01", 40), lambda i: 50.0 + i % 3)
    skip = _rows(_days("2022-06-01", 400), lambda i: i % 3)
    assert m.judge_filter(kept, skip, m.FILTER_RULE)["verdict"] == m.NO_VERDICT


def test_a_pass_needs_both_literals():
    assert m.combine(m.PASS, m.PASS) == m.PASS
    assert m.combine(m.PASS, m.FAIL) == m.FAIL
    assert m.combine(m.FAIL, m.NO_VERDICT) == m.FAIL
    assert m.combine(m.NO_VERDICT, m.PASS) == m.NO_VERDICT
