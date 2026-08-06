"""Probability of profit -- lognormal, no scipy. Same import-clean posture as `payoff.py`.

`prob_below(K, spot, sigma, t, r)` is the standard Black-Scholes risk-neutral probability that the
underlying settles below `K` at expiry, `N(-d2)`. `pop` sums this over every spot interval where the
leg basket's P/L (from `payoff.py`) is positive, bounded by the position's own breakevens -- so it is
exact given the lognormal assumption, not a Monte Carlo approximation.
"""

from __future__ import annotations

import math

from .payoff import Leg, breakevens, payoff_at


def norm_cdf(x: float) -> float:
    """Standard normal CDF via `math.erf` -- the stdlib has no `scipy.stats.norm`, and this identity
    (`N(x) = (1 + erf(x/sqrt(2))) / 2`) is exact, not an approximation."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d2(spot: float, strike: float, sigma: float, t: float, r: float) -> float:
    """Standard Black-Scholes d2. As `t -> 0` or `sigma -> 0` the distribution degenerates to a point
    mass at the forward price -- handled as the limiting step function rather than dividing by zero."""
    if t <= 0 or sigma <= 0:
        forward = spot * math.exp(r * t) if t > 0 else spot
        if forward > strike:
            return math.inf
        if forward < strike:
            return -math.inf
        return 0.0
    return (math.log(spot / strike) + (r - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def prob_below(spot: float, strike: float, sigma: float, t: float, r: float) -> float:
    """P(S_T < strike) under the risk-neutral lognormal measure."""
    d2 = _d2(spot, strike, sigma, t, r)
    if d2 == math.inf:
        return 0.0
    if d2 == -math.inf:
        return 1.0
    return norm_cdf(-d2)


def expected_move(spot: float, sigma: float, t: float) -> float:
    """`spot * sigma * sqrt(t)` -- the one-standard-deviation dollar move, for chart bands."""
    return spot * sigma * math.sqrt(max(t, 0.0))


def _bounded_cdf(x: float, spot: float, sigma: float, t: float, r: float) -> float:
    if x <= 0:
        return 0.0
    if x == math.inf:
        return 1.0
    return prob_below(spot, x, sigma, t, r)


def pop(legs: list[Leg], spot: float, sigma: float, t: float, r: float) -> float:
    """Probability of profit: the lognormal probability mass over every spot interval (bounded by the
    position's own breakevens) where `payoff_at` is positive. A position with no breakeven at all
    (always profitable, or never) returns 1.0 or 0.0 accordingly -- no interval to integrate."""
    breaks = sorted(b for b in breakevens(legs) if b > 0)
    if not breaks:
        return 1.0 if payoff_at(legs, spot) > 0 else 0.0

    bounds = [0.0, *breaks, math.inf]
    total = 0.0
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        probe = (lo + hi) / 2 if hi != math.inf else lo * 2 + 1.0
        if payoff_at(legs, probe) > 0:
            total += _bounded_cdf(hi, spot, sigma, t, r) - _bounded_cdf(lo, spot, sigma, t, r)
    return total
