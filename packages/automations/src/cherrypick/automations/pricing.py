"""Prices, payoffs, fees and the three fills of a structure. Pure.

Money follows `docs/conventions/money-layout.md`: gross is mid to mid (or mid to the settlement
intrinsic), and slippage, trading fees and settlement are their own columns, each subtracted once.
A fill KIND says how much of the spread is conceded on one fill:

    verdict   the suite's slippage model (`core.fees.slippage_dollars`): the judged fill
    stress    halfway from the mid to the natural fill (sell at the bid, buy at the ask)
    mid       nothing: context, never judged

Prices are per share and signed so a credit is +; `n` is +1 for a bought leg and -1 for a sold one, so
a structure's credit is -sum(n * price) and what closing it brings is +sum(n * price).
"""

from __future__ import annotations

from cherrypick.core import fees, legbars

KINDS = ("verdict", "stress", "mid")
MULTIPLIER = 100


def quotes_for(legs, chain) -> list[tuple[dict, int]] | None:
    """(quote, signed quantity) per leg, or None when a leg is not listed."""
    out = []
    for leg in legs:
        q = chain.get(leg.strike, leg.right)
        if q is None:
            return None
        out.append((q, leg.side))
    return out


def structure_prices(quotes) -> dict | None:
    """`core.legbars.structure_prices`: mid, natural, working and cost (mid less natural), per share."""
    return legbars.structure_prices(quotes)


def payoff_extremes(legs, credit: float) -> tuple[float | None, float | None]:
    """(maximum profit, maximum loss) per share at expiry for a structure taken at signed `credit`.
    Profit is None when unbounded. Loss is None when the structure is not defined-risk."""
    pts = sorted({0.0, *(leg.strike for leg in legs)})
    top = pts[-1] + 1.0

    def value(s: float) -> float:
        total = credit
        for leg in legs:
            intrinsic = max(s - leg.strike, 0.0) if leg.right == "C" else max(leg.strike - s, 0.0)
            total += leg.side * intrinsic
        return total

    slope = sum(leg.side for leg in legs if leg.right == "C")
    values = [value(s) for s in [*pts, top]]
    if slope < 0:
        return None, None
    profit = None if slope > 0 else max(values)
    return profit, -min(values)


def payoff_curve(legs, credit: float, lo: float, hi: float) -> list[tuple[float, float]]:
    """The structure's P&L per share at expiry, taken at signed `credit`, as (settle, P&L) at `lo`, each
    strike between and `hi`. The payoff is straight between strikes, so these points draw it exactly."""
    xs = sorted({lo, hi, *(leg.strike for leg in legs if lo < leg.strike < hi)})
    return [(x, credit + intrinsic_value(legs, x)) for x in xs]


def breakevens(points: list[tuple[float, float]]) -> list[float]:
    """Where a straight-line curve through `points` crosses zero, in order."""
    out: list[float] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if y0 == 0 and (not out or out[-1] != x0):
            out.append(x0)
        elif (y0 < 0 < y1) or (y1 < 0 < y0):
            out.append(x0 + (x1 - x0) * (-y0) / (y1 - y0))
    if points and points[-1][1] == 0 and (not out or out[-1] != points[-1][0]):
        out.append(points[-1][0])
    return out


def value_bounds(legs) -> tuple[float | None, float | None]:
    """The least and most the structure can be worth per share, +sum(n * price), read off its payoff
    at expiry: every leg shares one expiry, so its value now lies in that range. None where the payoff
    is unbounded that way."""
    pts = sorted({0.0, *(leg.strike for leg in legs)})
    values = [intrinsic_value(legs, s) for s in pts]
    slope = sum(leg.side for leg in legs if leg.right == "C")
    lo = min(values) if slope >= 0 else None
    hi = max(values) if slope <= 0 else None
    return lo, hi


def within_bounds(legs, value: float) -> bool:
    """Whether `value` (+sum(n * price) per share) is one the structure can be worth. A mid outside it
    is a quote that cannot be filled, never a price (`value_bounds`)."""
    lo, hi = value_bounds(legs)
    return (lo is None or value >= lo - BOUND_TOLERANCE) and (hi is None or value <= hi + BOUND_TOLERANCE)


#: Rounding room on `within_bounds`, per share: a mid is quoted to the half cent.
BOUND_TOLERANCE = 0.005


def intrinsic_value(legs, settle: float) -> float:
    """What the structure pays at expiry per share: sum(n * intrinsic)."""
    total = 0.0
    for leg in legs:
        i = max(settle - leg.strike, 0.0) if leg.right == "C" else max(leg.strike - settle, 0.0)
        total += leg.side * i
    return total


def slippage(kind: str, quotes, quantity: int, symbol: str, config: dict | None = None) -> float:
    """Dollars conceded on one fill of the whole position."""
    if kind == "mid":
        return 0.0
    if kind == "verdict":
        # One quote per contract the structure holds: a leg of two contracts concedes twice.
        each = [q for q, n in quotes for _ in range(abs(n))]
        return fees.slippage_dollars(each, quantity, config or {}, symbol=symbol)
    if kind == "stress":
        p = structure_prices(quotes)
        return 0.0 if p is None else round(p["cost"] / 2.0 * MULTIPLIER * quantity, 2)
    raise ValueError(kind)


def contracts(legs) -> int:
    """The contracts one structure holds: each leg's `side` counts its contracts, whatever its sign."""
    return sum(abs(g.side) for g in legs)


def open_fee(symbol: str, quantity: int, legs) -> float:
    """Charged per contract, so a leg of two contracts is charged twice."""
    return fees.ic_open_fee(
        symbol, quantity, legs=contracts(legs), sell_legs=contracts(g for g in legs if g.side < 0)
    )


def close_fee(symbol: str, quantity: int, legs) -> float:
    """Closing sells the legs held long."""
    return fees.ic_close_fee(
        symbol, quantity, legs=contracts(legs), sell_legs=contracts(g for g in legs if g.side > 0)
    )


def settlement_fee(legs, settle: float) -> float:
    """The cash-settlement charge: one per distinct leg finishing in the money."""
    itm = sum(1 for g in legs if (settle > g.strike if g.right == "C" else settle < g.strike))
    return fees.ic_expire_fee(itm)
