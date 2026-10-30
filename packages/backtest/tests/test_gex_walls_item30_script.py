"""Hypothesis 30's study (scripts/gex_walls_item30.py): the target expiry, the walls, their distance,
each wall-day's d and the verdict. Invented chains and paths only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("gex_walls_item30", SCRIPTS / "gex_walls_item30.py")
s30 = importlib.util.module_from_spec(_spec)
sys.modules["gex_walls_item30"] = s30
_spec.loader.exec_module(s30)


def test_the_monthly_steps_back_from_a_holiday_friday():
    assert s30.monthly_expiry(2022, 4) == date(2022, 4, 14)  # Good Friday
    assert s30.monthly_expiry(2024, 6) == date(2024, 6, 21)


def test_the_target_is_the_monthly_20_to_60_days_out_nearest_45():
    exps = s30.monthlies(date(2024, 1, 1), date(2024, 6, 30))
    assert s30.target_expiry(date(2024, 2, 1), exps) == date(2024, 3, 15)  # 43 days, not 16
    assert s30.target_expiry(date(2024, 1, 31), [date(2024, 2, 16)]) is None  # 16 days: too near
    assert s30.target_expiry(date(2024, 1, 2), [date(2024, 3, 15)]) is None  # 73 days: too far


def _chain(spot=100.0, years=45 / 365, vol=0.25):
    from cherrypick.core import bs

    out = []
    for k in range(80, 121, 5):
        for right in ("C", "P"):
            px = bs.price(spot, float(k), years, vol, right, 0.0)
            out.append((float(k), right, px - 0.01, px + 0.01))
    return out


def test_the_walls_are_where_the_net_gamma_peaks_either_side():
    oi = {("C", 110.0): 50_000, ("P", 90.0): 50_000, ("C", 100.0): 100, ("P", 100.0): 100}
    w = s30.walls(_chain(), oi, 100.0, 45 / 365, 0.0)
    assert w["net"] == (110.0, 90.0)
    assert abs(w["atm_vol"] - 0.25) < 0.01
    moved = {("C", 115.0): 50_000, ("P", 85.0): 50_000}
    assert s30.walls(_chain(), moved, 100.0, 45 / 365, 0.0)["net"] == (115.0, 85.0)
    assert s30.walls(_chain(), {}, 100.0, 45 / 365, 0.0) is None  # nothing open: no walls


def test_a_wall_on_the_wrong_side_or_out_of_range_is_no_wall():
    assert s30.distance(110.0, "call", 100.0, 10.0) == 1.0
    assert s30.distance(90.0, "put", 100.0, 10.0) == 1.0
    assert s30.distance(95.0, "call", 100.0, 10.0) is None  # below the close
    assert s30.distance(101.0, "call", 100.0, 10.0) is None  # nearer than 0.25 sigma
    assert s30.distance(130.0, "call", 100.0, 10.0) is None  # further than 2 sigma
    assert s30.distance(None, "put", 100.0, 10.0) is None


def test_the_excursion_reads_the_next_h_sessions_only():
    d0 = date(2024, 1, 1)
    bars = [(d0 + timedelta(days=i), 100 + i, 100 - i, 100.0) for i in range(5)]
    assert s30.excursion(bars, 0, 2) == (102, 98)
    assert s30.excursion(bars, 3, 2) is None  # one session left


def _days(walls_x, ms):
    d0 = date(2023, 1, 2)
    return [
        {
            "day": d0 + timedelta(days=i),
            "x": {k: {"call": x, "put": x} for k in ("net", "peak")},
            "m": {"call": m, "put": m},
        }
        for i, (x, m) in enumerate(zip(walls_x, ms, strict=True))
    ]


def test_d_is_the_crossing_less_the_rate_any_level_that_far_is_reached():
    # Four days: excursions 0.5, 1.5, 0.5, 1.5, so any level at 1.0 is reached half the time.
    r = s30.readings(_days([1.0, 1.0, 1.0, 1.0], [0.5, 1.5, 0.5, 1.5]))
    assert [row["d"] for row in r[("net", "call")]] == [-0.5, 0.5, -0.5, 0.5]
    # A day with no wall that side is not a wall-day, but its path still counts towards F.
    r = s30.readings(_days([None, 1.0], [2.0, 0.5]))
    assert [row["d"] for row in r[("net", "call")]] == [0.0 - 0.5]


def _cells(d_value, n=300, placebo=0.0):
    d0 = date(2022, 1, 3)
    rows = [{"day": d0 + timedelta(days=i), "x": 1.0, "d": d_value, "placebo": placebo} for i in range(n)]
    return {f"{s}/{side}": rows for s in ("SPY", "AAPL") for side in ("call", "put")}


def test_walls_that_hold_pass_and_walls_that_do_not_fail():
    mid = date(2022, 6, 1)
    held = s30.judge(_cells(-0.10), mid, 10)
    assert held["passes"] and held["mean_d"] == -0.10
    assert not s30.judge(_cells(0.0), mid, 10)["passes"]
    weak = s30.judge(_cells(-0.02), mid, 10)  # below zero, short of the declared effect
    assert not weak["passes"] and not weak["conditions"]["mean_at_or_below_effect"]
    no_better = s30.judge(_cells(-0.10, placebo=-0.12), mid, 10)  # the placebo does as well
    assert not no_better["passes"] and not no_better["conditions"]["below_placebo"]


def test_one_cell_against_the_rest_fails():
    cells = _cells(-0.10)
    d0 = date(2022, 1, 3)
    cells["AAPL/put"] = [
        {"day": d0 + timedelta(days=i), "x": 1.0, "d": 0.05, "placebo": 0.0} for i in range(300)
    ]
    out = s30.judge(cells, date(2022, 6, 1), 10)
    assert not out["conditions"]["negative_each_cell"] and not out["passes"]


def test_the_verdict_reads_the_holdout_only_after_a_design_pass():
    good, bad = {"passes": True, "mean_d": -0.1}, {"passes": False, "mean_d": 0.0}
    assert s30.verdict(bad, None, True) == "FAIL"
    assert s30.verdict(good, {"mean_d": -0.01}, True) == "PASS"
    assert s30.verdict(good, {"mean_d": 0.01}, True) == "FAIL"
    assert s30.verdict(good, None, False) == "NO VERDICT"


def test_more_than_one_expiry_in_a_week_counts_as_a_daily_expiry_week():
    weekly = {date(2024, 1, 2): {date(2024, 1, 5), date(2024, 1, 12)}}
    assert s30.daily_expiry_weeks(weekly) == 0
    daily = {date(2024, 1, 2): {date(2024, 1, 3), date(2024, 1, 5)}}
    assert s30.daily_expiry_weeks(daily) == 1
