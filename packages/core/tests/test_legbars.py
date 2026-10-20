"""The screener's liquidity bars and the working price, shared by the readings and manage."""

from cherrypick.core import legbars

BARS = legbars.LEG_BARS
OK = {"bid": 2.00, "ask": 2.10, "open_interest": 500, "volume": 50}


def test_a_leg_passes_on_either_width_rule_and_needs_its_open_interest():
    assert legbars.leg_passes(OK, BARS)  # 4.9% of mid
    assert legbars.leg_passes({**OK, "bid": 0.18, "ask": 0.23}, BARS)  # 25% of mid, one nickel
    assert not legbars.leg_passes({**OK, "ask": 2.40}, BARS)
    assert not legbars.leg_passes({**OK, "open_interest": 99}, BARS)
    assert not legbars.leg_passes({**OK, "bid": 0.0}, BARS) and not legbars.leg_passes(None, BARS)


def test_volume_never_fails_a_leg_and_is_flagged_instead():
    thin = {**OK, "volume": 0}
    assert legbars.leg_passes(thin, BARS) and legbars.quiet(thin, BARS) and not legbars.quiet(OK, BARS)


def test_a_spread_costs_half_its_legs_widths_and_works_halfway():
    p = legbars.structure_prices([(OK, -1), ({"bid": 0.50, "ask": 0.60}, 1)])
    assert p == {"mid": 1.5, "natural": 1.4, "working": 1.45, "cost": 0.1}


def test_a_bought_wing_bid_at_zero_still_prices_but_a_sold_one_does_not():
    wing = {"bid": 0.0, "ask": 0.05, "open_interest": 500}
    assert legbars.structure_prices([(OK, -1), (wing, 1)]) is not None
    assert legbars.structure_prices([(wing, -1), (OK, 1)]) is None


def test_a_spread_passes_on_its_own_cost_not_its_legs():
    cheap = {"bid": 0.50, "ask": 0.56, "open_interest": 500, "volume": 0}  # 11% of mid alone
    assert not legbars.leg_passes(cheap, BARS)
    assert legbars.structure_passes([(OK, -1), (cheap, 1)], BARS)  # 0.08 on 1.52: 5.3%
    wide = {**OK, "ask": 2.30}
    assert not legbars.structure_passes([(wide, -1), (cheap, 1)], BARS)  # 0.18 on 1.62: 11%
    assert not legbars.structure_passes([(OK, -1), ({**cheap, "open_interest": 10}, 1)], BARS)


def test_one_leg_is_judged_as_a_leg():
    assert legbars.structure_passes([(OK, -1)], BARS)
    assert not legbars.structure_passes([({**OK, "ask": 2.40}, -1)], BARS)


def test_the_working_premium_of_one_option_is_the_mid_less_a_quarter_width():
    assert legbars.working_premium(OK) == 2.025
    assert legbars.working_premium({"bid": 0.0, "ask": 1.0}) is None
