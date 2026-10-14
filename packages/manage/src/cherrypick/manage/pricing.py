"""Black-Scholes pricing used to model a trade's payoff at its nearest expiry.

Every number here is MODELLED, and the evaluation labels it so: implied volatility is solved per leg
from its own mid, dividends are ignored, and a later-expiry leg is valued at the nearest expiry at its
current IV. That is enough to bound what a trade can still win or lose, which is all the rules ask of
it; it is not a price anyone can trade at.
"""

from __future__ import annotations

import math
from typing import Any

MULTIPLIER = 100.0


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def intrinsic(spot: float, strike: float, right: str) -> float:
    return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)


def price(spot: float, strike: float, years: float, vol: float, right: str, rate: float) -> float:
    if years <= 0 or vol <= 0 or spot <= 0:
        return intrinsic(spot, strike, right)
    root = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / root
    d2 = d1 - root
    if right == "C":
        return spot * _n(d1) - strike * math.exp(-rate * years) * _n(d2)
    return strike * math.exp(-rate * years) * _n(-d2) - spot * _n(-d1)


def delta(spot: float, strike: float, years: float, vol: float, right: str, rate: float) -> float | None:
    if years <= 0 or vol <= 0 or spot <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / (vol * math.sqrt(years))
    return _n(d1) if right == "C" else _n(d1) - 1.0


def implied_vol(
    spot: float, strike: float, years: float, mid: float, right: str, rate: float
) -> float | None:
    """The vol that prices `mid`, by bisection; None when no vol can (a mid below intrinsic, no time)."""
    if years <= 0 or mid is None or spot <= 0:
        return None
    if mid <= intrinsic(spot, strike, right) + 1e-9:
        return 0.0001
    lo, hi = 0.0001, 5.0
    if price(spot, strike, years, hi, right, rate) < mid:
        return None
    for _ in range(100):
        mid_vol = (lo + hi) / 2.0
        if price(spot, strike, years, mid_vol, right, rate) > mid:
            hi = mid_vol
        else:
            lo = mid_vol
    return (lo + hi) / 2.0


def leg_value_at(leg: dict[str, Any], spot: float, years_left: float, rate: float) -> float:
    """Signed dollar value of one leg at `spot` with `years_left` to its own expiry."""
    qty = float(leg["quantity"])
    if leg.get("right") not in ("C", "P"):
        return qty * spot  # stock or ETF shares
    vol = leg.get("iv")
    if years_left > 0 and vol:
        unit = price(spot, float(leg["strike"]), years_left, vol, leg["right"], rate)
    else:
        unit = intrinsic(spot, float(leg["strike"]), leg["right"])
    return qty * MULTIPLIER * unit


def payoff_profile(
    legs: list[dict[str, Any]], spot: float, horizon_years: float, pricing: dict[str, Any]
) -> dict:
    """The trade's modelled value at the horizon across a spot grid, and its extremes.

    `unbounded_low` / `unbounded_high` say the worst value sits at a grid edge and is still falling --
    a naked short, whose loss the grid cannot bound.
    """
    rate = float(pricing["rate"])
    lo, hi, steps = float(pricing["grid_low"]), float(pricing["grid_high"]), int(pricing["grid_steps"])
    grid = [spot * (lo + (hi - lo) * i / steps) for i in range(steps + 1)]

    def value(p: float) -> float:
        return sum(leg_value_at(leg, p, leg["years"] - horizon_years, rate) for leg in legs)

    values = [value(p) for p in grid]
    worst = min(range(len(values)), key=values.__getitem__)
    best = max(range(len(values)), key=values.__getitem__)
    return {
        "min_value": values[worst],
        "min_at": grid[worst],
        "max_value": values[best],
        "max_at": grid[best],
        "unbounded_low": worst == len(values) - 1 and values[-1] < values[-2],
        "unbounded_high": best == len(values) - 1 and values[-1] > values[-2],
    }
