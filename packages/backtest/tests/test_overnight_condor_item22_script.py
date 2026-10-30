"""Hypothesis 22's study (scripts/overnight_condor_item22.py): the overnight condor's pricing, strike
choice, exits and verdict. Invented quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "overnight_condor_item22.py"


def _module():
    spec = importlib.util.spec_from_file_location("overnight_condor_item22", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oc = _module()


def q(bid, ask, delta=None, und=5000.0):
    return {"bid": bid, "ask": ask, "delta": delta, "underlying": und}


def chain(prices: dict[tuple[float, str], tuple]) -> dict:
    return {k: q(*v) for k, v in prices.items()}


# A 15:50 chain around spot 5000: calls above, puts below, deltas shrinking outward.
ENTRY = chain(
    {
        (5050, "C"): (3.0, 3.4, 0.14),
        (5060, "C"): (2.0, 2.3, 0.10),
        (5070, "C"): (1.3, 1.5, 0.07),
        (5085, "C"): (0.5, 0.7, 0.04),
        (4950, "P"): (3.6, 4.0, -0.13),
        (4940, "P"): (2.6, 2.9, -0.09),
        (4930, "P"): (1.9, 2.1, -0.07),
        (4915, "P"): (0.8, 1.0, -0.04),
    }
)


def test_the_short_is_the_otm_strike_with_delta_nearest_the_target():
    assert oc.pick_short(ENTRY, 5000.0, "C", 0.10) == 5060
    assert oc.pick_short(ENTRY, 5000.0, "P", 0.10) == 4940
    assert oc.pick_short(ENTRY, 5000.0, "C", 0.05) == 5085


def test_an_in_the_money_or_one_sided_strike_is_never_the_short():
    c = chain(
        {(4990, "C"): (12.0, 12.5, 0.60), (5060, "C"): (None, 2.3, 0.10), (5070, "C"): (1.3, 1.5, 0.07)}
    )
    assert oc.pick_short(c, 5000.0, "C", 0.10) == 5070


def test_a_sold_side_brings_less_at_working_than_at_mid_and_a_buyback_costs_more():
    legs = oc.side_legs(5060, 25, "C")
    assert legs == [((5060, "C"), -1), ((5085, "C"), 1)]
    mid = oc.price(ENTRY, legs, sell=True, fill="mid")
    assert mid == round(2.15 - 0.6, 4)
    assert oc.price(ENTRY, legs, sell=True, fill="working") < mid
    assert oc.price(ENTRY, legs, sell=True, fill="mid_0.05") == round(mid - 0.05, 4)
    assert oc.price(ENTRY, legs, sell=False, fill="working") > oc.price(ENTRY, legs, sell=False, fill="mid")


def test_a_wing_that_is_not_two_sided_prices_to_none():
    c = dict(ENTRY)
    c[(5085, "C")] = q(None, 0.7, 0.04)
    assert oc.price(c, oc.side_legs(5060, 25, "C"), sell=True, fill="working") is None


def test_settlement_owes_the_spread_when_the_market_runs_through_it():
    legs = oc.side_legs(5060, 25, "C")
    assert oc.intrinsic(legs, 5000) == (0.0, 0)
    assert oc.intrinsic(legs, 5070) == (-10.0, 1)
    assert oc.intrinsic(legs, 5200) == (-25.0, 2)


def test_the_buyback_uses_the_first_morning_mark_that_quotes_every_leg_or_settles():
    legs = oc.side_legs(5060, 25, "C")
    quoted = chain({(5060, "C"): (1.0, 1.2), (5085, "C"): (0.2, 0.3)})
    half = chain({(5060, "C"): (1.0, 1.2), (5085, "C"): (None, 0.3)})
    assert oc.exit_buyback({"09:35": quoted}, legs, "mid")[1] == "09:35"
    assert oc.exit_buyback({"09:35": half, "09:40": quoted}, legs, "mid")[1] == "09:40"
    assert oc.exit_buyback({"10:30": quoted}, legs, "mid") is None


def test_a_side_result_charges_fees_once_per_condor_and_the_settlement_fee_per_itm_leg():
    legs = oc.side_legs(5060, 25, "C")
    closed = oc.side_result(legs, 1.5, (0.9, "09:35"), 5000)
    assert closed["gross"] == 60.0 and closed["how"] == "closed" and closed["cost"] > 0
    settled_otm = oc.side_result(legs, 1.5, None, 5000)
    assert settled_otm["gross"] == 150.0 and settled_otm["how"] == "settled"
    settled_itm = oc.side_result(legs, 1.5, None, 5070)
    assert settled_itm["gross"] == -850.0
    assert settled_itm["cost"] > settled_otm["cost"]  # the in-the-money short pays $5 to settle


def test_a_session_needs_both_sides():
    # Every leg at half its closing price the next morning.
    morning = {k: (b * 0.5, a * 0.5) for k, (b, a, _) in {
        (5060, "C"): (2.0, 2.3, 0), (5085, "C"): (0.5, 0.7, 0),
        (4940, "P"): (2.6, 2.9, 0), (4915, "P"): (0.8, 1.0, 0),
    }.items()}  # fmt: skip
    path = {"09:35": chain(morning)}
    r = oc.run_session({"15:50": ENTRY}, path, 5000.0, at="15:50", delta=0.10, wing=25, fill="mid")
    assert set(r["sides"]) == {"C", "P"} and r["buyback_0935"]["net"] > 0 and r["settle"]["net"] > 0
    calls_only = {k: v for k, v in ENTRY.items() if k[1] == "C"}
    assert "skip" in oc.run_session(
        {"15:50": calls_only}, path, 5000.0, at="15:50", delta=0.10, wing=25, fill="mid"
    )
    assert oc.run_session({}, path, 5000.0, at="15:50", delta=0.10, wing=25, fill="mid") == {
        "skip": "no entry quote"
    }


def test_closure_kind_splits_weekends_and_holidays_from_weeknights():
    assert oc.closure_kind("2024-08-01", "2024-08-02") == "weeknight"
    assert oc.closure_kind("2024-08-02", "2024-08-05") == "weekend"
    assert oc.closure_kind("2024-07-03", "2024-07-05") == "weekend"


def test_vix_bands_are_labelled_from_the_prior_close():
    assert [oc.vix_band(v) for v in (12, 15, 19.9, 25, 40)] == ["0-15", "15-20", "15-20", "25+", "25+"]


def _rows(nets: list[float], start="2023-01-02") -> list[dict]:
    d0 = date.fromisoformat(start)
    return [
        {"day": (d0 + timedelta(days=i)).isoformat(), "credit": 3.0,
         "buyback_0935": {"net": n, "cost": 20.0}, "settle": {"net": n, "cost": 20.0}}
        for i, n in enumerate(nets)
    ]  # fmt: skip


def _judged(nets):
    rows = _rows(nets)
    start, end = rows[0]["day"], rows[-1]["day"]
    tests = {a: oc.score(rows, a, start, end) for a in oc.ARMS}
    oc.judge(tests)
    return tests["buyback_0935"]


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
    assert oc.main(["--end", "2025-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
