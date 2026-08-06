"""Order parsing and worst-case risk — the pure layer the policy gates are built on.

Risk is computed from the **expiry payoff diagram**, not from pattern-matching strategy names. A
named-structure whitelist ("iron condor", "butterfly", ...) is exactly the kind of check that passes
a mislabeled order and refuses a legitimate one it has no name for; the payoff of a set of legs is
unambiguous and covers ratios, broken wings, and structures nobody named yet.

The diagram is piecewise-linear in the underlying with kinks only at strikes, so its minimum over
``[0, inf)`` is attained at ``S = 0``, at a strike, or at infinity. Evaluating those points is exact
— no sampling, no tolerance. Infinity is handled by the slope test: if the payoff slope above the
highest strike is negative, loss is unbounded and the position is *undefined risk*.

Closing orders are classified separately and deliberately. An order whose every leg is "to close"
*removes* exposure, so a risk cap that blocks it is the cap misfiring — that is the concrete failure
that motivated this package (a naive account-level deploy governor refused a risk-reducing BKNG close
because it only knew "more buying power consumed = bad"). Their cost is still reported (a debit paid
to close is real money) but they are exempt from the defined-risk requirement: flattening a naked
short is precisely what you want to allow.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

# OCC 21-character option symbol: 6-char root (space padded), YYMMDD, C|P, 8-digit strike x1000.
#   "XYZ   260807C00085000" -> XYZ, 2026-08-07, call, 85.0
_OCC = re.compile(
    r"^(?P<root>[A-Z0-9 .]{1,6})\s*(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$"
)

# Actions the broker accepts, mapped to the sign the leg contributes to the payoff. "to open" vs
# "to close" is kept (not collapsed to buy/sell) because the open/close split drives classification.
_ACTIONS = {
    "buy to open": (+1, "open"),
    "sell to open": (-1, "open"),
    "buy to close": (+1, "close"),
    "sell to close": (-1, "close"),
}

OPTION_MULTIPLIER = 100


class OrderError(ValueError):
    """A malformed order spec. Raised during parsing so a bad order never reaches a gate that might
    accidentally pass it — an unparseable order is refused, never treated as risk-free."""


@dataclass(frozen=True)
class Leg:
    """One parsed leg. `signed_qty` folds the action's direction in, so the payoff math never has to
    re-derive long/short. `strike`/`right` are None for an equity leg."""

    instrument_type: str
    symbol: str
    action: str
    quantity: int
    signed_qty: int
    open_close: str
    right: str | None = None
    strike: float | None = None
    expiration: date | None = None
    underlying: str | None = None

    @property
    def is_option(self) -> bool:
        return self.right is not None


def parse_occ(symbol: str) -> tuple[str, date, str, float]:
    """(underlying, expiration, right, strike) from an OCC option symbol.

    Raises OrderError rather than returning a sentinel: a symbol we cannot decode is a symbol whose
    risk we cannot compute, and the caller must not proceed."""
    m = _OCC.match(symbol.strip().upper())
    if not m:
        raise OrderError(f"not a recognizable OCC option symbol: {symbol!r}")
    y, mo, d = int(m["y"]), int(m["m"]), int(m["d"])
    try:
        exp = date(2000 + y, mo, d)
    except ValueError as exc:
        raise OrderError(f"option symbol carries an impossible date: {symbol!r}") from exc
    return m["root"].strip(), exp, m["cp"], int(m["strike"]) / 1000.0


def parse_leg(raw: dict[str, Any]) -> Leg:
    """One leg dict (the same shape `core.broker.build_order` consumes) -> a parsed Leg."""
    for key in ("instrument_type", "symbol", "action", "quantity"):
        if raw.get(key) in (None, ""):
            raise OrderError(f"leg is missing required field {key!r}: {raw!r}")
    action = str(raw["action"]).strip().lower()
    if action not in _ACTIONS:
        raise OrderError(f"unknown leg action {raw['action']!r} (expected one of {sorted(_ACTIONS)})")
    sign, open_close = _ACTIONS[action]
    try:
        qty = int(raw["quantity"])
    except (TypeError, ValueError) as exc:
        raise OrderError(f"leg quantity is not an integer: {raw['quantity']!r}") from exc
    if qty <= 0:
        # Direction is carried by `action`, never by a negative quantity — allowing both would make
        # "sell to open -2" ambiguous (double negative) and is a plausible way to fat-finger a side.
        raise OrderError(f"leg quantity must be positive (direction comes from action): {qty}")

    itype = str(raw["instrument_type"]).strip()
    symbol = str(raw["symbol"]).strip()
    if itype.lower().replace("-", " ") in ("equity option", "future option"):
        underlying, exp, right, strike = parse_occ(symbol)
        return Leg(itype, symbol, action, qty, sign * qty, open_close, right, strike, exp, underlying)
    return Leg(itype, symbol, action, qty, sign * qty, open_close, underlying=symbol.upper())


@dataclass(frozen=True)
class RiskProfile:
    """Worst case for a parsed order, in dollars.

    `max_loss` is None exactly when loss is unbounded (`defined` is then False) — callers must treat
    None as "worse than any cap", never as "no risk". `entry_cash` is signed: negative for a debit
    paid, positive for a credit received.
    """

    classification: str  # "opening" | "closing" | "mixed"
    defined: bool
    max_loss: float | None
    max_gain: float | None
    entry_cash: float
    spreads: int
    breakevens: tuple[float, ...]
    underlyings: tuple[str, ...]
    # Why the worst case is not computable, when it isn't. "unbounded" = short the upside tail;
    # "multi_expiry" = a calendar/diagonal, where a single-expiry diagram is simply the wrong model
    # (the far leg still carries time value at the near expiry, which cannot be known without a
    # pricing model). Both surface as max_loss=None; the reason distinguishes them in refusals.
    undefined_reason: str | None = None

    @property
    def unbounded(self) -> bool:
        return self.max_loss is None


def _classify(legs: list[Leg]) -> str:
    marks = {leg.open_close for leg in legs}
    if marks == {"open"}:
        return "opening"
    if marks == {"close"}:
        return "closing"
    return "mixed"  # a roll — treated as opening by policy, since it establishes new exposure


def _payoff(legs: list[Leg], spot: float) -> float:
    """Position value at expiry for an underlying price of `spot`, in dollars."""
    total = 0.0
    for leg in legs:
        if leg.right == "C":
            total += leg.signed_qty * OPTION_MULTIPLIER * max(spot - leg.strike, 0.0)
        elif leg.right == "P":
            total += leg.signed_qty * OPTION_MULTIPLIER * max(leg.strike - spot, 0.0)
        else:
            total += leg.signed_qty * spot  # equity: one share per unit quantity
    return total


def _upside_slope(legs: list[Leg]) -> float:
    """d(payoff)/d(spot) above the highest strike. Negative => loss grows without bound as the
    underlying rises, which is the only genuinely unbounded direction (downside stops at spot 0)."""
    return sum(leg.signed_qty * OPTION_MULTIPLIER for leg in legs if leg.right == "C") + sum(
        leg.signed_qty for leg in legs if not leg.is_option
    )


def _spread_count(legs: list[Leg]) -> int:
    """How many copies of the structure this order represents — the unit the net price is quoted per.

    A 1/-2/1 butterfly is one spread at its quoted debit; 2/-4/2 is two. Taking the GCD of the leg
    quantities recovers that unit without the caller having to state it, and matches the `size` the
    broker echoes back on the order."""
    counts = [abs(leg.quantity) for leg in legs]
    return math.gcd(*counts) or 1  # gcd() of an empty list is 0; a lone leg gcds to itself


def _breakevens(legs: list[Leg], entry_cash: float, points: list[float]) -> tuple[float, ...]:
    """Underlying prices where P&L crosses zero, found by linear interpolation between adjacent
    critical points (the diagram is straight between them, so this is exact, not approximate)."""
    out: list[float] = []
    for lo, hi in zip(points, points[1:], strict=False):  # pairwise; the tail has no successor
        a = _payoff(legs, lo) + entry_cash
        b = _payoff(legs, hi) + entry_cash
        if a == 0:
            out.append(lo)
        if (a < 0 < b) or (b < 0 < a):
            out.append(lo + (hi - lo) * (0 - a) / (b - a))
    if points and _payoff(legs, points[-1]) + entry_cash == 0:
        out.append(points[-1])
    return tuple(round(p, 4) for p in sorted(set(out)))


def analyze(spec: dict[str, Any]) -> tuple[list[Leg], RiskProfile]:
    """Parse an order spec and compute its worst case. Pure — no broker, no network, no clock.

    `spec` is the same dict `core.broker.build_order` takes: `legs`, `price`, `price_effect`.
    """
    raw_legs = spec.get("legs") or []
    if not raw_legs:
        raise OrderError("order has no legs")
    legs = [parse_leg(leg) for leg in raw_legs]

    spreads = _spread_count(legs)
    price = spec.get("price")
    if price is None:
        raise OrderError("order has no price — a market order's cost is unbounded and is not accepted here")
    try:
        price = float(price)
    except (TypeError, ValueError) as exc:
        raise OrderError(f"order price is not a number: {spec.get('price')!r}") from exc
    effect = str(spec.get("price_effect") or "").strip().lower()
    if effect not in ("debit", "credit"):
        raise OrderError("order needs an explicit price_effect of 'debit' or 'credit'")
    # Signed cash at entry: a debit leaves the account, a credit arrives. Magnitude is per-spread,
    # so a 2-lot butterfly at 1.10 is 220 out, not 110.
    entry_cash = -abs(price) * OPTION_MULTIPLIER * spreads
    if effect == "credit":
        entry_cash = abs(price) * OPTION_MULTIPLIER * spreads

    strikes = sorted({leg.strike for leg in legs if leg.strike is not None})
    points = [0.0, *strikes]
    pnls = [_payoff(legs, s) + entry_cash for s in points]

    # A single expiry diagram is only a valid model when every option leg expires together. For a
    # calendar or diagonal the far leg still holds time value when the near one expires, and its
    # worth then cannot be derived without a pricing model — so rather than report a confidently
    # wrong number, the worst case is declared uncomputable and the gates refuse it by default.
    expiries = {leg.expiration for leg in legs if leg.expiration is not None}
    multi_expiry = len(expiries) > 1

    slope_up = _upside_slope(legs)
    undefined_reason: str | None = None
    if multi_expiry:
        max_loss: float | None = None
        undefined_reason = "multi_expiry"
    elif slope_up < 0:
        max_loss = None  # unbounded: loss keeps growing as the underlying rises
        undefined_reason = "unbounded"
    else:
        max_loss = -min(pnls)
        if max_loss < 0:
            max_loss = 0.0  # an arbitrage-shaped fill (never worse than flat) still reports 0, not a gain

    # Gain is unbounded whenever the payoff keeps rising above the last strike.
    max_gain: float | None = None if (slope_up > 0 or multi_expiry) else max(pnls)

    return legs, RiskProfile(
        classification=_classify(legs),
        defined=max_loss is not None,
        max_loss=max_loss,
        max_gain=max_gain,
        entry_cash=entry_cash,
        spreads=spreads,
        breakevens=() if multi_expiry else _breakevens(legs, entry_cash, points),
        underlyings=tuple(sorted({leg.underlying for leg in legs if leg.underlying})),
        undefined_reason=undefined_reason,
    )
