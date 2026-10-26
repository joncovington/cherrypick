"""Hypothesis 19's study (scripts/time_warp_item19.py): the Time Warp's pricing, body choice, exits
and verdict. Invented quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "time_warp_item19.py"


def _module():
    spec = importlib.util.spec_from_file_location("time_warp_item19", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tw = _module()


def q(bid, ask, und=5000.0):
    return {"bid": bid, "ask": ask, "underlying": und}


def call_chain(prices: dict[float, tuple[float, float]]) -> dict:
    return {(k, "C"): q(b, a) for k, (b, a) in prices.items()}


def test_width_follows_the_vix_bands():
    assert [tw.width_for(v) for v in (12, 16.99, 17, 31.9, 32, 60)] == [20, 20, 30, 30, 50, 50]


def test_a_bought_fly_costs_more_at_working_than_at_mid():
    chain = call_chain({5040: (3.0, 3.4), 5060: (1.0, 1.2), 5080: (0.2, 0.3)})
    legs = tw.fly_legs(5060, 20, "C")
    mid = tw.price(chain, legs, buy=True, fill="mid")
    working = tw.price(chain, legs, buy=True, fill="working")
    assert mid == round(3.2 - 2 * 1.1 + 0.25, 4)
    assert working > mid
    assert tw.price(chain, legs, buy=True, fill="mid_0.05") == round(mid + 0.05, 4)


def test_a_sold_fly_brings_less_at_working_than_at_mid_and_never_below_zero():
    chain = call_chain({5040: (3.0, 3.4), 5060: (1.0, 1.2), 5080: (0.2, 0.3)})
    legs = tw.fly_legs(5060, 20, "C")
    assert tw.price(chain, legs, buy=False, fill="working") < tw.price(chain, legs, buy=False, fill="mid")
    dead = call_chain({5040: (0.0, 0.05), 5060: (0.0, 0.05), 5080: (0.0, 0.05)})
    assert tw.price(dead, legs, buy=False, fill="working") == 0.0


def test_entry_refuses_a_leg_that_is_not_two_sided():
    chain = call_chain({5040: (3.0, 3.4), 5060: (None, 1.2), 5080: (0.2, 0.3)})
    assert tw.price(chain, tw.fly_legs(5060, 20, "C"), buy=True, fill="working") is None


def test_the_body_is_the_nearest_strike_that_costs_at_most_five_percent():
    # Convex call prices, so a fly gets cheaper the further its body sits above spot 5000.
    mids = {k: 40.0 * 0.92 ** ((k - 4960) / 5) for k in range(4960, 5300, 10)}
    chain = call_chain({k: (m - 0.05, m + 0.05) for k, m in mids.items()})
    picked = tw.pick_body(chain, 5000.0, 20, "C", 0.05, "working")
    assert picked is not None
    body, legs, debit = picked
    assert debit <= 1.0
    inner = tw.price(chain, tw.fly_legs(body - 10, 20, "C"), buy=True, fill="working")
    assert inner > 1.0  # the strike before it was too dear: it is the nearest that fits
    nearer = tw.pick_body(chain, 5000.0, 20, "C", 0.20, "working")
    assert nearer[0] < body  # a looser cap stops nearer spot: the walk goes outward


def test_intrinsic_counts_itm_legs_for_the_settlement_fee():
    legs = tw.fly_legs(5060, 20, "C")
    assert tw.intrinsic(legs, 5060) == (20.0, 1)
    assert tw.intrinsic(legs, 5100) == (0.0, 3)
    assert tw.intrinsic(legs, 5000) == (0.0, 0)


def _path(values: dict[str, float]) -> dict:
    """A path whose fly sells for exactly `value` at each time (a one-leg-wide quote trick)."""
    out = {}
    for t, v in values.items():
        out[t] = call_chain({5040: (v, v), 5060: (0.0, 0.0), 5080: (0.0, 0.0)})
    return out


def test_the_trail_waits_for_a_profit_then_gives_back_less_as_the_day_runs():
    legs = tw.fly_legs(5060, 20, "C")
    # Debit 1.00: up to 5.00 at 10:00, then 2.10 at 10:30. Before noon g = 0.75, so the floor is
    # 1 + 0.25 x 4 = 2.00: 2.10 holds.
    path = _path({"09:35": 0.8, "10:00": 5.0, "10:30": 2.1})
    assert tw.exit_trail(path, legs, 1.0, "mid") is None
    # After 14:00 g = 0.20: the floor is 1 + 0.8 x 4 = 4.20, so 4.10 sells.
    late = _path({"14:05": 5.0, "14:10": 4.1})
    assert tw.exit_trail(late, legs, 1.0, "mid") == (4.1, "14:10")


def test_the_trail_never_sells_a_fly_that_never_showed_a_profit():
    legs = tw.fly_legs(5060, 20, "C")
    assert tw.exit_trail(_path({"09:35": 0.5, "10:00": 0.1}), legs, 1.0, "mid") is None


def test_the_fixed_arm_sells_at_1030_or_settles():
    legs = tw.fly_legs(5060, 20, "C")
    assert tw.exit_fixed(_path({"10:30": 1.7}), legs, "mid") == (1.7, "10:30")
    assert tw.exit_fixed(_path({"10:35": 1.7}), legs, "mid") is None


def _rows(nets: list[float], start="2023-01-02") -> list[dict]:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [
        {"day": (d0 + timedelta(days=i)).isoformat(), "debit": 2.0,
         "trail": {"net": n, "cost": 20.0}, "fixed_1030": {"net": n, "cost": 20.0}}
        for i, n in enumerate(nets)
    ]  # fmt: skip


def _judged(nets):
    rows = _rows(nets)
    start, end = rows[0]["day"], rows[-1]["day"]
    tests = {a: tw.score(rows, a, start, end) for a in tw.ARMS}
    tw.judge(tests)
    return tests["trail"]


def test_a_strong_steady_edge_passes():
    assert _judged([60.0, 40.0] * 100)["verdict"] == "pass"


def test_fewer_than_100_sessions_is_no_verdict():
    assert _judged([60.0, 40.0] * 40)["verdict"] == "no verdict"


def test_a_noisy_edge_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 100)
    assert r["verdict"] == "fail" and not r["checks"]["t_above_bar"]


def test_a_losing_second_half_fails():
    r = _judged([80.0, 60.0] * 60 + [-5.0, -1.0] * 60)
    assert r["verdict"] == "fail" and not r["checks"]["both_halves_positive"]


def test_an_edge_smaller_than_half_its_costs_fails_the_stress():
    r = _judged([12.0, 6.0] * 100)  # mean 9 against costs of 20: 1.5x costs takes 10 off
    assert r["verdict"] == "fail" and not r["checks"]["survives_1_5x_costs"]


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert tw.main(["--end", "2025-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
