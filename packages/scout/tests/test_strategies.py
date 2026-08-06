from datetime import date

import pytest

from cherrypick.scout.analytics import strategies as _strategies


def _opt(strike, option_type, mid):
    return {
        "symbol": f"TEST {option_type}{strike}",
        "strike": strike,
        "option_type": option_type,
        "quote": {"bid": mid - 0.05, "ask": mid + 0.05, "mid": mid, "mark": mid},
    }


EXP = date(2027, 3, 19)


def _chain():
    # Spot ~100. Puts get cheaper below spot as strike drops (further OTM); calls cheaper above.
    return [
        _opt(85, "P", 0.50),
        _opt(90, "P", 1.00),
        _opt(95, "P", 2.00),
        _opt(105, "C", 2.00),
        _opt(110, "C", 1.00),
        _opt(115, "C", 0.50),
    ]


def test_put_credit_spread_picks_nearest_otm_by_expected_move_and_prices_at_haircut_mid():
    result = _strategies.put_credit_spread(
        _chain(), spot=100, expected_move=5, wing_width_pct=0.05, expiration=EXP, dte=30
    )
    assert result is not None
    assert result["strategy"] == "put_credit_spread"
    short_leg = next(leg for leg in result["legs"] if leg["quantity"] < 0)
    long_leg = next(leg for leg in result["legs"] if leg["quantity"] > 0)
    assert short_leg["strike"] == 95  # nearest to spot - expected_move (95)
    assert long_leg["strike"] == 90  # nearest to short_strike - width (95 * 0.05 ~= 4.75 -> 90)
    assert result["credit"] == pytest.approx((2.00 - 1.00) * 0.90 * 100)
    assert result["dte"] == 30
    assert result["expiration"] == "2027-03-19"


def test_call_credit_spread_mirrors_the_put_side():
    result = _strategies.call_credit_spread(
        _chain(), spot=100, expected_move=5, wing_width_pct=0.05, expiration=EXP, dte=30
    )
    assert result is not None
    short_leg = next(leg for leg in result["legs"] if leg["quantity"] < 0)
    long_leg = next(leg for leg in result["legs"] if leg["quantity"] > 0)
    assert short_leg["strike"] == 105
    assert long_leg["strike"] == 110
    assert result["credit"] == pytest.approx((2.00 - 1.00) * 0.90 * 100)


def test_short_put_is_a_single_leg_priced_at_haircut_mid():
    result = _strategies.short_put(_chain(), spot=100, expected_move=5, expiration=EXP, dte=30)
    assert result is not None
    assert len(result["legs"]) == 1
    assert result["legs"][0]["strike"] == 95
    assert result["credit"] == pytest.approx(2.00 * 0.90 * 100)


def test_short_put_max_risk_is_bounded_at_the_strike_not_unbounded():
    result = _strategies.short_put(_chain(), spot=100, expected_move=5, expiration=EXP, dte=30)
    # A short put's worst case at spot=0 is large but finite (strike * 100 - credit), never unbounded.
    assert result["max_risk"] is not None
    assert result["max_risk"] > 0


def test_covered_call_includes_a_stock_leg():
    result = _strategies.covered_call(
        _chain(), spot=100, expected_move=5, expiration=EXP, dte=30, stock_price=95.0
    )
    assert result is not None
    kinds = {leg["kind"] for leg in result["legs"]}
    assert kinds == {"stock", "call"}
    assert result["max_risk"] is not None  # bounded: stock can only fall to 0


def test_put_credit_spread_returns_none_when_no_wing_available():
    # Only one put strike exists -- there's nothing to buy for protection.
    chain = [_opt(95, "P", 2.00)]
    assert (
        _strategies.put_credit_spread(
            chain, spot=100, expected_move=5, wing_width_pct=0.05, expiration=EXP, dte=30
        )
        is None
    )


def test_put_credit_spread_returns_none_for_a_debit_not_a_credit():
    # Deliberately price the long leg richer than the short -- a debit, which is not this strategy.
    chain = [_opt(95, "P", 1.00), _opt(90, "P", 2.00)]
    assert (
        _strategies.put_credit_spread(
            chain, spot=100, expected_move=5, wing_width_pct=0.05, expiration=EXP, dte=30
        )
        is None
    )


def test_directional_edge_is_call_mid_minus_put_mid_at_matched_distance():
    edge = _strategies.directional_edge(_chain(), spot=100, expected_move=5)
    # short_strike("call") = 105 (mid 2.00), short_strike("put") = 95 (mid 2.00) -> edge == 0 here.
    assert edge == pytest.approx(0.0)


def test_directional_edge_is_none_without_both_sides():
    calls_only = [_opt(105, "C", 2.00)]
    assert _strategies.directional_edge(calls_only, spot=100, expected_move=5) is None


def test_composite_score_rewards_higher_return_on_risk_pop_and_iv_rank():
    low = _strategies.composite_score(return_on_risk=0.1, pop=0.5, iv_rank_frac=0.3, liquidity_rating=4)
    high = _strategies.composite_score(return_on_risk=0.3, pop=0.7, iv_rank_frac=0.6, liquidity_rating=4)
    assert high > low


def test_composite_score_floors_secondary_factors_so_they_never_zero_out_the_score():
    score = _strategies.composite_score(return_on_risk=0.2, pop=0.0, iv_rank_frac=0.0, liquidity_rating=0)
    assert score > 0
