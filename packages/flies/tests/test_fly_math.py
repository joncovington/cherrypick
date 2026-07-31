"""Unit tests for the pure butterfly math."""

import pytest

import fly


def test_slippage_frac_is_cores_single_source_of_truth():
    """One fill model across the suite: fly.py's slippage fraction is core's
    `slippage_frac_of_spread` by import, and the suite-calibrated value is 0.125
    (a deliberate change to the fill model must update core and this pin together)."""
    from cherrypick.core import fees as _fees

    assert fly.DEFAULT_SLIPPAGE_FRAC is _fees.DEFAULT_COSTS["slippage_frac_of_spread"]
    assert fly.DEFAULT_SLIPPAGE_FRAC == 0.125


# --------------------------------------------------------------------------- payoffs
@pytest.mark.parametrize("offset", [-100, -10, -5, -2.5, 0, 2.5, 5, 10, 100])
def test_fly_payoff_is_bounded_zero_to_width(offset):
    """The fact the entire strategy rests on: a long symmetric fly's expiry value lives in [0, W]."""
    payoff = fly.fly_payoff(6000, 5, 6000 + offset)
    assert 0.0 <= payoff <= 5.0


def test_fly_payoff_peaks_at_the_center():
    assert fly.fly_payoff(6000, 5, 6000) == 5.0
    assert fly.fly_payoff(6000, 5, 6005) == 0.0
    assert fly.fly_payoff(6000, 5, 5995) == 0.0
    assert fly.fly_payoff(6000, 5, 6002.5) == 2.5


def test_short_vertical_payoff_is_bounded_and_never_positive():
    assert fly.short_vertical_payoff("put", 6000, 5, 6100) == 0.0
    assert fly.short_vertical_payoff("put", 6000, 5, 5990) == -5.0
    assert fly.short_vertical_payoff("call", 6000, 5, 5900) == 0.0
    assert fly.short_vertical_payoff("call", 6000, 5, 6010) == -5.0
    assert fly.short_vertical_payoff("put", 6000, 5, 5998) == -2.0


def test_completing_direction_inverts_by_side():
    """Named and tested on its own because it is the easiest thing here to code backwards."""
    assert fly.completing_side_direction("put") == "up"
    assert fly.completing_side_direction("call") == "down"


# --------------------------------------------------------------------------- pricing
def q(bid, ask):
    return {"bid": bid, "ask": ask}


def test_vertical_credit_is_mid_minus_the_haircut():
    credit = fly.vertical_credit(q(3.0, 3.4), q(1.0, 1.4), slippage_frac=0.125)
    # mid 3.2 - 1.2 = 2.0; haircut 0.125 * (0.4 + 0.4) = 0.10
    assert credit == pytest.approx(1.90)


def test_vertical_debit_is_mid_plus_the_haircut():
    debit = fly.vertical_debit(q(3.0, 3.4), q(1.0, 1.4), slippage_frac=0.125)
    assert debit == pytest.approx(2.10)


def test_credit_and_debit_straddle_the_mid():
    """Selling always nets less than mid and buying always costs more — the haircut can never be a gift."""
    short_q, long_q = q(3.0, 3.4), q(1.0, 1.4)
    assert fly.vertical_credit(short_q, long_q) < fly.vertical_debit(short_q, long_q)


def test_fly_debit_charges_slippage_on_the_doubled_center():
    """The middle strike trades twice, so it concedes two spreads — charging one understates the cost
    of the leg carrying the most size."""
    debit = fly.fly_debit(q(1.0, 1.2), q(2.0, 2.2), q(3.4, 3.6), slippage_frac=0.125)
    # mid 1.1 - 2*2.1 + 3.5 = 0.40; haircut 0.125 * (0.2 + 2*0.2 + 0.2) = 0.10
    assert debit == pytest.approx(0.50)


