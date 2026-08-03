import pytest

from cherrypick.scout.analytics.payoff import (
    Leg,
    breakevens,
    max_loss,
    max_profit,
    net_greeks,
    payoff_at,
    payoff_curve,
)


def _put_credit_spread():
    # Sell the 100 put for 2.00, buy the 95 put for 1.00 -- $1.00 net credit, $5 wide.
    return [
        Leg(kind="put", quantity=-1, price=2.00, strike=100),
        Leg(kind="put", quantity=1, price=1.00, strike=95),
    ]


def test_put_credit_spread_max_profit_is_the_credit():
    result = max_profit(_put_credit_spread())
    assert result["unbounded"] is False
    assert result["value"] == pytest.approx(100.0)  # credit ($1.00) * 100


def test_put_credit_spread_max_loss_is_width_minus_credit():
    result = max_loss(_put_credit_spread())
    assert result["unbounded"] is False
    assert result["value"] == pytest.approx(-400.0)  # (width 5 - credit 1) * 100


def test_put_credit_spread_breakeven_is_short_strike_minus_credit():
    breaks = breakevens(_put_credit_spread())
    assert breaks == pytest.approx([99.0])  # 100 - 1.00 credit


def test_put_credit_spread_floor_is_flat_below_the_long_strike():
    legs = _put_credit_spread()
    assert payoff_at(legs, 95) == payoff_at(legs, 0.0) == pytest.approx(-400.0)


def _iron_condor():
    return [
        Leg(kind="call", quantity=-1, price=1.00, strike=110),
        Leg(kind="call", quantity=1, price=0.50, strike=115),
        Leg(kind="put", quantity=-1, price=1.00, strike=90),
        Leg(kind="put", quantity=1, price=0.50, strike=85),
    ]


def test_iron_condor_has_two_breakevens():
    breaks = breakevens(_iron_condor())
    assert breaks == pytest.approx([89.0, 111.0])


def test_iron_condor_max_profit_is_the_total_credit():
    result = max_profit(_iron_condor())
    assert result["unbounded"] is False
    assert result["value"] == pytest.approx(100.0)  # (0.50 + 0.50) * 100


def test_iron_condor_max_loss_is_the_same_on_both_wings():
    result = max_loss(_iron_condor())
    assert result["unbounded"] is False
    assert result["value"] == pytest.approx(-400.0)  # (width 5 - credit 1) * 100 either side


def test_iron_condor_curve_is_flat_between_the_short_strikes():
    legs = _iron_condor()
    assert payoff_at(legs, 95) == payoff_at(legs, 100) == payoff_at(legs, 105) == pytest.approx(100.0)


def test_naked_short_call_has_unbounded_loss():
    legs = [Leg(kind="call", quantity=-1, price=2.00, strike=100)]
    result = max_loss(legs)
    assert result["unbounded"] is True
    assert result["value"] is None
    # The other side is bounded -- capped at the premium collected.
    assert max_profit(legs) == {"value": pytest.approx(200.0), "unbounded": False}


def test_long_call_has_unbounded_profit():
    legs = [Leg(kind="call", quantity=1, price=2.00, strike=100)]
    result = max_profit(legs)
    assert result["unbounded"] is True
    assert result["value"] is None
    assert max_loss(legs) == {"value": pytest.approx(-200.0), "unbounded": False}


def test_long_call_breakeven_is_strike_plus_premium():
    legs = [Leg(kind="call", quantity=1, price=2.00, strike=100)]
    assert breakevens(legs) == pytest.approx([102.0])


def test_covered_call_stock_leg_participates_below_the_strike():
    # Long 100 shares at 90, short the 100 call for 3.00 -- upside capped, downside is just long stock.
    legs = [
        Leg(kind="stock", quantity=1, price=90.0),
        Leg(kind="call", quantity=-1, price=3.00, strike=100),
    ]
    assert payoff_at(legs, 80) == pytest.approx((80 - 90) * 100 + 3.00 * 100)
    assert max_profit(legs)["value"] == pytest.approx((100 - 90) * 100 + 3.00 * 100)
    assert max_profit(legs)["unbounded"] is False


def test_payoff_curve_has_one_point_per_distinct_strike():
    legs = _iron_condor()
    curve = payoff_curve(legs)
    assert [pt["spot"] for pt in curve] == [85, 90, 110, 115]


def test_net_greeks_sums_quantity_weighted():
    legs = [
        Leg(kind="call", quantity=-1, price=1.0, strike=100, delta=0.30, gamma=0.02, theta=-0.05),
        Leg(kind="call", quantity=1, price=0.5, strike=105, delta=0.15, gamma=0.01, theta=-0.03),
    ]
    greeks = net_greeks(legs)
    assert greeks["delta"] == pytest.approx((-0.30 + 0.15) * 100)
    assert greeks["gamma"] == pytest.approx((-0.02 + 0.01) * 100)
    assert greeks["theta"] == pytest.approx((0.05 - 0.03) * 100)
    assert greeks["vega"] is None  # not a single leg supplied it


def test_net_greeks_stock_leg_has_implicit_delta_one():
    legs = [Leg(kind="stock", quantity=2, price=90.0)]
    greeks = net_greeks(legs)
    assert greeks["delta"] == pytest.approx(200.0)
    assert greeks["gamma"] is None


def test_net_greeks_with_no_greeks_anywhere_is_all_none():
    legs = [Leg(kind="call", quantity=1, price=1.0, strike=100)]
    assert net_greeks(legs) == {"delta": None, "gamma": None, "theta": None, "vega": None}
