"""Hypothesis 58's study (packages/backtest/scripts/close_fly_item58.py): the closing fly's strikes, pricing,
settlement, calendar labels and verdict. Invented quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "close_fly_item58.py"


def _module():
    spec = importlib.util.spec_from_file_location("close_fly_item58", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cf = _module()


def chain(prices: dict[tuple[float, str], tuple[float, float]]) -> dict:
    return {k: {"bid": b, "ask": a} for k, (b, a) in prices.items()}


# A 15:55 chain around spot 5002: five-point strikes, both rights at every strike.
CHAIN = chain(
    {
        (4990, "P"): (0.05, 0.10), (4995, "P"): (0.30, 0.40), (5000, "P"): (1.10, 1.30),
        (5005, "P"): (4.00, 4.40), (5010, "P"): (8.40, 9.00),
        (4990, "C"): (11.8, 12.6), (4995, "C"): (7.10, 7.50), (5000, "C"): (3.00, 3.20),
        (5005, "C"): (0.90, 1.00), (5010, "C"): (0.20, 0.30), (5015, "C"): (0.05, 0.10),
        (4985, "P"): (0.00, 0.05), (4985, "C"): (16.5, 17.5),
    }
)  # fmt: skip
SPOT = 5002.0


def test_the_fly_centres_on_the_strike_nearest_spot():
    legs = cf.fly_legs(CHAIN, SPOT, 10)
    assert legs == [((5000, "C"), -1), ((5000, "P"), -1), ((5010, "C"), 1), ((4990, "P"), 1)]
    assert cf.fly_legs(CHAIN, 5003.0, 10)[0] == ((5005, "C"), -1)


def test_the_condor_shorts_sit_at_least_the_offset_from_spot():
    legs = cf.condor_legs(CHAIN, SPOT, 10)
    assert legs == [((5005, "C"), -1), ((5000, "P"), -1), ((5015, "C"), 1), ((4990, "P"), 1)]
    # At spot 5000.5 the put at 5000 is inside the $1 offset, so the put short steps down to 4995.
    assert cf.condor_legs(CHAIN, 5000.5, 10)[1] == ((4995, "P"), -1)
    assert cf.condor_legs(chain({(5005, "C"): (1, 2)}), SPOT, 10) is None


def test_working_gives_up_half_the_way_from_mid_to_natural_and_natural_is_worst():
    legs = cf.fly_legs(CHAIN, SPOT, 10)
    mid = cf.credit(CHAIN, legs, "mid")
    assert mid == round(3.10 + 1.20 - 0.25 - 0.075, 4)
    natural = cf.credit(CHAIN, legs, "natural")
    assert natural == round(3.00 + 1.10 - 0.30 - 0.10, 4)
    assert cf.credit(CHAIN, legs, "working") == round((mid + natural) / 2, 4)
    assert cf.credit(CHAIN, legs, "mid_0.05") == round(mid - 0.05, 4)


def test_a_leg_that_is_not_two_sided_prices_to_none():
    c = dict(CHAIN)
    c[(5010, "C")] = {"bid": None, "ask": 0.3}
    assert cf.credit(c, cf.fly_legs(c, SPOT, 10), "working") is None
    missing = {k: v for k, v in CHAIN.items() if k != (4990, "P")}
    assert cf.credit(missing, cf.fly_legs(CHAIN, SPOT, 10), "working") is None


def test_settlement_owes_the_move_capped_at_the_wing_and_counts_itm_symbols():
    legs = cf.fly_legs(CHAIN, SPOT, 10)
    assert cf.settle(legs, 5000.0) == (0.0, 0)
    assert cf.settle(legs, 5003.0) == (3.0, 1)
    assert cf.settle(legs, 4996.0) == (4.0, 1)
    assert cf.settle(legs, 5030.0) == (10.0, 2)
    assert cf.settle(legs, 4970.0) == (10.0, 2)


def test_a_result_is_credit_less_what_is_owed_less_open_and_settlement_fees():
    legs = cf.fly_legs(CHAIN, SPOT, 10)
    pinned = cf.result(legs, 4.0, 5000.0)
    assert pinned["gross"] == 400.0 and pinned["itm"] == 0
    moved = cf.result(legs, 4.0, 5030.0)
    assert moved["gross"] == -600.0 and moved["itm"] == 2
    assert moved["cost"] - pinned["cost"] == 10.0  # two in-the-money symbols at $5 each
    assert pinned["net"] == round(pinned["gross"] - pinned["cost"], 2)


def test_a_session_without_its_entry_minute_or_a_short_is_skipped_with_a_reason():
    assert cf.run_session(None, 5000.0, arm="fly", wing=10, fill="mid") == {"skip": "no entry quote"}
    empty = cf.run_session((SPOT, {}), 5000.0, arm="fly", wing=10, fill="mid")
    assert empty == {"skip": "no short strike"}
    r = cf.run_session((SPOT, CHAIN), 5000.0, arm="condor", wing=10, fill="mid")
    assert r["shorts"] == [5000, 5005] and r["net"] > 0


def test_calendar_labels_mark_the_third_friday_and_the_months_last_session():
    assert cf.third_friday(date(2024, 3, 1)) == date(2024, 3, 15)
    assert cf.third_friday(date(2024, 11, 30)) == date(2024, 11, 15)
    month = {"2024-03-14", "2024-03-15", "2024-03-28"}
    assert cf.labels("2024-03-15", month) == {"weekday": "Fri", "monthly_expiry": True, "month_end": False}
    assert cf.labels("2024-03-28", month)["month_end"] is True
    assert cf.labels("2024-03-14", month)["monthly_expiry"] is False


def test_vix_bands_are_labelled_from_the_prior_close():
    assert [cf.vix_band(v) for v in (12, 15, 19.9, 25, 40)] == ["0-15", "15-20", "15-20", "25+", "25+"]


def _rows(nets: list[float], official_from: int = 0, start="2023-01-02") -> list[dict]:
    d0 = date.fromisoformat(start)
    return [
        {"day": (d0 + timedelta(days=i)).isoformat(), "credit": 4.0, "gross": n + 15.0, "net": n,
         "cost": 15.0, "settle_source": cf.OFFICIAL if i >= official_from else "thetadata:last_underlying"}
        for i, n in enumerate(nets)
    ]  # fmt: skip


def _judged(nets, official_from: int = 0):
    rows = _rows(nets, official_from)
    start, end = rows[0]["day"], rows[-1]["day"]
    tests = {a: cf.score(rows, start, end) for a in cf.ARMS}
    cf.judge(tests)
    return tests["fly"]


def test_a_strong_steady_edge_passes():
    assert _judged([60.0, 40.0] * 100)["verdict"] == "pass"


def test_fewer_than_100_sessions_is_no_verdict():
    assert _judged([60.0, 40.0] * 40)["verdict"] == "no verdict"


def test_too_few_official_close_sessions_is_no_verdict():
    r = _judged([60.0, 40.0] * 100, official_from=160)
    assert r["verdict"] == "no verdict" and "official-close" in r["why"]


def test_a_noisy_edge_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 100)
    assert r["verdict"] == "fail" and not r["checks"]["t_above_bar"]


def test_a_losing_second_half_fails():
    r = _judged([80.0, 60.0] * 60 + [-5.0, -1.0] * 60)
    assert r["verdict"] == "fail" and not r["checks"]["both_halves_positive"]


def test_an_edge_smaller_than_half_its_costs_fails_the_stress():
    r = _judged([9.0, 5.0] * 100)  # mean 7 against costs of 15: 1.5x costs takes 7.5 off
    assert r["verdict"] == "fail" and not r["checks"]["survives_1_5x_costs"]


def test_losing_on_the_official_close_sessions_fails():
    nets = [60.0, 40.0] * 70 + [-20.0, -10.0] * 30
    r = _judged(nets, official_from=140)
    assert r["verdict"] == "fail" and not r["checks"]["official_close_positive"]
    assert r["checks"]["mean_positive"]


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert cf.main(["--end", "2025-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