def test_fly_close_credit_is_fly_debit_mirrored_below_mid():
    """Closing sells the fly back, so it nets LESS than mid by the same haircut fly_debit pays
    ABOVE mid to buy one — same asymmetry vertical_credit/vertical_debit already have."""
    lower_q, center_q, upper_q = q(1.0, 1.2), q(2.0, 2.2), q(3.4, 3.6)
    debit = fly.fly_debit(lower_q, center_q, upper_q, slippage_frac=0.125)
    credit = fly.fly_close_credit(lower_q, center_q, upper_q, slippage_frac=0.125)
    # mid 0.40 +/- haircut 0.10
    assert credit == pytest.approx(0.30)
    assert debit == pytest.approx(0.50)
    assert credit < debit


def test_fly_close_fee_matches_fly_open_fee_shape_at_the_closing_rate():
    """Same 4-contract/2-sell-contract shape as opening (the doubled centre trades twice), priced
    at tastytrade's close commission rather than open."""
    assert fly.fly_close_fee("SPX", 1) == pytest.approx(
        fly.fly_open_fee("SPX", 1) - 1.00 * 4, abs=0.01
    )  # open-only $1/contract commission is the only difference, same as ic_close_fee vs ic_open_fee


# --------------------------------------------------------------------------- exercise/assignment fee
def test_expire_fee_is_zero_with_no_itm_contracts():
    assert fly.expire_fee() == 0.0
    assert fly.expire_fee(0) == 0.0


def test_expire_fee_charges_five_dollars_per_itm_contract():
    assert fly.expire_fee(1) == 5.00
    assert fly.expire_fee(3) == 15.00


def test_itm_contracts_short_put_vertical_counts_each_itm_leg_once():
    position = {"kind": "short_vertical", "side": "put", "center": 6000, "wing_width": 5, "quantity": 1}
    assert fly.itm_contracts_at_settlement(position, 6100) == 0  # both legs OTM
    assert fly.itm_contracts_at_settlement(position, 5998) == 1  # only the short (center) ITM
    assert fly.itm_contracts_at_settlement(position, 5990) == 2  # both legs ITM (max loss zone)
    assert fly.itm_contracts_at_settlement(position, 6000) == 0  # exactly at the strike: not ITM


def test_itm_contracts_short_call_vertical_mirrors_put():
    position = {"kind": "short_vertical", "side": "call", "center": 6000, "wing_width": 5, "quantity": 1}
    assert fly.itm_contracts_at_settlement(position, 5900) == 0
    assert fly.itm_contracts_at_settlement(position, 6002) == 1
    assert fly.itm_contracts_at_settlement(position, 6010) == 2


def test_itm_contracts_fly_counts_the_doubled_center_twice():
    """A completed fly has 3 distinct strikes (center-W, center, center+W) but 4 contracts -- the
    centre carries 2 (one from the opening vertical, one from the completing vertical). For a put
    fly, as settlement moves down through the strikes the ITM count steps 0 -> 1 -> 3 -> 4: the
    centre's 2 contracts flip together (there is no "2" zone on its own), and the middle zone
    (between the wings, below centre) landing on 3 is exactly what a real live session hit
    (2026-07-30): a completed 740-center fly settled between its centre and lower wing, and
    tastytrade charged the $5 fee on 3 contracts, matching this shape."""
    position = {"kind": "fly", "side": "put", "center": 6000, "wing_width": 5, "quantity": 1}
    assert fly.itm_contracts_at_settlement(position, 6010) == 0  # beyond the upper wing: all OTM
    assert fly.itm_contracts_at_settlement(position, 6002) == 1  # between centre and upper wing
    assert fly.itm_contracts_at_settlement(position, 5998) == 3  # between lower wing and centre
    assert fly.itm_contracts_at_settlement(position, 4990) == 4  # beyond the lower wing: all 4 ITM


def test_itm_contracts_scales_with_quantity():
    position = {"kind": "fly", "side": "put", "center": 6000, "wing_width": 5, "quantity": 3}
    assert fly.itm_contracts_at_settlement(position, 4990) == 12


def test_assignment_fee_is_expire_fee_of_the_itm_count():
    position = {"kind": "short_vertical", "side": "put", "center": 6000, "wing_width": 5, "quantity": 1}
    assert fly.assignment_fee(position, 6100) == 0.0
    assert fly.assignment_fee(position, 5990) == 10.00


