"""Unit tests for the pure butterfly math."""

import pytest

import fly


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


# --------------------------------------------------------------------------- floors
def legged_fly(net, fees=0.0):
    return {"kind": "fly", "side": "put", "center": 6000, "wing_width": 5,
            "net": net, "quantity": 1, "fees": fees}


def test_fly_held_for_a_credit_cannot_lose():
    position = legged_fly(1.05, fees=0.0)
    assert fly.position_floor(position) == 105.0
    assert fly.is_risk_free(position)
    for price in range(5900, 6101):
        assert fly.position_pnl(position, price) >= 105.0


def test_fees_can_flip_the_floor_negative():
    """The failure mode that actually matters. A thin credit legged in against two SPX fee stacks is
    NOT risk-free, and the module has to be able to say so rather than reporting the gross credit."""
    fees = fly.vertical_open_fee("SPX", 1) * 2
    thin = legged_fly(0.02, fees=fees)   # $2 of credit against ~$7 of fees
    assert fly.position_floor(thin) < 0
    assert not fly.is_risk_free(thin)

    fat = legged_fly(1.05, fees=fees)
    assert fly.is_risk_free(fat)


def test_short_vertical_floor_is_full_defined_risk():
    """The branch a legged entry lands in when the completion never happens — and the one it would be
    dishonest to describe as risk-free."""
    position = {"kind": "short_vertical", "side": "put", "center": 6000, "wing_width": 5,
                "net": 1.50, "quantity": 1, "fees": 0.0}
    assert fly.position_floor(position) == -350.0
    assert not fly.is_risk_free(position)


def test_position_floor_scales_with_quantity():
    single = legged_fly(1.00)
    triple = {**legged_fly(1.00), "quantity": 3}
    assert fly.position_floor(triple) == fly.position_floor(single) * 3


# --------------------------------------------------------------------------- book level
def test_book_of_credit_flies_holds_its_floor_everywhere():
    positions = [legged_fly(1.05), {**legged_fly(0.35), "center": 6040}]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is True
    assert result["unbounded_below"] is False
    assert result["worst"] == 140.0  # both flies out of the money: 105 + 35


def test_book_funded_by_a_short_vertical_is_only_green_in_a_band():
    """A book whose credit comes from an open short vertical is not unconditionally safe, however
    green the middle of the risk graph looks. This distinction is the module's whole reason to
    report a band alongside the floor."""
    positions = [
        {"kind": "short_vertical", "side": "put", "center": 6000, "wing_width": 5,
         "net": 1.45, "quantity": 1, "fees": 0.0},
        {**legged_fly(-0.50), "center": 6020},
    ]
    result = fly.book_floor(positions)
    assert result["floor_holds"] is False
    assert result["unbounded_below"] is True
    low, high = result["band"]
    assert low <= 6000 <= high


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
