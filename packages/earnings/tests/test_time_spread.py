"""The time-spread engine against the worked example in docs/time-spread-screen-plan.md.

Stock 100, base vol 30%, a one-day event priced at a 6% standard deviation, a 3-day front and a
31-day back. The doc's tables were worked with a normal approximation; these pin the engine's own
Black-Scholes numbers, which agree to within a few dollars.
"""

from math import exp, sqrt

import pytest

from cherrypick.earnings import time_spread as ts

SPOT, BASE, EVENT = 100.0, 0.30, 0.06
FRONT, BACK = 3.0, 31.0
EV = EVENT * EVENT


def _iv(days: float) -> float:
    t = days / ts.YEAR_DAYS
    return sqrt((BASE * BASE * t + EV) / t)


def _leg(qty, cp, strike, days):
    vol = _iv(days)
    return ts.Leg(qty, cp, strike, days, ts.bs_price(cp, SPOT, strike, days / ts.YEAR_DAYS, vol), vol)


def _straddle_calendar(strike=100.0):
    return [
        _leg(1, "C", strike, BACK),
        _leg(1, "P", strike, BACK),
        _leg(-1, "C", strike, FRONT),
        _leg(-1, "P", strike, FRONT),
    ]


def _call_calendar(strike):
    return [_leg(1, "C", strike, BACK), _leg(-1, "C", strike, FRONT)]


def test_the_event_split_recovers_the_base_vol_and_the_event():
    split = ts.event_variance(_iv(FRONT), FRONT, _iv(BACK), BACK)
    assert split["ok"]
    assert split["base_vol"] == pytest.approx(BASE)
    assert split["event_move"] == pytest.approx(EVENT)


def test_a_term_structure_with_no_event_is_refused():
    # A flat term structure: the front no richer than the back, so there is nothing to sell.
    assert ts.event_variance(0.30, FRONT, 0.30, BACK)["reason"] == "no_event_premium"
    # The back's total variance below the front's: no base vol can be left.
    assert ts.event_variance(1.50, FRONT, 0.20, BACK)["reason"] == "back_variance_not_above_front"
    assert ts.event_variance(0.5, BACK, 0.4, FRONT)["reason"] == "bad_inputs"


def test_bs_price_holds_put_call_parity_and_falls_to_intrinsic():
    c = ts.bs_price("C", 100, 95, 0.1, 0.3, rate=0.04)
    p = ts.bs_price("P", 100, 95, 0.1, 0.3, rate=0.04)
    assert c - p == pytest.approx(100 - 95 * exp(-0.04 * 0.1))
    assert ts.bs_price("C", 110, 100, 0.0, 0.3) == 10.0
    assert ts.bs_price("P", 110, 100, 0.0, 0.3) == 0.0


def test_the_straddle_calendar_pays_on_an_unchanged_stock_and_loses_on_a_gap():
    legs = _straddle_calendar()
    debit = sum(leg.qty * leg.price for leg in legs)
    assert debit == pytest.approx(3.20, abs=0.01)
    assert ts.position_pnl(legs, SPOT, 0.0, EV) == pytest.approx(189, abs=1)
    assert ts.position_pnl(legs, SPOT, 0.06, EV) < 0
    # A huge move takes nearly the whole debit, and never more.
    blowout = ts.position_pnl(legs, SPOT, 0.30, EV)
    assert -debit * ts.MULTIPLIER <= blowout < -0.9 * debit * ts.MULTIPLIER


def test_the_endurable_gap_sits_inside_the_expected_move():
    legs = _straddle_calendar()
    gap = ts.endurable_gap(legs, SPOT, EV)
    assert gap["ok"] and not gap["up_capped"] and not gap["down_capped"]
    assert gap["down"] == pytest.approx(0.0435, abs=0.001)
    assert gap["up"] == pytest.approx(0.0482, abs=0.001)
    # It is where P&L crosses zero, on both sides.
    assert ts.position_pnl(legs, SPOT, gap["up"], EV) == pytest.approx(0, abs=0.01)
    assert ts.position_pnl(legs, SPOT, -gap["down"], EV) == pytest.approx(0, abs=0.01)
    # The suite's expected move (0.85 x the front straddle) is wider than either side after costs.
    em = 0.85 * (legs[2].price + legs[3].price) / SPOT
    after_costs = ts.endurable_gap(legs, SPOT, EV, costs=25.0)
    assert max(after_costs["down"], after_costs["up"]) < em


def test_a_tolerated_loss_widens_the_endurable_gap():
    legs = _straddle_calendar()
    even = ts.endurable_gap(legs, SPOT, EV)
    loose = ts.endurable_gap(legs, SPOT, EV, loss_line=-80.0)
    assert loose["down"] > even["down"] and loose["up"] > even["up"]