# --------------------------------------------------------------------------- floors
def legged_fly(net, fees=0.0):
    return {
        "kind": "fly",
        "side": "put",
        "center": 6000,
        "wing_width": 5,
        "net": net,
        "quantity": 1,
        "fees": fees,
    }


def test_fly_held_for_a_credit_cannot_lose():
    position = legged_fly(1.05, fees=0.0)
    # A fly's floor does NOT reserve the worst-case exercise fee (2026-07-30):
    # engine.evaluate_pre_close_exit closes an ITM fly ahead of expiry whenever that's cheaper
    # than the assignment fee, so going forward the realistic worst case is just the gross
    # credit net of trading fees -- see fly.position_floor's docstring for the full reasoning.
    assert fly.position_floor(position) == 105.0
    assert fly.is_risk_free(position)
    # position_pnl, unlike position_floor, still prices the exercise fee fresh at EVERY
    # hypothetical price (it's a pure expiry question, not a management-aware one) -- so an
    # UNMANAGED hold to raw expiry still bottoms out at position_floor minus the worst-case $20
    # assignment fee (4 contracts, out past the wings on both sides).
    for price in range(5900, 6101):
        assert fly.position_pnl(position, price) >= 85.0
    assert min(fly.position_pnl(position, p) for p in range(5900, 6101)) == pytest.approx(85.0)


def test_fees_can_flip_the_floor_negative():
    """The failure mode that actually matters. A thin credit legged in against two SPX fee stacks is
    NOT risk-free, and the module has to be able to say so rather than reporting the gross credit."""
    fees = fly.vertical_open_fee("SPX", 1) * 2
    thin = legged_fly(0.02, fees=fees)  # $2 of credit against ~$7 of fees
    assert fly.position_floor(thin) < 0
    assert not fly.is_risk_free(thin)

    fat = legged_fly(1.05, fees=fees)
    assert fly.is_risk_free(fat)


def test_short_vertical_floor_is_full_defined_risk():
    """The branch a legged entry lands in when the completion never happens — and the one it would be
    dishonest to describe as risk-free."""
    position = {
        "kind": "short_vertical",
        "side": "put",
        "center": 6000,
        "wing_width": 5,
        "net": 1.50,
        "quantity": 1,
        "fees": 0.0,
    }
    # -350.0 defined-risk max loss. Not reserving the worst-case exercise-assignment fee on top
    # (2026-07-30) -- engine.evaluate_pre_close_exit closes an ITM vertical ahead of expiry the
    # same way it does a fly, whenever that's cheaper than the fee; see position_floor's docstring.
    assert fly.position_floor(position) == -350.0
    assert not fly.is_risk_free(position)


def test_position_floor_scales_with_quantity():
    single = legged_fly(1.00)
    triple = {**legged_fly(1.00), "quantity": 3}
    assert fly.position_floor(triple) == fly.position_floor(single) * 3


# --------------------------------------------------------------------------- debit_first (Phase 1)
def test_debit_vertical_payoff_call_side_is_bounded_and_ramps_between_the_strikes():
    # +1 5995 call / -1 6000 call: worthless at/below 5995, maxed at/above 6000.
    assert fly.debit_vertical_payoff("call", 6000, 5, 5990) == 0.0
    assert fly.debit_vertical_payoff("call", 6000, 5, 5995) == 0.0
    assert fly.debit_vertical_payoff("call", 6000, 5, 6000) == 5.0
    assert fly.debit_vertical_payoff("call", 6000, 5, 6010) == 5.0
    assert fly.debit_vertical_payoff("call", 6000, 5, 5997.5) == 2.5


def test_debit_vertical_payoff_put_side_mirrors_call_side():
    # +1 6005 put / -1 6000 put: worthless at/above 6005, maxed at/below 6000.
    assert fly.debit_vertical_payoff("put", 6000, 5, 6010) == 0.0
    assert fly.debit_vertical_payoff("put", 6000, 5, 6005) == 0.0
    assert fly.debit_vertical_payoff("put", 6000, 5, 6000) == 5.0
    assert fly.debit_vertical_payoff("put", 6000, 5, 5990) == 5.0
    assert fly.debit_vertical_payoff("put", 6000, 5, 6002.5) == 2.5


