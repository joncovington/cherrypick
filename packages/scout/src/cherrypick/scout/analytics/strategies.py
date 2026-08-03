"""Strategy leg-generators: turn an already-fetched option chain (strikes + quotes for one
expiration) into a priced candidate. No I/O -- everything needed (spot, the option list, an expected
move for strike selection) is passed in by `screener_service`, which owns the actual chain fetch.

Deliberately not reusing `earnings.scanner`'s generators (event-shaped subprocess CLIs) -- these
borrow its `{legs, credit, max_risk, breakevens, pop, dte, expiration}` dict shape but stay pure and
inline, since importing `scanner` would drag in earnings' own config/Dolt plumbing for no reason.

**Short-strike selection has no live delta to key off of.** `chain_service`'s quotes come from
`get_market_data_by_type`, which carries no greeks (see the package CLAUDE.md) -- so every generator
here uses the plan's documented fallback unconditionally: the strike nearest spot +/- one expected
move on the relevant side, rather than a ~0.30-delta strike. This is an honest simplification, not a
placeholder for something silently wrong: a wrong delta would be worse than an openly proxy-based
strike pick.

Credit is priced at the mid quote with a haircut (`_SLIPPAGE_HAIRCUT`) standing in for a
`cherrypick.core.fees`-style fill-slippage adjustment -- a resting limit rarely fills at the exact
mid.
"""

from __future__ import annotations

from datetime import date

from .payoff import Leg, breakevens, max_loss

_SLIPPAGE_HAIRCUT = 0.90


def _mid(option: dict) -> float | None:
    quote = option.get("quote")
    return quote.get("mid") if quote else None


def _nearest_by_target(options: list[dict], target: float) -> dict | None:
    if not options:
        return None
    return min(options, key=lambda o: abs(o["strike"] - target))


def _otm_options(options: list[dict], spot: float, side: str) -> list[dict]:
    if side == "call":
        return [o for o in options if o["option_type"] == "C" and o["strike"] > spot]
    return [o for o in options if o["option_type"] == "P" and o["strike"] < spot]


def _short_strike(options: list[dict], spot: float, expected_move: float, side: str) -> dict | None:
    """The fallback strike pick: nearest-OTM-by-expected-move (see module docstring)."""
    target = spot + expected_move if side == "call" else spot - expected_move
    return _nearest_by_target(_otm_options(options, spot, side), target)


def _wing_strike(options: list[dict], short_strike: float, width: float, side: str) -> dict | None:
    target = short_strike + width if side == "call" else short_strike - width
    if side == "call":
        candidates = [o for o in options if o["option_type"] == "C" and o["strike"] > short_strike]
    else:
        candidates = [o for o in options if o["option_type"] == "P" and o["strike"] < short_strike]
    return _nearest_by_target(candidates, target)


def _package(legs: list[Leg], credit: float, expiration: date, dte: int) -> dict:
    loss = max_loss(legs)
    return {
        "legs": [
            {"kind": leg.kind, "quantity": leg.quantity, "price": leg.price, "strike": leg.strike}
            for leg in legs
        ],
        "credit": credit,
        "max_risk": None if loss["unbounded"] else abs(loss["value"]),
        "breakevens": breakevens(legs),
        "dte": dte,
        "expiration": expiration.isoformat(),
    }


def put_credit_spread(
    options: list[dict], spot: float, expected_move: float, wing_width_pct: float, expiration: date, dte: int
) -> dict | None:
    short = _short_strike(options, spot, expected_move, "put")
    if short is None:
        return None
    width = short["strike"] * wing_width_pct
    long_leg = _wing_strike(options, short["strike"], width, "put")
    if long_leg is None:
        return None
    short_mid, long_mid = _mid(short), _mid(long_leg)
    if short_mid is None or long_mid is None:
        return None
    credit = (short_mid - long_mid) * _SLIPPAGE_HAIRCUT
    if credit <= 0:
        return None
    legs = [
        Leg(kind="put", quantity=-1, price=short_mid, strike=short["strike"], expiration=expiration),
        Leg(kind="put", quantity=1, price=long_mid, strike=long_leg["strike"], expiration=expiration),
    ]
    return {**_package(legs, credit * 100, expiration, dte), "strategy": "put_credit_spread"}


