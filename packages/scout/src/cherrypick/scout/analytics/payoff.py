"""Generic leg-list -> payoff engine. Import-clean (stdlib + dataclasses only, no I/O) so a future
promotion to `cherrypick.core.payoff` is a file move once stable -- see the package README.

A `Leg` is priced per contract (1 contract = 100 shares, including a "stock" leg, matching how a
covered call/put is normally sized). `quantity` is signed: positive = long, negative = short.
`price` is what was paid per share (a positive number for a debit, i.e. what you paid to open it) --
P/L at expiry is `(intrinsic_value - price) * quantity * 100`, which works uniformly whether the
leg is long or short: a short leg's negative quantity flips the sign so *receiving* premium (price)
becomes a profit when the option expires worthless.

`payoff_curve`/`breakevens`/`max_profit`/`max_loss` all key off the same insight: an option payoff is
*exactly* piecewise-linear in the underlying, with kinks only at strikes. So the curve only needs to be
evaluated at the strikes themselves (an exact representation, not a dense approximation), and
breakevens/extrema follow from those points plus the two tail slopes (computable directly from the
legs, since a call/put is either fully ITM or fully OTM beyond every strike).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

_GREEK_FIELDS = ("delta", "gamma", "theta", "vega")


@dataclass(frozen=True)
class Leg:
    kind: str  # "call" | "put" | "stock"
    quantity: int  # signed: positive = long, negative = short
    price: float  # per share, what was paid (positive) or received (negative flows from quantity sign)
    strike: float | None = None  # None for "stock"
    expiration: date | None = None  # None for "stock"
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


def _intrinsic(leg: Leg, spot: float) -> float:
    if leg.kind == "call":
        return max(0.0, spot - leg.strike)
    if leg.kind == "put":
        return max(0.0, leg.strike - spot)
    if leg.kind == "stock":
        return spot
    raise ValueError(f"unknown leg kind: {leg.kind!r}")


def payoff_at(legs: list[Leg], spot: float) -> float:
    """Total P/L (dollars) across every leg if the underlying settles at `spot`."""
    return sum((_intrinsic(leg, spot) - leg.price) * leg.quantity * 100 for leg in legs)


def _kinks(legs: list[Leg]) -> list[float]:
    return sorted({leg.strike for leg in legs if leg.strike is not None})


def payoff_curve(legs: list[Leg]) -> list[dict]:
    """`[{"spot", "pnl"}, ...]` at every distinct strike -- exact, since nothing curves between them."""
    return [{"spot": k, "pnl": payoff_at(legs, k)} for k in _kinks(legs)]


def slope_below(legs: list[Leg]) -> float:
    """d(pnl)/d(spot) below every strike: puts are ITM (slope -1/share), calls worthless (slope 0),
    stock always contributes its full delta."""
    slope = 0.0
    for leg in legs:
        if leg.kind == "put":
            slope -= leg.quantity * 100
        elif leg.kind == "stock":
            slope += leg.quantity * 100
    return slope


def slope_above(legs: list[Leg]) -> float:
    """d(pnl)/d(spot) above every strike: calls are ITM (slope +1/share), puts worthless."""
    slope = 0.0
    for leg in legs:
        if leg.kind == "call":
            slope += leg.quantity * 100
        elif leg.kind == "stock":
            slope += leg.quantity * 100
    return slope


def breakevens(legs: list[Leg]) -> list[float]:
    """Every spot price where cumulative P/L is exactly zero. Interior crossings (between the lowest
    and highest strike) come from linear interpolation across a sign change -- exact, since each
    segment between adjacent strikes is a straight line. The two tail segments (below the lowest
    strike, above the highest) are extrapolated using the analytic tail slopes, so an undefined-risk
    position's breakeven is found even though it lies outside the strike range."""
    kinks = _kinks(legs)
    if not kinks:
        return []
    points = [(k, payoff_at(legs, k)) for k in kinks]
    crossings: list[float] = []

    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if y0 == 0:
            crossings.append(x0)
        elif y0 * y1 < 0:
            crossings.append(x0 + (0 - y0) * (x1 - x0) / (y1 - y0))
    if points[-1][1] == 0:
        crossings.append(points[-1][0])

    x0, y0 = points[0]
    slope = slope_below(legs)
    if slope != 0:
        x_cross = x0 - y0 / slope
        if x_cross <= x0:
            crossings.append(x_cross)

    x1, y1 = points[-1]
    slope = slope_above(legs)
    if slope != 0:
        x_cross = x1 - y1 / slope
        if x_cross >= x1:
            crossings.append(x_cross)

    return sorted({round(c, 6) for c in crossings})


def max_profit(legs: list[Leg]) -> dict:
    """`{"value": float|None, "unbounded": bool}`. `value` is `None` exactly when `unbounded` is
    True -- an uncapped upside (long call/stock beyond the last strike) has no finite maximum."""
    if slope_above(legs) > 0:
        return {"value": None, "unbounded": True}
    candidates = [payoff_at(legs, k) for k in _kinks(legs)] or [payoff_at(legs, 0.0)]
    candidates.append(payoff_at(legs, 0.0))
    return {"value": max(candidates), "unbounded": False}


def max_loss(legs: list[Leg]) -> dict:
    """`{"value": float|None, "unbounded": bool}`. Downside is naturally bounded at `spot = 0`
    (already one of the evaluated candidates) -- only an uncapped upside loss (naked short call/stock)
    is truly unbounded."""
    if slope_above(legs) < 0:
        return {"value": None, "unbounded": True}
    candidates = [payoff_at(legs, k) for k in _kinks(legs)] or [payoff_at(legs, 0.0)]
    candidates.append(payoff_at(legs, 0.0))
    return {"value": min(candidates), "unbounded": False}


def net_greeks(legs: list[Leg]) -> dict:
    """Quantity-weighted rollup per greek. A "stock" leg has an implicit delta of 1 (and no gamma/
    theta/vega) regardless of its `delta` field. A greek is `None` overall only if not a single leg
    supplied it -- a mix of some-known/some-unknown legs still nets what's known."""
    totals = dict.fromkeys(_GREEK_FIELDS, 0.0)
    present = dict.fromkeys(_GREEK_FIELDS, False)
    for leg in legs:
        if leg.kind == "stock":
            totals["delta"] += 1.0 * leg.quantity * 100
            present["delta"] = True
            continue
        for greek in _GREEK_FIELDS:
            value = getattr(leg, greek)
            if value is not None:
                totals[greek] += value * leg.quantity * 100
                present[greek] = True
    return {greek: (totals[greek] if present[greek] else None) for greek in _GREEK_FIELDS}
