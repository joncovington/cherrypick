"""One gamma per strike (2026-10-09): a strike's call and put carry the out-of-the-money side's gamma,
so the net is gamma x (call OI - put OI) and quote noise in one side's implied vol cannot pick its sign.

The worked case is the one that showed it: SPX 7790 on 2026-10-09, more call than put open interest,
but a put gamma a shade higher than the call's. Netted with each side's own gamma the strike read
negative and became the chain's put wall; with one gamma it reads what the positions say.
"""

import pytest

from cherrypick.core import gex


def test_the_out_of_the_money_side_supplies_the_gamma():
    assert gex.strike_gamma(105, 100, call_gamma=0.02, put_gamma=0.03) == 0.02  # above spot: the call
    assert gex.strike_gamma(95, 100, call_gamma=0.02, put_gamma=0.03) == 0.03  # below spot: the put
    assert gex.strike_gamma(100, 100, call_gamma=0.02, put_gamma=0.03) == 0.02  # at spot: the call


def test_a_missing_side_falls_back_to_the_other():
    assert gex.strike_gamma(105, 100, call_gamma=None, put_gamma=0.03) == 0.03
    assert gex.strike_gamma(95, 100, call_gamma=0.02, put_gamma=0.0) == 0.02
    assert gex.strike_gamma(95, 100, call_gamma=None, put_gamma=None) == 0.0


def _chain(strike, call_gamma, put_gamma, call_oi, put_oi):
    entries = [
        {"strike_price": strike, "streamer_symbol": "C", "option_type": "C"},
        {"strike_price": strike, "streamer_symbol": "P", "option_type": "P"},
    ]
    greeks = {"C": {"gamma": call_gamma, "iv": 10.78}, "P": {"gamma": put_gamma, "iv": 10.76}}
    return entries, greeks, {"C": call_oi, "P": put_oi}


SPOT = 7803.0


def test_a_balanced_at_the_money_strike_takes_its_sign_from_open_interest_not_quote_noise():
    # 2425 calls against 2270 puts, but a put gamma 8% above the call's: netted side by side this is
    # negative; one gamma makes it positive, as the positions are.
    entries, greeks, oi = _chain(7790.0, 0.0097, 0.0105, 2425, 2270)
    assert 0.0097 * 2425 - 0.0105 * 2270 < 0  # the old arithmetic's sign
    profile = gex.compute_gex_profile(entries, greeks, oi, {}, SPOT)
    (row,) = profile["series"]
    want = gex.dollar_gamma(0.0105, 2425 - 2270, 100, SPOT)  # below spot: the put's gamma, both sides
    assert row["net_gex"] == pytest.approx(want, abs=1)
    assert row["call_gamma"] == 0.0097 and row["put_gamma"] == 0.0105  # the feed's own, kept for audit
    loop = gex.compute_gex(entries, greeks, oi, SPOT)
    assert loop["per_strike"][0]["net_gex"] == pytest.approx(want, abs=1)


def test_volume_flow_uses_the_same_one_gamma():
    entries, greeks, oi = _chain(7810.0, 0.0090, 0.0120, 100, 100)
    profile = gex.compute_gex_profile(entries, greeks, oi, {"C": 500, "P": 200}, SPOT)
    (row,) = profile["series"]
    assert row["net_gex"] == 0  # equal OI: no net, whatever the two quotes' vols
    assert row["net_gex_vol"] == pytest.approx(gex.dollar_gamma(0.0090, 300, 100, SPOT), abs=1)


def test_a_side_listed_under_two_roots_is_open_interest_weighted_in_the_trading_loop_read():
    entries = [
        {"strike_price": 7820.0, "streamer_symbol": "C1", "option_type": "C"},
        {"strike_price": 7820.0, "streamer_symbol": "C2", "option_type": "C"},
        {"strike_price": 7820.0, "streamer_symbol": "P1", "option_type": "P"},
    ]
    greeks = {"C1": {"gamma": 0.01}, "C2": {"gamma": 0.02}, "P1": {"gamma": 0.05}}
    out = gex.compute_gex(entries, greeks, {"C1": 100, "C2": 300, "P1": 50}, SPOT)
    g = (0.01 * 100 + 0.02 * 300) / 400  # above spot: the calls' weighted gamma, for both sides
    assert out["per_strike"][0]["net_gex"] == pytest.approx(gex.dollar_gamma(g, 350, 100, SPOT), abs=1)
