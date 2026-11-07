"""Black-Scholes pricing used to model a trade's payoff at its nearest expiry.

Every number here is MODELLED, and the evaluation labels it so: implied volatility is solved per leg
from its own mid with the name's dividend yield (`div_yield` on the leg: the year's dividends gone ex
over today's price, 0 when the snapshot has no record), and a later-expiry leg is valued at the nearest
expiry at its current IV. That is enough to bound what a trade can still win or lose, which is all the
rules ask of it; it is not a price anyone can trade at.

`price`, `delta`, `implied_vol` and `intrinsic` are `cherrypick.core.bs`'s (Black-Scholes-Merton:
European, with a continuous dividend yield). A call's modelled value is floored at its intrinsic, the
least an American call is worth: with a yield, a deep European call can price under it. The payoff
chart (`payoff.py`) prices American options properly (Bjerksund-Stensland) and can differ by cents.

Until 2026-11-07 the evaluation carried no dividend: on a high-yield name a long-dated call's mid sat
under the model's no-dividend floor (spot less the discounted strike), no vol priced it, and the leg
was left unmodelled.
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
    "model_price",
    "outliving_unpriced",
    "payoff_profile",
    "price",
    "price_band",
]

MULTIPLIER = 100.0


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def greeks(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> dict | None:
    """Per-share delta, gamma (delta per $1 of the underlying), theta ($ per calendar day, as the
    broker states it) and vega ($ per volatility point), at dividend yield `div`. None without time
    or a vol."""
    if years <= 0 or vol <= 0 or spot <= 0:
        return None
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - div + vol * vol / 2.0) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    held = math.exp(-div * years)  # the share less the dividends paid before expiry, to an option
    decay = -spot * held * pdf * vol / (2.0 * root_t)
    carry = rate * strike * math.exp(-rate * years)
    paid = div * spot * held
    if right == "C":
        theta_year = decay - carry * _n(d2) + paid * _n(d1)
    else:
        theta_year = decay + carry * _n(-d2) - paid * _n(-d1)
    return {
        "delta": held * _n(d1) if right == "C" else held * (_n(d1) - 1.0),
        "gamma": held * pdf / (spot * vol * root_t),
        "theta": theta_year / 365.0,
        "vega": spot * held * pdf * root_t / 100.0,
    }


def model_price(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> float:
    """One option's modelled price: Black-Scholes-Merton at yield `div`, a call floored at intrinsic."""
    unit = price(spot, strike, years, vol, right, rate, div)
    return max(unit, intrinsic(spot, strike, right)) if right == "C" else unit


def leg_value_at(leg: dict[str, Any], spot: float, years_left: float, rate: float) -> float:
    """Signed dollar value of one leg at `spot` with `years_left` to its own expiry, at the leg's
    `div_yield`."""
    qty = float(leg["quantity"])
    if leg.get("right") not in ("C", "P"):
        return qty * spot  # stock or ETF shares
    vol = leg.get("iv")
    if years_left > 0 and vol:
        q = float(leg.get("div_yield") or 0.0)
        unit = model_price(spot, float(leg["strike"]), years_left, vol, leg["right"], rate, q)
    else:
        unit = intrinsic(spot, float(leg["strike"]), leg["right"])
    return qty * MULTIPLIER * unit


def outliving_unpriced(legs: list[dict[str, Any]], horizon: str) -> bool:
    """Whether an option leg that outlives the `horizon` expiry has no IV.

    Such a leg is valued at the horizon at its intrinsic, its remaining time thrown away: a long-dated
    call under a near short read a best case of exactly the strike width. A profile over it is wrong, so
    callers model none."""
    return any(leg.get("right") and leg.get("iv") is None and leg["expiration"] > horizon for leg in legs)


def price_band(
    legs: list[dict[str, Any]], spot: float, horizon_years: float, rate: float, floor: float
) -> tuple[float | None, float | None] | None:
    """The underlying prices either side of the trade's modelled best at the horizon where its value
    falls to `floor` dollars: (low, high), a side None when the value never falls that far within half
    to one and a half times `spot`. None when the trade is worth under `floor` at its best. Modelled
    like `payoff_profile`, on a finer grid (0.05% of spot)."""
    steps = 2000
    grid = [spot * (0.5 + i / steps) for i in range(steps + 1)]
    values = [sum(leg_value_at(leg, p, leg["years"] - horizon_years, rate) for leg in legs) for p in grid]
    best = max(range(len(values)), key=values.__getitem__)
    if values[best] < floor:
        return None
    lo = next((grid[i + 1] for i in range(best - 1, -1, -1) if values[i] < floor), None)
    hi = next((grid[i - 1] for i in range(best + 1, len(grid)) if values[i] < floor), None)
    return (round(lo, 2) if lo else None, round(hi, 2) if hi else None)


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
        "min_value": round(values[worst], 2),
        "min_at": grid[worst],
        "max_value": round(values[best], 2),
        "max_at": grid[best],
        "unbounded_low": worst == len(values) - 1 and values[-1] < values[-2],
        "unbounded_high": best == len(values) - 1 and values[-1] > values[-2],
    }
