"""Black-Scholes-Merton for one European option: price, delta and the vol that prices a mid.

`div` is a continuous dividend yield (Merton's model). At zero it is plain Black-Scholes, term for
term, so callers that ignore dividends get the numbers they always had. A long-dated call on a
high-yield fund cannot be priced without it: TLT's January 2029 80 call at a 7.05 mid with the fund
at 80.16 sits below its no-dividend floor (spot less the discounted strike, 7.08), so plain
Black-Scholes solves to a vol of zero and a delta of 1; at its ~4.3% yield it is 0.49 delta.

Every number here is a model. It bounds or compares; it is not a price anyone can trade at.
"""

from __future__ import annotations

import math


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def intrinsic(spot: float, strike: float, right: str) -> float:
    return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)


def _d1(spot: float, strike: float, years: float, vol: float, rate: float, div: float) -> float:
    return (math.log(spot / strike) + (rate - div + vol * vol / 2.0) * years) / (vol * math.sqrt(years))


def price(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> float:
    if years <= 0 or vol <= 0 or spot <= 0:
        return intrinsic(spot, strike, right)
    d1 = _d1(spot, strike, years, vol, rate, div)
    d2 = d1 - vol * math.sqrt(years)
    carry = math.exp(-div * years)
    if right == "C":
        return spot * carry * _n(d1) - strike * math.exp(-rate * years) * _n(d2)
    return strike * math.exp(-rate * years) * _n(-d2) - spot * carry * _n(-d1)


def delta(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> float | None:
    if years <= 0 or vol <= 0 or spot <= 0:
        return None
    d1 = _d1(spot, strike, years, vol, rate, div)
    carry = math.exp(-div * years)
    return carry * _n(d1) if right == "C" else carry * (_n(d1) - 1.0)


def prob_itm(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> float | None:
    """The model's chance the option finishes in the money: N(d2) for a call, N(-d2) for a put.

    Not delta, which is N(d1) and sits on the far side of it: a 0.15-delta call is about a 13% chance
    of finishing in the money at 30% vol over 30 days, and a 0.15-delta put about 17%. Anything asking
    "how often does the price end past the strike" compares against this, never 1 - |delta|."""
    if years <= 0 or vol <= 0 or spot <= 0:
        return None
    d2 = _d1(spot, strike, years, vol, rate, div) - vol * math.sqrt(years)
    return _n(d2) if right == "C" else _n(-d2)


def implied_vol(
    spot: float, strike: float, years: float, mid: float, right: str, rate: float, div: float = 0.0
) -> float | None:
    """The vol that prices `mid`, by bisection; None when no vol can (a mid below intrinsic, no time).

    Up to 100 halvings of [0.0001, 5]. Two things make it fast without moving a bit of the answer:
    the terms of `price` that do not depend on the vol are worked out once, in the same order of
    operations, so each step's price is the very float `price` gives; and the loop stops once the
    midpoint equals an end of the interval. From then on no step can change `lo` or `hi` (the
    interval is two adjacent floats, or one), so the remaining halvings would return the same
    float. That happens after about 58 steps."""
    if years <= 0 or mid is None or spot <= 0:
        return None
    if mid <= intrinsic(spot, strike, right) + 1e-9:
        return 0.0001
    lo, hi = 0.0001, 5.0
    if price(spot, strike, years, hi, right, rate, div) < mid:
        return None
    # `price`'s terms with no vol in them, each computed exactly as `price` and `_d1` compute it.
    log_moneyness = math.log(spot / strike)
    drift = rate - div
    root_years = math.sqrt(years)
    spot_carry = spot * math.exp(-div * years)
    strike_disc = strike * math.exp(-rate * years)
    root_two = math.sqrt(2.0)
    erf = math.erf
    call = right == "C"
    for _ in range(100):
        mid_vol = (lo + hi) / 2.0
        d1 = (log_moneyness + (drift + mid_vol * mid_vol / 2.0) * years) / (mid_vol * root_years)
        d2 = d1 - mid_vol * root_years
        if call:
            value = spot_carry * (0.5 * (1.0 + erf(d1 / root_two))) - strike_disc * (
                0.5 * (1.0 + erf(d2 / root_two))
            )
        else:
            value = strike_disc * (0.5 * (1.0 + erf(-d2 / root_two))) - spot_carry * (
                0.5 * (1.0 + erf(-d1 / root_two))
            )
        settled = mid_vol == lo or mid_vol == hi
        if value > mid:
            hi = mid_vol
        else:
            lo = mid_vol
        if settled:
            break
    return (lo + hi) / 2.0
