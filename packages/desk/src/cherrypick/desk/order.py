"""Order parsing and worst-case risk — the pure layer the policy gates are built on.

Risk is computed from the **expiry payoff diagram**, not from pattern-matching strategy names. A
named-structure whitelist ("iron condor", "butterfly", ...) is exactly the kind of check that passes
a mislabeled order and refuses a legitimate one it has no name for; the payoff of a set of legs is
unambiguous and covers ratios, broken wings, and structures nobody named yet.

The diagram is piecewise-linear in the underlying with kinks only at strikes, so its minimum over
``[0, inf)`` is attained at ``S = 0``, at a strike, or at infinity. Evaluating those points is exact
— no sampling, no tolerance. Infinity is handled by the slope test: if the payoff slope above the
highest strike is negative, loss is unbounded and the position is *undefined risk*.

Only one kind of order is exempt from the risk gates: one whose every leg is "buy to close". Buying
back shorts can only remove exposure. "Sell to close" is different — selling the long wing of an iron
condor leaves a naked short — and this function only ever sees the order, not the position it acts
on, so a sell-to-close leg is scored like any other short. `covering_only` carries the distinction.

The accepted shape is deliberately narrow: equity options only, one underlying, a Day Limit order
with a positive finite price, whole-number quantities, and no keys outside a small allow-list.
Anything else is refused here, before a gate could misread it.
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


SPEC_KEYS = frozenset({"legs", "price", "price_effect", "order_type", "time_in_force"})
LEG_KEYS = frozenset({"instrument_type", "symbol", "action", "quantity"})
INSTRUMENT_TYPE = "Equity Option"


def parse_leg(raw: dict[str, Any]) -> Leg:
    """One leg dict (the same shape `core.broker.build_order` consumes) -> a parsed Leg."""
    if not isinstance(raw, dict):
        raise OrderError(f"each leg must be a JSON object: {raw!r}")
    unknown = sorted(set(raw) - LEG_KEYS)
    if unknown:
        raise OrderError(f"leg carries unsupported keys {unknown} (allowed: {sorted(LEG_KEYS)})")
    for key in ("instrument_type", "symbol", "action", "quantity"):
        if raw.get(key) in (None, ""):
            raise OrderError(f"leg is missing required field {key!r}: {raw!r}")
    if raw["instrument_type"] != INSTRUMENT_TYPE:
        raise OrderError(
            f"instrument_type must be exactly {INSTRUMENT_TYPE!r} (got {raw['instrument_type']!r})"
        )
    action = str(raw["action"]).strip().lower()
    if action not in _ACTIONS:
        raise OrderError(f"unknown leg action {raw['action']!r} (expected one of {sorted(_ACTIONS)})")
    sign, open_close = _ACTIONS[action]
    qty = raw["quantity"]
    # A bool is an int in Python, and "2" or 2.7 would be coerced by int() — all refused, so the
    # number fingerprinted is exactly the number the broker receives.
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise OrderError(f"leg quantity must be a whole number: {qty!r}")
    if qty < 1:
        # Direction is carried by `action`, never by a negative quantity — allowing both would make
        # "sell to open -2" ambiguous (double negative) and is a plausible way to fat-finger a side.
        raise OrderError(f"leg quantity must be positive (direction comes from action): {qty}")

    symbol = raw["symbol"]
    if not isinstance(symbol, str) or symbol != symbol.strip().upper():
        raise OrderError(f"option symbol must be an upper-case OCC symbol with no outer spaces: {symbol!r}")
    underlying, exp, right, strike = parse_occ(symbol)
    return Leg(INSTRUMENT_TYPE, symbol, action, qty, sign * qty, open_close, right, strike, exp, underlying)


def _check_spec(spec: Any) -> None:
    """The order-level allow-list. Every refusal names the field, so the fix is obvious."""
    if not isinstance(spec, dict):
        raise OrderError("order must be a JSON object")
    if "stop_trigger" in spec:
        raise OrderError("stop orders are not accepted here (stop_trigger)")
    if "external_identifier" in spec:
        raise OrderError("external_identifier is set by the desk itself, not by the order")
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        raise OrderError(f"order carries unsupported keys {unknown} (allowed: {sorted(SPEC_KEYS)})")
    if spec.get("order_type", "Limit") != "Limit":
        raise OrderError(f"only Limit orders are accepted (order_type {spec.get('order_type')!r})")
    if spec.get("time_in_force", "Day") != "Day":
        raise OrderError(f"only Day orders are accepted (time_in_force {spec.get('time_in_force')!r})")
    if spec.get("legs") is not None and not isinstance(spec["legs"], list):
        raise OrderError("order legs must be a JSON list")


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
    # True only when every leg is "buy to close" — the one shape that can only remove exposure, and
    # the only one the policy exempts from the risk gates.
    covering_only: bool = False

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

    `spec` is the same dict `core.broker.build_order` takes: `legs`, `price`, `price_effect`, and
    optionally `order_type` ("Limit") and `time_in_force` ("Day").
    """
    _check_spec(spec)
    raw_legs = spec.get("legs") or []
    if not raw_legs:
        raise OrderError("order has no legs")
    legs = [parse_leg(leg) for leg in raw_legs]
    # One payoff diagram is only meaningful over one underlying: strikes on two different symbols
    # are not points on the same axis, and mixing them can make a naked short look covered.
    if len({leg.underlying for leg in legs}) > 1:
        raise OrderError("order spans more than one underlying — the payoff diagram would be meaningless")

    spreads = _spread_count(legs)
    price = spec.get("price")
    if price is None:
        raise OrderError("order has no price — a market order's cost is unbounded and is not accepted here")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise OrderError(f"order price must be a number: {price!r}")
    price = float(price)
    if not math.isfinite(price) or price <= 0:
        raise OrderError(
            f"order price must be finite and greater than zero (direction comes from price_effect): {price!r}"
        )
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
        covering_only=all(leg.action == "buy to close" for leg in legs),
    )
