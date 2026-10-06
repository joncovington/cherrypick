"""Each arm is planned from its own merged params (2026-10-06, when `near` joined).

Until then every non-hook arm reused control's plan, so an arm that declared a different entry rule
(near: the 0.40-delta short) would have traded control's spread under its own name. Arms whose
params resolve the same still share ONE plan -- control and noflip must stay byte-identical until a
flip fires, which is the whole noflip comparison.
"""

from cherrypick.curve import engine, paper_loop


def _entry(strike, sym):
    return {
        "strike_price": strike,
        "streamer_symbol": sym,
        "occ_symbol": f"VXX   261120C{int(strike * 1000):08d}",
        "option_type": "call",
    }


def _snapshot():
    chain = [_entry(20, "s20"), _entry(25, "s25"), _entry(30, "s30"), _entry(35, "s35")]
    quotes = {
        "s20": {"bid": 5.9, "ask": 6.1, "mid": 6.0},
        "s25": {"bid": 2.4, "ask": 2.6, "mid": 2.5},
        "s30": {"bid": 0.9, "ask": 1.1, "mid": 1.0},
        "s35": {"bid": 0.36, "ask": 0.44, "mid": 0.40},
    }
    greeks = {"s20": {"delta": 0.70}, "s25": {"delta": 0.42}, "s30": {"delta": 0.29}, "s35": {"delta": 0.12}}
    return {
        "symbol": "VXX",
        "spot": 18.0,
        "expiration": "2026-11-20",
        "dte": 46,
        "chain": chain,
        "quotes": quotes,
        "greeks": greeks,
    }


CONFIG = {
    "defaults": {
        "short_delta_target": 0.30,
        "spread_width": 5.0,
        "min_credit_pct_of_width": 0.10,
        "max_leg_spread_pct": 0.25,
        "quantity": 1,
    },
    "books": {
        "control": {"enabled": True},
        "noflip": {"enabled": True},
        "near": {"short_delta_target": 0.40},
    },
}


def test_near_is_planned_on_its_own_short_and_control_noflip_share_one_plan():
    plans = paper_loop.plan_arms(
        _snapshot(), CONFIG, ["control", "noflip", "near"], advised={}, decision=None
    )
    assert plans["control"]["plan"]["short_strike"] == 30.0  # the 0.29-delta strike
    assert plans["near"]["plan"]["short_strike"] == 25.0  # the 0.42-delta strike
    assert plans["noflip"] is plans["control"]  # one plan, not two equal ones


def test_near_carries_the_flip_exit_like_control():
    assert "near" in engine.ARMS
    from cherrypick.curve import management

    assert "near" in management.FLIP_BOOKS
