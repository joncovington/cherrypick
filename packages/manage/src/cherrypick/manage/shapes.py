"""Name a trade's shape from its open option legs.

The name is for people (the page and the explanation say what is being judged) and for the two rules
that depend on shape: a jade lizard has no upside risk, so a call spread trading through is not a
broken thesis. Everything else is judged by its numbers, not its name.

Shapes, one expiry unless said otherwise:
- super bear: a long put spread below a short call spread (long the higher put, short the lower put;
  short the lower call, long the higher call), put strikes at or below the call strikes. OIC calls it a
  double bear spread. Bearish, defined risk both ways, usually opened near even money.
- super bull: the mirror -- a short put spread below a long call spread.
- jade lizard: a short put (no long put) with a short call spread above it, taken in for at least the
  call spread's width, which leaves no upside risk.
- iron condor / iron butterfly: a short put spread and a short call spread (shorts at one strike for
  the butterfly).
- credit spread / debit spread: one vertical. naked put / naked call / long put / long call: one leg.
- diagonal: shorts expiring before a long they are sold against. calendar: the same, at one strike.
- butterfly / broken-wing butterfly: long-short-long at three strikes of one right, 1-2-1.
- a ratio butterfly, named by its quantities read from the top strike ("1-3-2"): long-short-long at
  three strikes of one right whose wings together match the body, so nothing is naked, but are not
  1-2-1 (SPY on 2026-10-18: +1 745P / -3 740P / +2 730P). ratio time spread: the same across expiries.
Anything else is "custom".
"""

from __future__ import annotations

from math import gcd
from typing import Any

MULTIPLIER = 100.0


def _one(legs: list[dict[str, Any]], right: str, sign: int) -> list[dict[str, Any]]:
    return [leg for leg in legs if leg["right"] == right and (leg["quantity"] > 0) == (sign > 0)]


def _vertical(legs: list[dict[str, Any]], right: str) -> tuple[dict, dict] | None:
    """(long, short) when `legs` of `right` are exactly one long and one short of equal size."""
    longs, shorts = _one(legs, right, 1), _one(legs, right, -1)
    if len(longs) == 1 and len(shorts) == 1 and abs(longs[0]["quantity"]) == abs(shorts[0]["quantity"]):
        return longs[0], shorts[0]
    return None


def classify(options: list[dict[str, Any]], cost: float | None) -> str:
    if not options:
        return "shares"
    expiries = {leg["expiration"] for leg in options}
    if len(expiries) > 1:
        if _ratio(options):
            return "ratio time spread"
        shorts = [leg for leg in options if leg["quantity"] < 0]
        if shorts and min(s["expiration"] for s in shorts) < max(leg["expiration"] for leg in options):
            strikes = {leg["strike"] for leg in options}
            return "calendar" if len(strikes) == 1 else "diagonal"
        return "custom"

    calls = [leg for leg in options if leg["right"] == "C"]
    puts = [leg for leg in options if leg["right"] == "P"]
    if len(options) == 1:
        leg = options[0]
        side = "naked" if leg["quantity"] < 0 else "long"
        return f"{side} {'put' if leg['right'] == 'P' else 'call'}"

    pv, cv = _vertical(puts, "P"), _vertical(calls, "C")
    if pv and cv:
        p_long, p_short = pv
        c_long, c_short = cv
        top_put = max(p_long["strike"], p_short["strike"])
        low_call = min(c_long["strike"], c_short["strike"])
        put_long_above = p_long["strike"] > p_short["strike"]
        call_short_below = c_short["strike"] < c_long["strike"]
        if top_put <= low_call:
            if put_long_above and call_short_below:
                return "super bear"
            if not put_long_above and not call_short_below:
                return "super bull"
            if not put_long_above and call_short_below:
                if p_short["strike"] == c_short["strike"]:
                    return "iron butterfly"
                # Taken in for at least the call wing: no upside risk, so it is managed as a jade
                # lizard whose short put happens to carry a long put below it (GLW on 2026-10-13).
                call_width = (c_long["strike"] - c_short["strike"]) * abs(c_short["quantity"]) * MULTIPLIER
                if -(cost or 0.0) >= call_width - 1e-6:
                    return "jade lizard (put wing)"
                return "iron condor"
        return "custom"
    if not calls and pv:
        return _spread_kind(cost)
    if not puts and cv:
        return _spread_kind(cost)
    # Jade lizard: one short put, no long put, and a short call spread above it.
    if cv and len(puts) == 1 and puts[0]["quantity"] < 0:
        c_long, c_short = cv
        if c_short["strike"] < c_long["strike"] and puts[0]["strike"] <= c_short["strike"]:
            width = (c_long["strike"] - c_short["strike"]) * abs(c_short["quantity"]) * MULTIPLIER
            credit = -(cost or 0.0)
            return "jade lizard" if credit >= width - 1e-6 else "custom"
    # Risk reversal: long one side, short the other (a wing on the short side is allowed).
    # The short side may be a naked short or a short vertical (FXI: long 33P against a short 33/34C).
    short_calls = sum(leg["quantity"] for leg in calls) < 0 or bool(cv and cv[1]["strike"] < cv[0]["strike"])
    short_puts = sum(leg["quantity"] for leg in puts) < 0 or bool(pv and pv[1]["strike"] > pv[0]["strike"])
    if puts and calls and all(leg["quantity"] > 0 for leg in puts) and short_calls:
        return "risk reversal (bear)"
    if puts and calls and all(leg["quantity"] > 0 for leg in calls) and short_puts:
        return "risk reversal (bull)"
    fly = _butterfly(calls) if not puts else _butterfly(puts) if not calls else None
    return fly or _ratio(options) or "custom"


