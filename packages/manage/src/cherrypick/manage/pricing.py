"""Black-Scholes pricing used to model a trade's payoff at its nearest expiry.

Every number here is MODELLED, and the evaluation labels it so: implied volatility is solved per leg
from its own mid, dividends are ignored, and a later-expiry leg is valued at the nearest expiry at its
current IV. That is enough to bound what a trade can still win or lose, which is all the rules ask of
it; it is not a price anyone can trade at.

`price`, `delta`, `implied_vol` and `intrinsic` are `cherrypick.core.bs`'s, called here with no
dividend yield, which is plain Black-Scholes term for term.
"""

from __future__ import annotations

import math
from typing import Any

from cherrypick.core.bs import delta, implied_vol, intrinsic, price

__all__ = [
    "MULTIPLIER",
    "delta",
    "greeks",
    "implied_vol",
    "intrinsic",
    "leg_value_at",
    "payoff_profile",
    "price",
]

MULTIPLIER = 100.0


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def greeks(spot: float, strike: float, years: float, vol: float, right: str, rate: float) -> dict | None:
    """Per-share delta, gamma (delta per $1 of the underlying), theta ($ per calendar day, as the
    broker states it) and vega ($ per volatility point). None without time or a vol."""
    if years <= 0 or vol <= 0 or spot <= 0:
        return None
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    decay = -spot * pdf * vol / (2.0 * root_t)
    carry = rate * strike * math.exp(-rate * years)
    theta_year = decay - carry * _n(d2) if right == "C" else decay + carry * _n(-d2)
    return {
        "delta": _n(d1) if right == "C" else _n(d1) - 1.0,
        "gamma": pdf / (spot * vol * root_t),
        "theta": theta_year / 365.0,
        "vega": spot * pdf * root_t / 100.0,
    }


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
