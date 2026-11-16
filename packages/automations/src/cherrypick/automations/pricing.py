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
        return fees.slippage_dollars([q for q, _ in quotes], quantity, config or {}, symbol=symbol)
    if kind == "stress":
        p = structure_prices(quotes)
        return 0.0 if p is None else round(p["cost"] / 2.0 * MULTIPLIER * quantity, 2)
    raise ValueError(kind)


def open_fee(symbol: str, quantity: int, legs) -> float:
    return fees.ic_open_fee(symbol, quantity, legs=len(legs), sell_legs=sum(1 for g in legs if g.side < 0))


def close_fee(symbol: str, quantity: int, legs) -> float:
    """Closing sells the legs held long."""
    return fees.ic_close_fee(symbol, quantity, legs=len(legs), sell_legs=sum(1 for g in legs if g.side > 0))


def settlement_fee(legs, settle: float) -> float:
    """The cash-settlement charge: one per distinct leg finishing in the money."""
    itm = sum(1 for g in legs if (settle > g.strike if g.right == "C" else settle < g.strike))
    return fees.ic_expire_fee(itm)
