"""Hypothesis 20's study (scripts/convex_stack_item20.py) and fetch plan: the Convex Stack's width,
direction, body choice, exits and verdict. Invented quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = _module("convex_stack_item20")
fetch = _module("fetch_spx_convex_stack")


def q(bid, ask, und=5000.0):
    return {"bid": bid, "ask": ask, "underlying": und}


def chain(right: str, prices: dict[float, tuple[float, float]]) -> dict:
    return {(k, right): q(b, a) for k, (b, a) in prices.items()}


# --------------------------------------------------------------------------- entry rules
def test_width_follows_the_declared_points_in_the_vix_bands():
    assert [cs.width_for(v) for v in (12, 16.99, 17, 24.49, 24.5, 31.9, 32, 60)] == [
        25,
        25,
        30,
        30,
        40,
        40,
        50,
        50,
    ]


def test_the_expiry_is_friday_or_tuesday_and_moves_back_over_a_holiday():
    assert fetch.expiry_for(date(2024, 3, 4)) == date(2024, 3, 8)  # Monday -> Friday
    assert fetch.expiry_for(date(2024, 3, 7)) == date(2024, 3, 12)  # Thursday -> next Tuesday
    assert fetch.expiry_for(date(2024, 3, 25)) == date(2024, 3, 28)  # Good Friday -> Thursday


def test_the_fetch_plans_an_entry_and_every_session_to_expiry_and_skips_what_is_done():
    todo = fetch.plan("2024-03-04", "2024-03-07", set())
    monday = [r for r in todo if r[0] == date(2024, 3, 4)]
    assert [(r[2], r[3].isoformat()) for r in monday] == [
        ("entry", "2024-03-04"), ("path", "2024-03-05"), ("path", "2024-03-06"),
        ("path", "2024-03-07"), ("path", "2024-03-08"),
    ]  # fmt: skip
    assert {r[0].weekday() for r in todo} == {0, 3}
    done = {("2024-03-04", "2024-03-08", "entry", "2024-03-04")}
    assert len(fetch.plan("2024-03-04", "2024-03-07", done)) == len(todo) - 1


def _session(level: float, slope: float) -> list[tuple[str, float]]:
    """One session's prints, rising by `slope` a minute from `level` at 09:30."""
    out, t = [], 9 * 60 + 30
    while t <= 16 * 60:
        out.append((f"{t // 60:02d}:{t % 60:02d}", level + slope * (t - 570)))
        t += 1
    return out


def _spots(slope: float, days: int = 10) -> dict:
    d0 = date(2024, 1, 2)
    out, level = {}, 5000.0
    for i in range(days):
        out[(d0 + timedelta(days=i)).isoformat()] = _session(level, slope)
        level += slope * 390
    return out


def test_the_side_follows_the_moving_average():
    up, down = _spots(0.1), _spots(-0.1)
    last = max(up)
    assert cs.direction(up, last, "15:45") == "C"
    assert cs.direction(down, last, "15:45") == "P"


def test_the_open_bar_closes_at_the_entry_minute_not_the_session_close():
    spots = _spots(0.1)
    last = max(spots)
    # A collapse after 15:45 is not yet known at entry, so the side stays a call.
    spots[last] = [(t, s if t <= "15:45" else s - 500) for t, s in spots[last]]
    assert cs.direction(spots, last, "15:45") == "C"
    assert cs.bar_closes(spots[last], until="15:45")[-1] == dict(spots[last])["15:45"]


def test_too_few_bars_is_no_direction():
    spots = _spots(0.1, days=6)
    assert cs.direction(spots, max(spots), "15:45") is None


def test_the_body_is_the_nearest_strike_on_the_side_that_costs_at_most_ten_percent():
    mids = {k: 40.0 * 0.92 ** ((5040 - k) / 5) for k in range(4700, 5045, 5)}
    puts = chain("P", {k: (m - 0.05, m + 0.05) for k, m in mids.items()})
    body, legs, debit = cs.pick_body(puts, 5000.0, 30, "P", 0.10, "working")
    assert body < 5000.0 and debit <= 3.0
    inner = cs.price(puts, cs.fly_legs(body + 5, 30, "P"), buy=True, fill="working")
    assert inner > 3.0  # the strike nearer spot was too dear
    assert cs.pick_body(puts, 5000.0, 30, "P", 0.05, "working")[0] < body  # a tighter cap walks further


# --------------------------------------------------------------------------- exits
LEGS = [((4940.0, "P"), 1), ((4970.0, "P"), -2), ((5000.0, "P"), 1)]