def test_base_vol_falling_after_the_report_costs_the_long_leg():
    legs = _straddle_calendar()
    assert ts.position_pnl(legs, SPOT, 0.0, EV, base_shock=-0.10) == pytest.approx(138, abs=1)


def test_the_otm_call_calendar_is_lopsided():
    legs = _call_calendar(105.0)
    gap = ts.endurable_gap(legs, SPOT, EV)
    assert gap["ok"]
    assert gap["down"] < 0.01 < 0.10 < gap["up"]
    # Best near the strike, a loss on a fall.
    assert ts.position_pnl(legs, SPOT, 0.05, EV) > ts.position_pnl(legs, SPOT, 0.0, EV) > 0
    assert ts.position_pnl(legs, SPOT, -0.05, EV) < 0


def test_a_structure_losing_unchanged_has_no_endurable_interval_but_is_still_covered():
    # Far enough out that a still stock loses, while a rise to the strike pays.
    legs = _call_calendar(112.0)
    assert ts.endurable_gap(legs, SPOT, EV, costs=10.0)["reason"] == "loses_unchanged"
    cov = ts.coverage(legs, SPOT, EV, [0.0, 0.11, 0.12, -0.03], costs=10.0)
    assert cov["n"] == 4 and 0 < cov["covered"] < 4


def test_coverage_counts_each_past_gap_against_the_position():
    legs = _straddle_calendar()
    cov = ts.coverage(legs, SPOT, EV, [0.01, -0.02, 0.03, -0.08, 0.12, None])
    assert cov == {"n": 5, "covered": 3, "coverage": 0.6}
    assert ts.coverage(legs, SPOT, EV, [])["coverage"] is None


def test_screen_scores_bundle_the_plan_scores():
    legs = _straddle_calendar()
    out = ts.screen_scores(
        legs,
        SPOT,
        front_atm_iv=_iv(FRONT),
        front_days=FRONT,
        back_atm_iv=_iv(BACK),
        back_days=BACK,
        past_gaps=[0.02, -0.03, 0.01, -0.05],
    )
    assert out["ok"] and out["clamped_legs"] == 0
    assert out["coverage"]["coverage"] == 0.75
    assert out["cushion"] == pytest.approx(0.0435 / EVENT, abs=0.02)
    assert out["richness"] == pytest.approx(EVENT / 0.025)
    refused = ts.screen_scores(
        legs, SPOT, front_atm_iv=0.3, front_days=FRONT, back_atm_iv=0.3, back_days=BACK, past_gaps=[0.01]
    )
    assert refused == {"ok": False, "reason": "no_event_premium", "split": refused["split"]}


def test_a_wing_too_cheap_to_hold_the_event_is_floored_and_counted():
    cheap = ts.Leg(-1, "C", 120.0, FRONT, 0.05, 0.40)  # 40% front wing under a 6% event
    vol, clamped = ts.post_event_vol(cheap, EV)
    assert clamped and vol == ts.MIN_VOL
    assert ts.clamped_legs([cheap, *_straddle_calendar()], EV) == 1


def test_votes_and_the_ladder():
    assert ts.vote(None, -1, 1) == 0
    assert ts.vote(2, -1, 1) == 1 and ts.vote(-2, -1, 1) == -1 and ts.vote(0, -1, 1) == 0
    assert ts.vote(2, -1, 1, sign=-1) == -1  # a contrarian family
    assert ts.family_vote([1, 1, -1]) == 1 and ts.family_vote([1, -1]) == 0 and ts.family_vote([]) == 0

    def rung(**votes):
        return ts.side_rung(votes)

    assert rung(price=0, positioning=0, fundamentals=0) == {"rung": "neutral", "side": 0, "agreeing": 0}
    assert rung(price=1, positioning=-1, fundamentals=0)["rung"] == "neutral"
    assert rung(price=0, positioning=-1, fundamentals=0) == {"rung": "lean", "side": -1, "agreeing": 1}
    assert rung(price=1, positioning=1, fundamentals=0)["rung"] == "side"
    assert rung(price=1, positioning=1, fundamentals=1)["rung"] == "diagonal"


def test_structure_for_each_rung():
    assert ts.structure_for("neutral", diagonal_clears=True) == "neutral"
    assert ts.structure_for("lean", diagonal_clears=True) == "neutral"
    assert ts.structure_for("lean", diagonal_clears=True, lean_as="otm_calendar") == "otm_calendar"
    assert ts.structure_for("side", diagonal_clears=True) == "otm_calendar"
    assert ts.structure_for("diagonal", diagonal_clears=True) == "diagonal"
    assert ts.structure_for("diagonal", diagonal_clears=False) == "otm_calendar"
    with pytest.raises(ValueError):
        ts.structure_for("ladder", diagonal_clears=True)