def _butterfly(legs: list[dict[str, Any]]) -> str | None:
    """Long-short-long at three strikes, the body twice the wings: a butterfly (broken-wing if uneven)."""
    if len(legs) != 3:
        return None
    low, mid, high = sorted(legs, key=lambda leg: leg["strike"])
    if not (low["quantity"] > 0 and high["quantity"] > 0 and mid["quantity"] == -2 * low["quantity"]):
        return None
    even = abs((mid["strike"] - low["strike"]) - (high["strike"] - mid["strike"])) < 1e-9
    return "butterfly" if even else "broken-wing butterfly"


def _ratio(legs: list[dict[str, Any]]) -> str | None:
    """Long-short-long at three strikes of one right, the wings together matching the body but not 1-2-1:
    its quantities read from the top strike ("1-3-2"), reduced. None for anything else, a 1-2-1 included
    (a butterfly on one expiry; across expiries it is left to the calendar and diagonal names)."""
    if (
        len(legs) != 3
        or len({leg["right"] for leg in legs}) != 1
        or len({leg["strike"] for leg in legs}) != 3
    ):
        return None
    low, mid, high = sorted(legs, key=lambda leg: leg["strike"])
    qty = [high["quantity"], mid["quantity"], low["quantity"]]
    if any(q != int(q) for q in qty):
        return None
    top, body, bottom = (int(q) for q in qty)
    if not (top > 0 and bottom > 0 and body < 0 and top + bottom == -body) or top == bottom:
        return None
    if len({leg["expiration"] for leg in legs}) > 1:
        return "ratio time spread"
    k = gcd(gcd(top, -body), bottom)
    return f"{top // k}-{-body // k}-{bottom // k}"


# The shapes that own a direction and are usually opened near even money: judged on what they can win
# and lose, never on a percentage of their premium.
DIRECTIONAL = frozenset({"super bear", "super bull", "risk reversal (bear)", "risk reversal (bull)"})
# Also judged on risk when bought cheaply against a large tail: a broken-wing butterfly's premium says
# little about what it can lose on the wide-wing side (GLD on 2026-10-13: $193 in, $1,693 at risk).
RISK_JUDGED = DIRECTIONAL | {"broken-wing butterfly", "ratio time spread"}


def is_ratio(shape: str | None) -> bool:
    """A ratio butterfly's name is its quantities ("1-3-2"), so it is matched by form, not listed."""
    parts = (shape or "").split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def risk_judged(shape: str | None) -> bool:
    """Judged on what it can win and lose when its premium is small against its risk: a ratio carries
    its risk beyond the heavier wing, as a broken-wing butterfly does."""
    return shape in RISK_JUDGED or is_ratio(shape)


def _spread_kind(cost: float | None) -> str:
    return "credit spread" if (cost or 0.0) < 0 else "debit spread"
