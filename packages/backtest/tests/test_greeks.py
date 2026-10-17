"""Gamma from IV, checked against the second difference of the Black-Scholes price it comes from."""

from __future__ import annotations

import math

import pytest

from cherrypick.backtest import greeks


def _call(spot, strike, iv, t):
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return spot * n(d1) - strike * n(d2)


@pytest.mark.parametrize(("strike", "iv", "hours"), [(6000, 0.15, 6.5), (6050, 0.20, 2.0), (5900, 0.30, 0.5)])
def test_gamma_is_the_second_derivative_of_the_price(strike, iv, hours):
    spot, t, h = 6000.0, greeks.year_fraction(hours * 3600), 0.5
    numeric = (
        _call(spot + h, strike, iv, t) - 2 * _call(spot, strike, iv, t) + _call(spot - h, strike, iv, t)
    ) / (h * h)
    assert greeks.bs_gamma(spot, strike, iv, t) == pytest.approx(numeric, rel=1e-3)


def test_no_iv_no_gamma():
    t = greeks.year_fraction(3600)
    assert greeks.bs_gamma(6000, 6000, 0.0, t) is None
    assert greeks.bs_gamma(6000, 6000, None, t) is None
    assert greeks.bs_gamma(0, 6000, 0.2, t) is None


def test_time_is_floored_at_a_minute():
    assert greeks.year_fraction(0) == greeks.year_fraction(60) > 0
