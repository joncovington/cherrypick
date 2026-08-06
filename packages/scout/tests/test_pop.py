import math

import pytest

from cherrypick.scout.analytics.payoff import Leg, breakevens, payoff_at
from cherrypick.scout.analytics.pop import expected_move, norm_cdf, pop, prob_below

# S=100, K=100, sigma=0.2, T=0.25, r=0.05 -> d2 = 0.075 -> N(d2) ~= 0.5299 (textbook Black-Scholes
# constant; hand-verified: d2 = (ln(1) + (0.05 - 0.02) * 0.25) / (0.2 * 0.5) = 0.0075 / 0.1 = 0.075).
_S, _K, _SIGMA, _T, _R = 100.0, 100.0, 0.2, 0.25, 0.05
_N_D2 = 0.5299


def test_norm_cdf_matches_the_textbook_d2_constant():
    assert norm_cdf(0.075) == pytest.approx(_N_D2, abs=1e-4)


def test_prob_below_is_one_minus_n_d2_at_the_same_point():
    # prob_below = N(-d2) = 1 - N(d2)
    assert prob_below(_S, _K, _SIGMA, _T, _R) == pytest.approx(1 - _N_D2, abs=1e-4)


def test_prob_below_increases_monotonically_with_strike():
    low = prob_below(_S, 90.0, _SIGMA, _T, _R)
    mid = prob_below(_S, 100.0, _SIGMA, _T, _R)
    high = prob_below(_S, 110.0, _SIGMA, _T, _R)
    assert low < mid < high


def test_sigma_to_zero_limit_is_a_step_function_at_the_forward_price():
    forward = _S * math.exp(_R * _T)
    tiny_sigma = 1e-9
    assert prob_below(_S, forward - 1, tiny_sigma, _T, _R) == pytest.approx(0.0, abs=1e-6)
    assert prob_below(_S, forward + 1, tiny_sigma, _T, _R) == pytest.approx(1.0, abs=1e-6)


def test_t_zero_is_also_a_step_function_at_spot():
    assert prob_below(_S, _S - 1, _SIGMA, 0.0, _R) == 0.0
    assert prob_below(_S, _S + 1, _SIGMA, 0.0, _R) == 1.0


def _prob_of_loss(legs, spot, sigma, t, r):
    """The complement of `pop`: same interval scan, but summing where P/L is <= 0 instead of > 0 --
    written independently (not by calling `pop` and subtracting) so the parity check below is real."""
    breaks = sorted(b for b in breakevens(legs) if b > 0)
    bounds = [0.0, *breaks, math.inf]
    total = 0.0
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        probe = (lo + hi) / 2 if hi != math.inf else lo * 2 + 1.0
        if payoff_at(legs, probe) <= 0:
            lo_cdf = 0.0 if lo <= 0 else prob_below(spot, lo, sigma, t, r)
            hi_cdf = 1.0 if hi == math.inf else prob_below(spot, hi, sigma, t, r)
            total += hi_cdf - lo_cdf
    return total


def test_pop_and_prob_of_loss_sum_to_one_for_an_iron_condor():
    legs = [
        Leg(kind="call", quantity=-1, price=1.00, strike=110),
        Leg(kind="call", quantity=1, price=0.50, strike=115),
        Leg(kind="put", quantity=-1, price=1.00, strike=90),
        Leg(kind="put", quantity=1, price=0.50, strike=85),
    ]
    p_win = pop(legs, _S, _SIGMA, _T, _R)
    p_lose = _prob_of_loss(legs, _S, _SIGMA, _T, _R)
    assert p_win + p_lose == pytest.approx(1.0, abs=1e-9)
    assert 0.0 < p_win < 1.0


def test_pop_for_a_naked_short_put_is_prob_above_breakeven():
    legs = [Leg(kind="put", quantity=-1, price=2.00, strike=100)]
    breakeven = breakevens(legs)[0]
    assert breakeven == pytest.approx(98.0)
    expected = 1 - prob_below(_S, breakeven, _SIGMA, _T, _R)
    assert pop(legs, _S, _SIGMA, _T, _R) == pytest.approx(expected)


def test_pop_with_no_breakeven_is_zero_or_one():
    # A balanced butterfly (equal call counts on each wing -> both tail slopes are exactly zero)
    # bought for more than its maximum possible payout (the $5 width) is a guaranteed loss at every
    # spot price -- no sign change anywhere, so there is no breakeven at all.
    legs = [
        Leg(kind="call", quantity=1, price=6.0, strike=95),
        Leg(kind="call", quantity=-2, price=0.0, strike=100),
        Leg(kind="call", quantity=1, price=0.0, strike=105),
    ]
    assert breakevens(legs) == []
    assert pop(legs, _S, _SIGMA, _T, _R) == 0.0


def test_expected_move_matches_the_closed_form():
    assert expected_move(100.0, 0.2, 0.25) == pytest.approx(100.0 * 0.2 * math.sqrt(0.25))


def test_expected_move_at_zero_time_is_zero():
    assert expected_move(100.0, 0.2, 0.0) == 0.0
