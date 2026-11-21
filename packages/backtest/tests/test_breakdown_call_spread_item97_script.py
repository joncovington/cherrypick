"""Item 97's script (breakdown_call_spread_item97.py): invented bars and trades only, the store never read."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest
from cherrypick.core import signals

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "breakdown_call_spread_item97",
    Path(__file__).resolve().parents[1] / "scripts" / "breakdown_call_spread_item97.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

PREV, DAY = "2024-03-04", "2024-03-05"  # a Monday and the Tuesday after it


def _always(prev, session):
    return True


def _bars(prev_closes, day_closes):
    """30-minute bars, each a flat bar at its close (high = low = close), 13 a full session, behind a
    session flat at the day before's first close so the 20-bar EMA is defined from the day's first bar."""
    out = []
    warm = [prev_closes[0]] * 13
    for session, closes in (("2024-03-01", warm), (PREV, prev_closes), (DAY, day_closes)):
        for i, c in enumerate(closes):
            start = 570 + 30 * i
            out.append(signals.Bar(session, start, start + 30, c, c, c, c))
    return out


# The day before falls steadily to a low of 4000, so the EMA sits above the late closes.
FALLING = [4100.0 - 7.0 * i for i in range(13)] + [4000.0]
FALLING = FALLING[:13]
LOW = min(FALLING)


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == [m.CONTROL_RULE["split"]]


def test_the_chain_smokes_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["smoke", "study"]


def test_the_arm_is_a_valid_automation():
    from cherrypick.automations import arms

    assert arms.validate(m.arm_at(600)) == []


def test_the_arm_is_entered_at_the_firing_minute_and_keeps_the_structure():
    a = m.arm_at(630)
    assert a["entry"] == {"kind": "time", "at": "10:30", "window": 1}
    for k in ("legs", "gates", "size", "limits", "exit", "expiry", "weekdays"):
        assert a[k] == m.STRUCTURE[k]
    assert m.placebo_name(3) == "breakdown-call-spread~p03"


def test_a_sample_closes_the_minute_bar_ending_then():
    trail = [(571, 1.0), (600, 2.0), (601, 3.0), (960, 4.0)]
    bars = signals.from_trail(DAY, m.bar_ticks(trail), 30)
    assert [(b.start, b.end, b.close) for b in bars] == [(570, 600, 2.0), (600, 630, 3.0), (930, 960, 4.0)]


def test_it_fires_on_the_first_close_below_the_low_by_the_margin():
    just_above = LOW * (1 - 0.0002)
    below = LOW * (1 - 0.0005)
    day = [LOW + 5, just_above, below, below - 5] + [below - 10] * 9
    assert m.firing_minute(_bars(FALLING, day), DAY, _always) == 660  # the 10:30 bar, ending 11:00


def test_a_close_within_the_margin_does_not_fire():
    day = [LOW * (1 - 0.0002)] * 13
    assert m.firing_minute(_bars(FALLING, day), DAY, _always) is None


def _three(older, prev, day):
    """Bars over three sessions: one before the day before, the day before, and the day."""
    out = []
    for session, closes in (("2024-03-01", older), (PREV, prev), (DAY, day)):
        for i, c in enumerate(closes):
            start = 570 + 30 * i
            out.append(signals.Bar(session, start, start + 30, c, c, c, c))
    return out


OLDER_LOW = [3000.0] * 13  # a session far below: it drags the 20-bar EMA under the day before's low
FLAT = [4000.0] * 13  # the day before; its low is 4000, the break level 3998.8


def test_a_close_above_the_ema_stops_the_cross_and_spends_it():
    # Without the low session behind it, the same day fires at its first bar.
    assert m.firing_minute(_three([4000.0] * 13, FLAT, [3990.0] * 13), DAY, _always) == 600
    # Behind a session at 3000 the EMA sits near 3680, under the close: the cross is stopped, and the
    # closes that stay below never cross again.
    assert m.firing_minute(_three(OLDER_LOW, FLAT, [3990.0] * 13), DAY, _always) is None


def test_a_rising_rate_of_change_stops_a_second_cross():
    # The first cross (3900) is stopped by the low EMA; two closes far above re-arm it and lift the EMA
    # above 3995, so the second cross (3995) passes the EMA and is stopped only by ROC(3) against 3900.
    day = [3900.0, 6000.0, 6000.0, 3995.0] + [3995.0] * 9
    assert m.firing_minute(_three(OLDER_LOW, FLAT, day), DAY, _always) is None
    # The same second cross with a higher close three bars back fires there (the 11:00 bar, ending 11:30).
    day = [3900.0, 6000.0, 6000.0, 3995.0] + [3995.0] * 9
    day[0] = 3996.0  # still a first cross stopped by the EMA, now above the second cross's close
    assert m.firing_minute(_three(OLDER_LOW, FLAT, day), DAY, _always) == 690


def test_at_a_first_cross_the_rate_of_change_is_below_zero_by_construction():
    # Every close in the three bars before a first cross is at or above the break level, so the
    # crossing close is below each of them: ROC(3) cannot stop a first cross.
    assert m.firing_minute(_bars(FALLING, [3990.0] * 13), DAY, _always) == 600


def test_nothing_fires_after_the_cutoff_or_without_the_day_before():
    below = LOW * (1 - 0.0005)
    late = [LOW + 50] * 11 + [below, below]  # first close below on the 15:00-15:30 bar
    assert m.firing_minute(_bars(FALLING, late), DAY, _always) is None
    day = [below] * 13
    assert m.firing_minute(_bars(FALLING, day), DAY, lambda p, s: False) is None
    only_today = [b for b in _bars(FALLING, day) if b.session == DAY]
    assert m.firing_minute(only_today, DAY, _always) is None


def test_placebo_minutes_come_from_the_sessions_own_part():
    sessions = ["2025-06-27", "2025-06-30", "2025-07-01", "2025-07-02"]
    got = m.draw_minutes(
        sessions, {"design": [600, 610], "holdout": [700]}, "2025-07-01", 4, random.Random(1)
    )
    assert all(x in (600, 610) for s in sessions[:2] for x in got[s])
    assert all(x == 700 for s in sessions[2:] for x in got[s])
    assert m.draw_minutes(sessions, {"design": [600]}, "2025-07-01", 2, random.Random(1))["2025-07-02"] == []


def test_max_loss_is_the_wing_less_the_credit():
    legs = [{"right": "C", "side": -1, "strike": 5000.0}, {"right": "C", "side": 1, "strike": 5015.0}]
    assert m.max_loss_dollars({"legs": legs, "entry_mid": 4.2, "quantity": 1}) == 1080.0


def test_position_delta_signs_each_leg_and_refuses_a_missing_one():
    trade = {
        "legs": [{"right": "C", "side": -1, "strike": 5000.0}, {"right": "C", "side": 1, "strike": 5015.0}],
        "quantity": 1,
    }
    assert m.position_delta(trade, {(5000.0, "C"): 0.30, (5015.0, "C"): 0.18}) == pytest.approx(-12.0)
    assert m.position_delta(trade, {(5000.0, "C"): 0.30}) is None


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
    first, second = _days("2023-01-01", 200), _days("2024-02-01", 200)
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


def test_the_arm_passes_only_on_both_literals():
    assert m.combine(m.PASS, m.PASS) == m.PASS
    assert m.combine(m.PASS, m.FAIL) == m.FAIL
    assert m.combine(m.NO_VERDICT, m.PASS) == m.NO_VERDICT
    assert m.combine(m.NO_VERDICT, m.FAIL) == m.FAIL