def call_credit_spread(
    options: list[dict], spot: float, expected_move: float, wing_width_pct: float, expiration: date, dte: int
) -> dict | None:
    short = _short_strike(options, spot, expected_move, "call")
    if short is None:
        return None
    width = short["strike"] * wing_width_pct
    long_leg = _wing_strike(options, short["strike"], width, "call")
    if long_leg is None:
        return None
    short_mid, long_mid = _mid(short), _mid(long_leg)
    if short_mid is None or long_mid is None:
        return None
    credit = (short_mid - long_mid) * _SLIPPAGE_HAIRCUT
    if credit <= 0:
        return None
    legs = [
        Leg(kind="call", quantity=-1, price=short_mid, strike=short["strike"], expiration=expiration),
        Leg(kind="call", quantity=1, price=long_mid, strike=long_leg["strike"], expiration=expiration),
    ]
    return {**_package(legs, credit * 100, expiration, dte), "strategy": "call_credit_spread"}


def short_put(
    options: list[dict], spot: float, expected_move: float, expiration: date, dte: int
) -> dict | None:
    short = _short_strike(options, spot, expected_move, "put")
    if short is None:
        return None
    mid = _mid(short)
    if mid is None or mid <= 0:
        return None
    credit = mid * _SLIPPAGE_HAIRCUT
    legs = [Leg(kind="put", quantity=-1, price=credit, strike=short["strike"], expiration=expiration)]
    return {**_package(legs, credit * 100, expiration, dte), "strategy": "short_put"}


def covered_call(
    options: list[dict], spot: float, expected_move: float, expiration: date, dte: int, stock_price: float
) -> dict | None:
    short = _short_strike(options, spot, expected_move, "call")
    if short is None:
        return None
    mid = _mid(short)
    if mid is None or mid <= 0:
        return None
    credit = mid * _SLIPPAGE_HAIRCUT
    legs = [
        Leg(kind="stock", quantity=1, price=stock_price),
        Leg(kind="call", quantity=-1, price=credit, strike=short["strike"], expiration=expiration),
    ]
    return {**_package(legs, credit * 100, expiration, dte), "strategy": "covered_call"}


STRATEGIES = {
    "put_credit_spread": put_credit_spread,
    "call_credit_spread": call_credit_spread,
    "short_put": short_put,
    "covered_call": covered_call,
}


def directional_edge(options: list[dict], spot: float, expected_move: float) -> float | None:
    """A price-based skew proxy: OTM call mid minus OTM put mid, each picked at roughly one expected
    move from spot (matched dollar-distance, not matched delta -- see the module docstring on why).
    Positive means calls are pricing richer than puts (a bullish tilt); negative is the more common
    equity put-skew. Mirrors `cherrypick.flies`' `skew_bucket`: "OTM put vs. OTM call price at the
    exact strikes ... a direct read of whether the chain itself is pricing in a direction." `None` if
    either side has no strike to compare."""
    call = _short_strike(options, spot, expected_move, "call")
    put = _short_strike(options, spot, expected_move, "put")
    if call is None or put is None:
        return None
    call_mid, put_mid = _mid(call), _mid(put)
    if call_mid is None or put_mid is None:
        return None
    return call_mid - put_mid


def composite_score(return_on_risk: float, pop: float, iv_rank_frac: float, liquidity_rating) -> float:
    """Weighted composite rank, `compute_composite_score`'s shape (multiplicative rather than
    summed, so a strong core edge -- return-on-risk -- isn't diluted by averaging with weaker
    secondary confirmations): `return_on_risk * pop * iv_rank * a liquidity factor`, each secondary
    factor floored so a merely-average reading doesn't zero out an otherwise strong candidate."""
    liquidity_factor = min(1.0, (liquidity_rating or 0) / 4.0)
    return return_on_risk * max(pop, 0.05) * max(iv_rank_frac, 0.05) * max(liquidity_factor, 0.1)
