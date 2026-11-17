"""Item 90's script (signal_entries_item90.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "signal_entries_item90", Path(__file__).resolve().parents[1] / "scripts" / "signal_entries_item90.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == [m.CONTROL_RULE["split"]]


def test_the_chain_smokes_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["smoke", "study"]


def test_both_arms_are_valid_automations():
    from cherrypick.automations import arms

    for a in m.ARMS.values():
        assert arms.validate(a) == []


def test_a_placebo_keeps_the_structure_and_replaces_only_the_entry():
    arm = m.ARMS["adx-condor"]
    p = m.placebo_arm(arm, 3, 754)
    assert p["name"] == "adx-condor~p03" and m.base_arm(p["name"]) == "adx-condor"
    assert p["entry"] == {"kind": "time", "at": "12:34", "window": 1}
    for k in ("legs", "gates", "size", "limits", "exit", "expiry", "weekdays"):
        assert p[k] == arm[k]
    assert arm["entry"]["kind"] == "trigger"  # the arm itself is untouched


def test_placebo_minutes_come_from_the_sessions_own_part():
    sessions = ["2025-06-27", "2025-06-30", "2025-07-01", "2025-07-02"]
    got = m.draw_minutes(
        sessions, {"design": [600, 610], "holdout": [700]}, "2025-07-01", 4, random.Random(1)
    )
    assert all(x in (600, 610) for s in sessions[:2] for x in got[s])
    assert all(x == 700 for s in sessions[2:] for x in got[s])
    assert all(len(v) == 4 for v in got.values())
    # A part where the arm never entered draws nothing, never another part's minutes.
    assert m.draw_minutes(sessions, {"design": [600]}, "2025-07-01", 2, random.Random(1))["2025-07-02"] == []


def _trade(legs, credit, qty=1):
    return {"legs": legs, "entry_mid": credit, "quantity": qty}


def test_max_loss_is_the_widest_wing_less_the_credit():
    spread = [
        {"right": "P", "side": -1, "strike": 5000.0},
        {"right": "P", "side": 1, "strike": 4990.0},
    ]
    assert m.max_loss_dollars(_trade(spread, 3.2)) == 680.0
    condor = spread[:1] + [
        {"right": "P", "side": 1, "strike": 4995.0},
        {"right": "C", "side": -1, "strike": 5010.0},
        {"right": "C", "side": 1, "strike": 5015.0},
    ]
    assert m.max_loss_dollars(_trade(condor, 2.5, qty=2)) == 500.0


def test_position_delta_signs_each_leg_and_refuses_a_missing_one():
    legs = [{"right": "P", "side": -1, "strike": 5000.0}, {"right": "P", "side": 1, "strike": 4990.0}]
    deltas = {(5000.0, "P"): -0.35, (4990.0, "P"): -0.2}
    assert m.position_delta(_trade(legs, 3.0), deltas) == pytest.approx(15.0)
    assert m.position_delta(_trade(legs, 3.0), {(5000.0, "P"): -0.35}) is None


def _rows(dates, net):
    return [{"date": d, "net": net(i)} for i, d in enumerate(dates)]


def _days(start, n):
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_the_control_passes_an_arm_clearly_above_its_placebo_in_every_part():
    design = _days("2023-01-01", 200) + _days("2024-02-01", 200)
    holdout = _days("2025-08-01", 100)
    arm = _rows(design + holdout, lambda i: 50.0 + (i % 7))
    placebo = _rows(design + holdout, lambda i: -5.0 + (i % 5))
    got = m.judge_control(arm, placebo, m.CONTROL_RULE)
    assert got["verdict"] == m.PASS and got["holdout"]["verdict"] == m.PASS


def test_the_control_fails_an_arm_that_only_beats_its_placebo_in_one_half():
    first = _days("2023-01-01", 200)
    second = _days("2024-02-01", 200)
    arm = _rows(first, lambda i: 80.0 + (i % 3)) + _rows(second, lambda i: -10.0 + (i % 3))
    placebo = _rows(first + second, lambda i: 0.0 + (i % 3))
    got = m.judge_control(arm, placebo, m.CONTROL_RULE)
    assert got["verdict"] == m.FAIL and got["holdout"] == {"verdict": m.NOT_READ}


def test_the_control_gives_no_verdict_under_its_design_floor():
    arm = _rows(_days("2023-01-01", 40), lambda i: 50.0 + i % 3)
    placebo = _rows(_days("2023-01-01", 400), lambda i: i % 3)
    assert m.judge_control(arm, placebo, m.CONTROL_RULE)["verdict"] == m.NO_VERDICT


def test_placebo_rows_count_once_a_session():
    rows = [
        {"date": "2024-01-02", "net": 10.0},
        {"date": "2024-01-02", "net": 30.0},
        {"date": "2024-01-03", "net": -4.0},
    ]
    assert m.per_session_means(rows) == [
        {"date": "2024-01-02", "net": 20.0},
        {"date": "2024-01-03", "net": -4.0},
    ]


def test_holm_steps_down_over_the_two_arms():
    assert m.holm({"a": 0.01, "b": 0.04}, 0.05) == {"a": True, "b": True}
    assert m.holm({"a": 0.03, "b": 0.04}, 0.05) == {"a": False, "b": False}
    assert m.holm({"a": 0.01, "b": 0.2}, 0.05) == {"a": True, "b": False}


def test_an_arm_passes_only_on_both_literals_and_holm():
    assert m.combine(m.PASS, m.PASS, True) == m.PASS
    assert m.combine(m.PASS, m.PASS, False) == m.FAIL
    assert m.combine(m.PASS, m.FAIL, True) == m.FAIL
    assert m.combine(m.NO_VERDICT, m.PASS, True) == m.NO_VERDICT
    assert m.combine(m.NO_VERDICT, m.FAIL, True) == m.FAIL