def test_debit_first_completing_direction_inverts_completing_side_direction():
    """The whole point of offering debit_first alongside legged: the two monetize opposite drift
    regimes at the same centre."""
    for side in (fly.PUT, fly.CALL):
        assert fly.debit_first_completing_direction(side) != fly.completing_side_direction(side)


def debit_vertical(side="call", debit=1.0, fees=None):
    return {
        "kind": "long_vertical",
        "side": side,
        "center": 6000,
        "wing_width": 5,
        "net": -debit,
        "quantity": 1,
        "fees": fly.vertical_open_fee("SPX", 1) if fees is None else fees,
        "status": "open",
        "position_id": "D1",
    }


def test_long_vertical_floor_is_bounded_at_zero_never_negative_width():
    """Unlike a short vertical (-W) or a fly (0), a long vertical's worst case at expiry is 0 -- the
    debit paid is already spent, so the floor is the negative of what was paid, less fees and the
    conservative assignment-fee reserve (see position_floor's long_vertical branch)."""
    pos = debit_vertical(debit=1.0, fees=0.0)
    expected = -1.0 * fly.CONTRACT_MULTIPLIER - fly.expire_fee(1)
    assert fly.position_floor(pos) == pytest.approx(expected)
    assert not fly.is_risk_free(pos)


def test_long_vertical_floor_reserves_the_assignment_fee_unlike_a_fly():
    """A fly reserves nothing (evaluate_pre_close_exit is assumed to beat the fee); a long vertical
    is more conservative -- its cheapest dollar point is a leg barely ITM, where a 2-leg close's
    slippage beats the fee less reliably."""
    pos = debit_vertical(debit=0.0, fees=0.0)
    assert fly.position_floor(pos) == pytest.approx(-fly.expire_fee(1))


def test_itm_contracts_long_vertical_call_side():
    pos = debit_vertical(side="call")
    # Deep ITM through both call strikes (5995, 6000): 2 contracts.
    assert fly.itm_contracts_at_settlement(pos, 6010) == 2
    # Between the strikes: only the lower (5995) is ITM.
    assert fly.itm_contracts_at_settlement(pos, 5997) == 1
    # Below both strikes: 0.
    assert fly.itm_contracts_at_settlement(pos, 5990) == 0


def test_itm_contracts_long_vertical_put_side():
    pos = debit_vertical(side="put")
    # Deep ITM through both put strikes (6000, 6005): 2 contracts.
    assert fly.itm_contracts_at_settlement(pos, 5990) == 2
    assert fly.itm_contracts_at_settlement(pos, 6010) == 0


def test_position_pnl_long_vertical_uses_debit_vertical_payoff():
    pos = debit_vertical(side="call", debit=1.0, fees=0.0)
    pnl = fly.position_pnl(pos, 6010)  # fully ITM -> payoff = wing_width = 5
    # (net + payoff) * 100 - fees(0, before assignment) - assignment fee for 2 ITM legs
    assert pnl == pytest.approx((-1.0 + 5.0) * 100 - fly.expire_fee(2))


# --------------------------------------------------------------------------- iron completion (Phase 1b)
@pytest.mark.parametrize("offset", [-100, -10, -5, -2.5, 0, 2.5, 5, 10, 100])
def test_iron_fly_payoff_is_fly_payoff_shifted_down_by_width(offset):
    assert fly.iron_fly_payoff(6000, 5, 6000 + offset) == fly.fly_payoff(6000, 5, 6000 + offset) - 5


def test_iron_fly_payoff_bottoms_out_at_negative_width():
    assert fly.iron_fly_payoff(6000, 5, 6000) == 0.0  # peaks at 0, not 5
    assert fly.iron_fly_payoff(6000, 5, 6005) == -5.0
    assert fly.iron_fly_payoff(6000, 5, 5995) == -5.0
    assert fly.iron_fly_payoff(6000, 5, 6100) == -5.0  # bounded, does not keep falling


def iron_fly(net, fees=0.0):
    return {
        "kind": "iron_fly",
        "center": 6000,
        "wing_width": 5,
        "net": net,
        "quantity": 1,
        "fees": fees,
    }


