"""Black-Scholes-Merton (cherrypick.core.bs): plain Black-Scholes at zero yield, Merton above it."""

from __future__ import annotations

import math

import pytest

from cherrypick.core import bs


def _plain(spot, strike, years, vol, right, rate):
    """Black-Scholes as manage computed it before the move, for the zero-yield comparison."""
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if right == "C":
        return spot * n(d1) - strike * math.exp(-rate * years) * n(d2)
    return strike * math.exp(-rate * years) * n(-d2) - spot * n(-d1)


@pytest.mark.parametrize("right", ["C", "P"])
def test_zero_yield_is_plain_black_scholes_term_for_term(right):
    for spot, strike, years, vol in ((100, 95, 0.1, 0.3), (50, 60, 1.5, 0.45), (500, 500, 0.02, 0.15)):
        assert bs.price(spot, strike, years, vol, right, 0.04) == pytest.approx(
            _plain(spot, strike, years, vol, right, 0.04), rel=1e-12
        )


def test_put_call_parity_holds_with_a_dividend_yield():
    s, k, t, v, r, q = 100.0, 105.0, 0.75, 0.25, 0.04, 0.03
    c = bs.price(s, k, t, v, "C", r, q)
    p = bs.price(s, k, t, v, "P", r, q)
    assert c - p == pytest.approx(s * math.exp(-q * t) - k * math.exp(-r * t), abs=1e-9)


@pytest.mark.parametrize("right", ["C", "P"])
def test_the_solved_vol_prices_the_mid_back(right):
    mid = bs.price(80, 85, 0.3, 0.32, right, 0.04, 0.02)
    vol = bs.implied_vol(80, 85, 0.3, mid, right, 0.04, 0.02)
    assert vol == pytest.approx(0.32, abs=1e-6)


def test_a_high_yield_long_call_needs_the_yield_to_be_priced():
    # TLT 2026-10-16: January 2029 80 call at 7.05, the fund at 80.16, 826 days, ~4.3% yield.
    years = 826 / 365
    plain = bs.implied_vol(80.16, 80, years, 7.05, "C", 0.04)
    assert plain < 0.01 and bs.delta(80.16, 80, years, plain, "C", 0.04) == pytest.approx(1.0, abs=1e-3)
    vol = bs.implied_vol(80.16, 80, years, 7.05, "C", 0.04, 0.043)
    assert 0.1 < vol < 0.25
    assert bs.delta(80.16, 80, years, vol, "C", 0.04, 0.043) == pytest.approx(0.49, abs=0.01)


def test_no_time_or_no_mid_solves_nothing():
    assert bs.implied_vol(100, 100, 0, 1.0, "C", 0.04) is None
    assert bs.implied_vol(100, 100, 0.1, None, "C", 0.04) is None
    assert bs.delta(100, 100, 0.1, 0.0, "C", 0.04) is None
    assert bs.prob_itm(100, 110, 0.0, 0.3, "C", 0.04) is None
    assert bs.prob_itm(100, 110, 0.1, 0.0, "P", 0.04) is None


@pytest.mark.parametrize("q", [0.0, 0.03])
def test_the_chance_of_finishing_in_the_money_is_n_d2_and_the_two_sides_sum_to_one(q):
    s, k, t, v, r = 100.0, 110.0, 30 / 365, 0.30, 0.04
    call, put = bs.prob_itm(s, k, t, v, "C", r, q), bs.prob_itm(s, k, t, v, "P", r, q)
    assert call + put == pytest.approx(1.0, abs=1e-12)
    d2 = (math.log(s / k) + (r - q - v * v / 2) * t) / (v * math.sqrt(t))
    assert call == pytest.approx(0.5 * (1 + math.erf(d2 / math.sqrt(2))), abs=1e-12)


def test_the_chance_is_not_delta():
    """A 0.15-delta call finishes in the money less often than 15%, a 0.15-delta put more often."""
    t, v, r = 30 / 365, 0.30, 0.04
    assert bs.delta(100, 110.09, t, v, "C", r) == pytest.approx(0.15, abs=1e-3)
    assert bs.prob_itm(100, 110.09, t, v, "C", r) == pytest.approx(0.131, abs=1e-3)
    assert bs.delta(100, 92.11, t, v, "P", r) == pytest.approx(-0.15, abs=1e-3)
    assert bs.prob_itm(100, 92.11, t, v, "P", r) == pytest.approx(0.171, abs=1e-3)
