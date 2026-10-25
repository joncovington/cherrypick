"""The post-earnings drift study (scripts/pead_history.py): dates, forward fields, the held screen, the
clustered statistic and the declared pass test.

Synthetic sessions only: no store, no Dolt. The pass test is shown to FAIL on a group with no drift,
because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "pead_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("pead_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ph = _module()
ARGS = SimpleNamespace(min_dollar_volume=100e6, min_price=10.0)


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def series(closes: list[float], start: date = date(2024, 1, 2), volume: float = 10e6) -> dict:
    dates = weekdays(start, len(closes))
    return {
        "dates": dates,
        "open": list(closes),
        "close": list(closes),
        "dollar_volume": [c * volume for c in closes],
    }


def test_locate_takes_the_report_day_as_p_after_the_close_and_as_r_before_the_open():
    dates = weekdays(date(2024, 3, 4), 10)  # Mon 03-04 ..
    report = date(2024, 3, 6)  # a Wednesday
    assert ph.locate(dates, report, amc=True) == (2, 3)
    assert ph.locate(dates, report, amc=False) == (1, 2)
    # a report on a day with no session is no event, not a guess
    assert ph.locate(dates, date(2024, 3, 9), amc=True) is None


def test_event_row_measures_the_reaction_and_forward_returns_against_spy():
    closes = [100.0] * 240 + [105.0] + [110.0] * 40  # P at 239 (100), R at 240 (105), then 110
    s = series(closes)
    spy = {d: 400.0 for d in s["dates"]}  # flat market
    row = ph.event_row(s, 239, 240, spy, ARGS)
    assert row["react"] == pytest.approx(0.05)
    assert row["fwd_21"] == pytest.approx(110 / 105 - 1)
    assert row["abn_21"] == pytest.approx(110 / 105 - 1)  # SPY flat, so abnormal = raw
    assert row["above_pre_21"] is True and row["touch_pre_21"] is False
    assert row["liquid"] is True
    # 100 for 240 sessions: the close equals both means, so not an uptrend
    assert row["uptrend"] is False


def test_event_row_sees_a_round_trip_below_the_pre_report_close():
    closes = [100.0] * 240 + [105.0, 99.0] + [101.0] * 40
    s = series(closes)
    spy = {d: 400.0 for d in s["dates"]}
    row = ph.event_row(s, 239, 240, spy, ARGS)
    assert row["touch_pre_8"] is True  # the 99 close went under 100
    assert row["above_pre_8"] is True  # but it ended above


def test_forward_fields_are_none_past_the_end_of_the_data():
    closes = [100.0] * 240 + [105.0] + [106.0] * 5
    s = series(closes)
    spy = {d: 400.0 for d in s["dates"]}
    row = ph.event_row(s, 239, 240, spy, ARGS)
    assert row["fwd_5"] is not None
    assert row["fwd_21"] is None and row["abn_21"] is None


def _past(react: float, fwd: float, known: date) -> dict:
    return {"react": react, "fwd_21": fwd, "known_on": known}


def test_held_screen_needs_four_known_up_reactions_and_three_quarters_held():
    d = date(2024, 1, 1)
    three = [_past(0.03, 0.01, d)] * 3
    assert ph.held_screen(three, date(2024, 6, 1)) is None  # too few
    held = [_past(0.03, 0.01, d)] * 6 + [_past(0.03, -0.02, d)] * 2  # 6 of 8
    assert ph.held_screen(held, date(2024, 6, 1)) is True
    failed = [_past(0.03, 0.01, d)] * 5 + [_past(0.03, -0.02, d)] * 3  # 5 of 8
    assert ph.held_screen(failed, date(2024, 6, 1)) is False


def test_held_screen_ignores_down_reactions_and_outcomes_not_yet_known():
    d, later = date(2024, 1, 1), date(2024, 12, 1)
    rows = [_past(0.03, 0.01, d)] * 4 + [_past(-0.05, -0.10, d)] * 4 + [_past(0.03, -0.05, later)] * 4
    # only the four known up reactions count, all held
    assert ph.held_screen(rows, date(2024, 6, 1)) is True


def _rows(values: list[float], start: date = date(2021, 1, 4)) -> list[dict]:
    """One event a week, spread over years, with abn_21 taken from `values` cyclically."""
    out = []
    for i in range(320):
        d = start + timedelta(weeks=i)
        out.append({"r_date": d.isoformat(), "abn_21": values[i % len(values)]})
    return out


def test_clustered_averages_within_a_week_before_the_t():
    d = date(2024, 1, 8)
    rows = [{"r_date": d.isoformat(), "abn_21": v} for v in (0.1, 0.1, 0.1, 0.1)]
    rows += [{"r_date": (d + timedelta(weeks=w)).isoformat(), "abn_21": 0.0} for w in (1, 2)]
    st = ph.clustered(rows, "abn_21")
    assert st["n"] == 6 and st["weeks"] == 3
    assert st["mean"] == pytest.approx(0.4 / 6)


def test_the_pass_test_passes_a_steady_drift_and_fails_a_zero_one():
    drift = ph.verdict(_rows([0.02, 0.01, 0.03, -0.005]))
    assert drift["pass"] is True
    none = ph.verdict(_rows([0.02, -0.02, 0.01, -0.01]))
    assert none["pass"] is False
    assert none["checks"]["t_at_least_2"] is False


def test_the_pass_test_fails_a_drift_confined_to_one_half():
    rows = _rows([0.03, 0.02])
    for r in rows:
        if date.fromisoformat(r["r_date"]) >= ph.SPLIT:
            r["abn_21"] = -0.001
    v = ph.verdict(rows)
    assert v["checks"]["both_halves"] is False
    assert v["pass"] is False


def test_events_that_lose_a_horizon_to_the_data_end_are_counted():
    m = _module()
    rows = [
        {f"fwd_{h}": 0.01 for h in m.HORIZONS},
        {**{f"fwd_{h}": 0.01 for h in m.HORIZONS}, "fwd_30": None},
    ]
    lost = m.lost_to_data_end(rows)
    assert lost["fwd_30"] == 1 and lost["fwd_5"] == 0