def test_iron_fly_floor_is_the_two_credits_less_the_width():
    # Two credits summing to 6.00 against a 5-wide fly: floor = (6.00 - 5) * 100 - fees.
    pos = iron_fly(net=6.00, fees=0.0)
    assert fly.position_floor(pos) == pytest.approx(100.0)
    assert fly.is_risk_free(pos)


def test_iron_fly_floor_can_be_negative_unlike_a_same_type_fly():
    """The one honesty-critical difference from `fly`: two credits summed are NOT guaranteed to
    clear the width, so this floor can and does go negative -- storing this under kind='fly' would
    silently claim risk-free when it is not."""
    pos = iron_fly(net=3.00, fees=0.0)  # 3.00 < wing_width 5 -- genuinely NOT risk-free
    assert fly.position_floor(pos) == pytest.approx(-200.0)
    assert not fly.is_risk_free(pos)


def test_iron_fly_floor_flips_exactly_at_the_width_boundary():
    at_boundary = iron_fly(net=5.00, fees=0.0)
    assert fly.position_floor(at_boundary) == pytest.approx(0.0)
    assert fly.is_risk_free(at_boundary)


def test_itm_contracts_iron_fly_never_both_shorts_at_once():
    pos = iron_fly(net=6.00)
    # Deep past the put side: both put legs ITM, calls untouched.
    assert fly.itm_contracts_at_settlement(pos, 5990) == 2
    # Between the strikes but below centre: only the short put ITM.
    assert fly.itm_contracts_at_settlement(pos, 5998) == 1
    # At/above centre but below the call wing: only the short call ITM.
    assert fly.itm_contracts_at_settlement(pos, 6002) == 1
    # Deep past the call side: both call legs ITM.
    assert fly.itm_contracts_at_settlement(pos, 6010) == 2
    # Exactly at centre: neither short strike is crossed (strict ITM test).
    assert fly.itm_contracts_at_settlement(pos, 6000) == 0


def test_position_pnl_iron_fly_uses_iron_fly_payoff():
    pos = iron_fly(net=6.00, fees=0.0)
    pnl = fly.position_pnl(pos, 6002.5)  # payoff = fly_payoff(...) - 5 = 2.5 - 5 = -2.5
    # (net + payoff) * 100, less the $5 assignment fee (one ITM leg: the short call at 6002.5 > 6000)
    assert pnl == pytest.approx((6.00 - 2.5) * 100 - 5.0)


# --------------------------------------------------------------------------- bwb_roll (Phase 2)
def test_bwb_strikes_put_and_call():
    assert fly.bwb_strikes("put", 6000, 5, 15) == (6005, 6000, 5985)
    assert fly.bwb_strikes("call", 6000, 5, 15) == (5995, 6000, 6015)


def test_bwb_payoff_put_side_kinks_and_tail():
    # put BWB: near wing 6005 (protected/upside), far wing 5985 (risk/downside), width 5, tail -10
    assert fly.bwb_payoff("put", 6000, 5, 15, 6005) == 0.0  # at/above near wing: worthless
    assert fly.bwb_payoff("put", 6000, 5, 15, 6010) == 0.0  # further above: still worthless
    assert fly.bwb_payoff("put", 6000, 5, 15, 6000) == 5.0  # peaks at centre, same as a symmetric fly
    assert fly.bwb_payoff("put", 6000, 5, 15, 5995) == 0.0  # near-side zero, mirrors a symmetric fly
    assert fly.bwb_payoff("put", 6000, 5, 15, 5985) == pytest.approx(-10.0)  # far wing: the tail
    assert fly.bwb_payoff("put", 6000, 5, 15, 5900) == pytest.approx(-10.0)  # flat beyond the tail


def test_bwb_payoff_call_side_mirrors_put_side():
    assert fly.bwb_payoff("call", 6000, 5, 15, 5995) == 0.0
    assert fly.bwb_payoff("call", 6000, 5, 15, 6000) == 5.0
    assert fly.bwb_payoff("call", 6000, 5, 15, 6005) == 0.0
    assert fly.bwb_payoff("call", 6000, 5, 15, 6015) == pytest.approx(-10.0)
    assert fly.bwb_payoff("call", 6000, 5, 15, 6100) == pytest.approx(-10.0)