def _path(values: dict[tuple[str, str], float]) -> dict:
    """A path whose fly sells for exactly `value` at each (session, time)."""
    return {
        k: chain("P", {5000.0: (v, v), 4970.0: (0.0, 0.0), 4940.0: (0.0, 0.0)}) for k, v in values.items()
    }


def test_the_target_sells_at_the_first_mark_worth_three_times_the_debit():
    path = _path({("2024-03-05", "09:30"): 9.0, ("2024-03-05", "10:00"): 2.9, ("2024-03-06", "11:00"): 3.2})
    assert cs.exit_target(path, LEGS, 1.0, "mid", 3.0) == (3.2, "2024-03-06 11:00")  # 09:30 is too early
    assert cs.exit_target(path, LEGS, 1.2, "mid", 3.0) is None


def test_day_two_sells_at_the_second_sessions_1545_or_the_last_mark_before_it():
    # Monday 2024-03-04: the second session after it is Wednesday the 6th.
    path = _path({("2024-03-05", "15:45"): 5.0, ("2024-03-06", "15:40"): 1.4, ("2024-03-06", "15:50"): 9.0})
    assert cs.exit_day2(path, LEGS, "mid", "2024-03-04", "2024-03-08") == (1.4, "2024-03-06 15:40")
    assert (
        cs.exit_day2(_path({("2024-03-05", "15:45"): 5.0}), LEGS, "mid", "2024-03-04", "2024-03-08") is None
    )
    # Thursday 2024-03-07: Friday, then Monday the 11th.
    assert cs.nth_session_after("2024-03-07", 2) == "2024-03-11"


def test_an_unsold_fly_settles_at_intrinsic_with_its_settlement_fee():
    r = cs.fly_result(LEGS, 1.0, None, 4970.0)
    assert r["how"] == "settled" and r["exit"] == 30.0
    assert r["net"] == round(r["gross"] - r["cost"], 2)


# --------------------------------------------------------------------------- verdict
def _rows(nets: list[float], start="2023-01-02") -> list[dict]:
    """Two entries a week (Monday, Thursday), each arm the same net."""
    d0 = date.fromisoformat(start)
    out = []
    for i, n in enumerate(nets):
        day = d0 + timedelta(weeks=i // 2, days=3 * (i % 2))
        out.append({"day": day.isoformat(), "debit": 2.0, **{a: {"net": n, "cost": 20.0} for a in cs.ARMS}})
    return out


def _judged(nets):
    rows = _rows(nets)
    tests = {a: cs.score(rows, a, rows[0]["day"], rows[-1]["day"]) for a in cs.ARMS}
    cs.judge(tests)
    return tests["hold"]


def test_a_strong_steady_edge_passes():
    # Weeks of 110 and 80: steady, but not identical, so the week-level t is defined.
    r = _judged([70.0, 40.0, 40.0, 40.0] * 50)
    assert r["verdict"] == "pass", r.get("checks")


def test_fewer_than_100_entries_is_no_verdict():
    assert _judged([60.0, 40.0] * 40)["verdict"] == "no verdict"


def test_a_noisy_edge_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 100)
    assert r["verdict"] == "fail" and not r["checks"]["t_above_bar"]


def test_a_losing_second_half_fails():
    r = _judged([80.0, 60.0] * 60 + [-5.0, -1.0] * 60)
    assert r["verdict"] == "fail" and not r["checks"]["both_halves_positive"]


def test_an_edge_smaller_than_half_its_costs_fails_the_stress():
    r = _judged([12.0, 6.0] * 100)
    assert r["verdict"] == "fail" and not r["checks"]["survives_1_5x_costs"]


def test_weeks_count_as_one_observation_and_can_fail_alone():
    # Entry nets alternate within each week, so the per-entry t is high, while whole weeks swing:
    # +200 and -150 weeks, with a small positive edge, are too noisy at the week level.
    nets = []
    for i in range(110):
        nets += [101.0, 101.0] if i % 2 == 0 else [-74.0, -74.0]
    rows = _rows(nets)
    assert len(cs.week_nets(rows, "hold")) == 110
    tests = {a: cs.score(rows, a, rows[0]["day"], rows[-1]["day"]) for a in cs.ARMS}
    cs.judge(tests)
    assert tests["hold"]["week_t"] < tests["hold"]["t"]
    assert not tests["hold"]["checks"]["week_t_above_bar"]


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert cs.main(["--end", "2025-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