def bwb(side="put", credit=1.5, far_width=15, fees=0.0):
    return {
        "kind": "bwb",
        "side": side,
        "center": 6000,
        "wing_width": 5,
        "far_width": far_width,
        "net": credit,
        "quantity": 1,
        "fees": fees,
    }


def test_bwb_floor_is_the_real_negative_tail_plus_reserve():
    pos = bwb(credit=1.5, far_width=15, fees=0.0)  # tail = -(15-5) = -10
    expected = (1.5 - 10.0) * 100 - fly.expire_fee(4)
    assert fly.position_floor(pos) == pytest.approx(expected)
    assert not fly.is_risk_free(pos)


def test_itm_contracts_bwb_asymmetric_wings():
    pos = bwb(side="put")  # near 6005, far 5985
    assert fly.itm_contracts_at_settlement(pos, 6010) == 0  # above near wing: none ITM
    assert fly.itm_contracts_at_settlement(pos, 6002) == 1  # only short centre put ITM
    assert fly.itm_contracts_at_settlement(pos, 5990) == 3  # both near+centre ITM (2), far not yet
    assert fly.itm_contracts_at_settlement(pos, 5980) == 4  # deep past the far wing: all four


def test_position_pnl_bwb_uses_bwb_payoff():
    pos = bwb(credit=1.5, far_width=15, fees=0.0)
    pnl = fly.position_pnl(pos, 6000)  # payoff = 5.0 (peak, at centre)
    # (credit + payoff) * 100, less a $5 assignment fee: the near (put) wing at 6005 sits ABOVE
    # spot, so it's genuinely ITM even exactly at the centre -- same property a regular put fly's
    # upper wing has (S < strike is a strict test, and 6000 < 6005 is true).
    assert pnl == pytest.approx((1.5 + 5.0) * 100 - 5.0)


def test_book_floor_sees_the_bwb_tail_not_just_the_near_wing():
    """Regression: without the far strike in _scan_prices, book_floor's grid would stop at the
    near wing and never sample the true (negative) trough past the far wing."""
    positions = [bwb(credit=1.5, far_width=15, fees=0.0)]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is False
    assert result["worst"] < -500  # the tail, not the near-wing zero


# --------------------------------------------------------------------------- unknown kind (Phase 0 hardening)
def test_position_pnl_raises_on_unknown_kind():
    with pytest.raises(ValueError, match="unknown position kind"):
        fly.position_pnl({**legged_fly(1.00), "kind": "garbage"}, 6000)


def test_position_floor_raises_on_unknown_kind():
    with pytest.raises(ValueError, match="unknown position kind"):
        fly.position_floor({**legged_fly(1.00), "kind": "garbage"})


def test_itm_contracts_raises_on_unknown_kind():
    with pytest.raises(ValueError, match="unknown position kind"):
        fly.itm_contracts_at_settlement({**legged_fly(1.00), "kind": "garbage"}, 6000)


def test_scan_prices_raises_on_unknown_kind():
    with pytest.raises(ValueError, match="unknown position kind"):
        fly.book_floor([{**legged_fly(1.00), "kind": "garbage"}])


# --------------------------------------------------------------------------- book level
def test_book_of_credit_flies_holds_its_floor_everywhere():
    positions = [legged_fly(1.05), {**legged_fly(0.35), "center": 6040}]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is True
    assert result["unbounded_below"] is False
    # Far enough below BOTH centres (5995 and 6035, 40 apart), every leg of both flies is
    # simultaneously ITM: 105 + 35 gross, less $20 + $20 worst-case exercise fees.
    assert result["worst"] == 100.0


def test_book_funded_by_a_short_vertical_is_only_green_in_a_band():
    """A book whose credit comes from an open short vertical is not unconditionally safe, however
    green the middle of the risk graph looks. This distinction is the module's whole reason to
    report a band alongside the floor."""
    positions = [
        {
            "kind": "short_vertical",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 1.45,
            "quantity": 1,
            "fees": 0.0,
        },
        {**legged_fly(-0.50), "center": 6020},
    ]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is False
    assert result["unbounded_below"] is True
    low, high = result["band"]
    assert low <= 6000 <= high


def test_book_with_two_profit_zones_reports_a_band_that_never_spans_the_trough():
    """The strategy's own premise is a FOREST — several profit zones with losing troughs
    between them. The band must be one CONTIGUOUS non-negative zone (the one holding the
    payoff maximum), not a min/max over every green grid point, which would claim the
    floor holds across a trough where the book loses money."""
    positions = [
        # Two debit flies far enough apart that the region between them is negative.
        {
            "kind": "fly",
            "side": "call",
            "center": 6000,
            "wing_width": 5,
            "net": -1.00,
            "quantity": 1,
            "fees": 0.0,
        },  # peak +400 at 6000, -100 away
        {
            "kind": "fly",
            "side": "call",
            "center": 6100,
            "wing_width": 10,
            "net": -0.50,
            "quantity": 1,
            "fees": 0.0,
        },  # peak +950 at 6100, -50 away
    ]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is False
    # The trough between the flies is genuinely negative...
    assert fly.book_pnl(positions, 6050) < 0
    # ...and there are exactly two zones, neither containing the trough.
    assert len(result["bands"]) == 2
    for low, high in result["bands"]:
        assert not (low <= 6050 <= high)
    # The headline band is the zone around the payoff maximum (6100), and it must not
    # reach back across the trough toward the other fly's zone.
    low, high = result["band"]
    assert low <= 6100 <= high
    assert low > 6050
    # The two zones are the flies' own profitable neighborhoods.
    (a_low, a_high), (b_low, b_high) = sorted(result["bands"])
    assert a_low <= 6000 <= a_high
    assert b_low <= 6100 <= b_high


def test_single_zone_band_equals_its_only_zone():
    positions = [
        {
            "kind": "short_vertical",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 1.45,
            "quantity": 1,
            "fees": 0.0,
        },
        {**legged_fly(-0.50), "center": 6020},
    ]
    result = fly.book_floor(positions)
    assert len(result["bands"]) == 1
    assert result["band"] == result["bands"][0]


def test_book_negative_everywhere_has_no_band_and_no_zones():
    # A short vertical whose credit can't cover its own fees anywhere: net 0.01 credit,
    # huge fees — every grid point is negative.
    positions = [
        {
            "kind": "short_vertical",
            "side": "put",
            "center": 6000,
            "wing_width": 5,
            "net": 0.01,
            "quantity": 1,
            "fees": 500.0,
        }
    ]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is False
    assert result["band"] is None
    assert result["bands"] == []


@pytest.mark.parametrize(
    "centers",
    [
        (6000, 6010),
        (6000, 6040),
        (6000, 6100),
        (6000, 6200),
        (6000, 6015, 6120),
        (5950, 6000, 6050),
    ],
)
@pytest.mark.parametrize("net", [-1.00, -0.40, 0.20])
def test_band_endpoints_and_midpoint_are_never_negative(centers, net):
    """Property sweep over book shapes: whatever the zone structure, every reported band
    must be genuinely non-negative at its endpoints and midpoint — the exact claim the
    old min/max band violated whenever the forest had a trough."""
    positions = [
        {"kind": "fly", "side": "call", "center": c, "wing_width": 5, "net": net, "quantity": 1, "fees": 0.0}
        for c in centers
    ]
    result = fly.book_floor(positions)
    for low, high in result["bands"]:
        for x in (low, high, (low + high) / 2):
            assert fly.book_pnl(positions, x) >= -1e-6, f"band ({low}, {high}) claims non-negative at {x}"


def test_book_cash_splits_credits_debits_and_fees():
    positions = [legged_fly(1.05, fees=5.0), {**legged_fly(-0.20, fees=7.0), "center": 6015}]
    cash = fly.book_cash(positions)
    assert cash["credit_collected"] == 105.0
    assert cash["debits_paid"] == 20.0
    assert cash["fees"] == 12.0
    assert cash["net_cash"] == 73.0


def test_empty_book_is_trivially_flat():
    result = fly.book_floor([])
    assert result["worst"] == 0.0 and result["floor_holds"] is True
