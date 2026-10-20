"""Name a trade's shape from its open option legs.

The name is for people (the page and the explanation say what is being judged) and for the rules that
depend on shape: a lizard has no risk on one side, so a short trading through there is not a broken
thesis; a cheap trade with a long tail is judged on its risk; and the profit targets can be set per
shape (`credit.take_profit.by_shape`, `debit.take_profit.by_shape`). Everything else is judged by its
numbers, not its name.

Shapes, one expiry unless said otherwise:
- super bear: a long put spread below a short call spread (long the higher put, short the lower put;
  short the lower call, long the higher call), put strikes at or below the call strikes. OIC calls it a
  double bear spread. Bearish, defined risk both ways, usually opened near even money.
- super bull: the mirror -- a short put spread below a long call spread.
- jade lizard: a short put (no long put) with a short call spread above it, taken in for at least the
  call spread's width, which leaves no upside risk. big lizard: the same with the short put at the
  short call's strike. reverse jade lizard / reverse big lizard: the mirror -- a short call (no long
  call) over a short put spread, taken in for at least the put spread's width: no downside risk.
- iron condor / iron butterfly: a short put spread and a short call spread (shorts at one strike for
  the butterfly). An iron condor taken in for at least one side's width is that side's lizard with a
  wing: jade lizard (put wing) / reverse jade lizard (call wing).
- short straddle / short strangle: one short put and one short call of one size, at one strike, or
  the put below the call.
- credit spread / debit spread: one vertical. naked put / naked call / long put / long call: one leg.
- call zebra / put zebra: two longs against one short of the same right further out of the money
  (calls: the longs below the short), a stock substitute whose risk is the debit. call front-ratio /
  put front-ratio: one long against two shorts further out, the second short naked.
- diagonal: shorts expiring before a long they are sold against. calendar: the same, at one strike.
- butterfly / broken-wing butterfly: long-short-long at three strikes of one right, 1-2-1.
- broken-heart butterfly: a broken-wing butterfly pulled apart -- a long vertical nearer the money and
  a wider short vertical further out, one right, one size, a gap between the two shorts.
- a ratio butterfly, named by its quantities read from the top strike ("1-3-2"): long-short-long at
  three strikes of one right whose wings together match the body, so nothing is naked, but are not
  1-2-1 (SPY on 2026-10-18: +1 745P / -3 740P / +2 730P). ratio time spread: the same across expiries.
Anything else is "custom".

The straddle and strangle, the big and reverse lizards, the zebra, the front-ratio and the broken-heart
butterfly are named as the tastylive Options Strategy Guide (2023) draws them.
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


def _width(long: dict[str, Any], short: dict[str, Any]) -> float:
    """A vertical's width in whole-position dollars."""
    return abs(long["strike"] - short["strike"]) * abs(short["quantity"]) * MULTIPLIER


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

    credit = -(cost or 0.0)
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
                if credit >= _width(c_long, c_short) - 1e-6:
                    return "jade lizard (put wing)"
                if credit >= _width(p_long, p_short) - 1e-6:
                    return "reverse jade lizard (call wing)"
                return "iron condor"
        return "custom"
    if not calls and pv:
        return _spread_kind(cost)
    if not puts and cv:
        return _spread_kind(cost)
    # Short straddle / strangle: one short put and one short call of one size, nothing else.
    if (
        len(puts) == 1
        and len(calls) == 1
        and puts[0]["quantity"] < 0
        and puts[0]["quantity"] == calls[0]["quantity"]
    ):
        if puts[0]["strike"] == calls[0]["strike"]:
            return "short straddle"
        return "short strangle" if puts[0]["strike"] < calls[0]["strike"] else "custom"
    # Jade lizard: one short put, no long put, and a short call spread above it (big: at its strike).
    if cv and len(puts) == 1 and puts[0]["quantity"] < 0:
        c_long, c_short = cv
        if c_short["strike"] < c_long["strike"] and puts[0]["strike"] <= c_short["strike"]:
            if credit < _width(c_long, c_short) - 1e-6:
                return "custom"
            return "big lizard" if puts[0]["strike"] == c_short["strike"] else "jade lizard"
    # Reverse jade lizard: one short call, no long call, and a short put spread below it.
    if pv and len(calls) == 1 and calls[0]["quantity"] < 0:
        p_long, p_short = pv
        if p_short["strike"] > p_long["strike"] and calls[0]["strike"] >= p_short["strike"]:
            if credit < _width(p_long, p_short) - 1e-6:
                return "custom"
            return "reverse big lizard" if calls[0]["strike"] == p_short["strike"] else "reverse jade lizard"
    # Risk reversal: long one side, short the other (a wing on the short side is allowed).
    # The short side may be a naked short or a short vertical (FXI: long 33P against a short 33/34C).
    short_calls = sum(leg["quantity"] for leg in calls) < 0 or bool(cv and cv[1]["strike"] < cv[0]["strike"])
    short_puts = sum(leg["quantity"] for leg in puts) < 0 or bool(pv and pv[1]["strike"] > pv[0]["strike"])
    if puts and calls and all(leg["quantity"] > 0 for leg in puts) and short_calls:
        return "risk reversal (bear)"
    if puts and calls and all(leg["quantity"] > 0 for leg in calls) and short_puts:
        return "risk reversal (bull)"
    one_right = calls if not puts else puts if not calls else None
    if one_right:
        named = _butterfly(one_right) or _two_to_one(one_right) or _broken_heart(one_right)
        if named:
            return named
    return _ratio(options) or "custom"


def _butterfly(legs: list[dict[str, Any]]) -> str | None:
    """Long-short-long at three strikes, the body twice the wings: a butterfly (broken-wing if uneven)."""
    if len(legs) != 3:
        return None
    low, mid, high = sorted(legs, key=lambda leg: leg["strike"])
    if not (low["quantity"] > 0 and high["quantity"] > 0 and mid["quantity"] == -2 * low["quantity"]):
        return None
    even = abs((mid["strike"] - low["strike"]) - (high["strike"] - mid["strike"])) < 1e-9
    return "butterfly" if even else "broken-wing butterfly"


def _two_to_one(legs: list[dict[str, Any]]) -> str | None:
    """One long and one short of one right, the long nearer the money (calls: the lower strike) and one
    side twice the other: a zebra when the longs are doubled, a front-ratio when the shorts are."""
    if len(legs) != 2:
        return None
    longs = [leg for leg in legs if leg["quantity"] > 0]
    shorts = [leg for leg in legs if leg["quantity"] < 0]
    if len(longs) != 1 or len(shorts) != 1:
        return None
    long, short = longs[0], shorts[0]
    right = long["right"]
    nearer = long["strike"] < short["strike"] if right == "C" else long["strike"] > short["strike"]
    if not nearer:
        return None
    side = "call" if right == "C" else "put"
    if long["quantity"] == -2 * short["quantity"]:
        return f"{side} zebra"
    if -short["quantity"] == 2 * long["quantity"]:
        return f"{side} front-ratio"
    return None


def _broken_heart(legs: list[dict[str, Any]]) -> str | None:
    """Four legs of one right and one size, read from the money outward: long, short, then a short and a
    long further out -- a long vertical and a short vertical with a gap between them -- the short
    vertical the wider. At equal widths it is a condor, which is not named here."""
    if len(legs) != 4 or len({abs(leg["quantity"]) for leg in legs}) != 1:
        return None
    if len({leg["strike"] for leg in legs}) != 4:
        return None
    outward = sorted(legs, key=lambda leg: leg["strike"], reverse=legs[0]["right"] == "P")
    signs = [leg["quantity"] > 0 for leg in outward]
    if signs != [True, False, False, True]:
        return None
    long_width = abs(outward[1]["strike"] - outward[0]["strike"])
    short_width = abs(outward[3]["strike"] - outward[2]["strike"])
    return "broken-heart butterfly" if short_width > long_width + 1e-9 else None


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


# Every fixed name `classify` can return (a ratio butterfly's name is its quantities, so it is not one):
# the names a per-shape override may key on.
NAMES = frozenset(
    {
        "shares",
        "custom",
        "ratio time spread",
        "calendar",
        "diagonal",
        "naked put",
        "naked call",
        "long put",
        "long call",
        "super bear",
        "super bull",
        "iron butterfly",
        "iron condor",
        "jade lizard",
        "jade lizard (put wing)",
        "big lizard",
        "reverse jade lizard",
        "reverse jade lizard (call wing)",
        "reverse big lizard",
        "short straddle",
        "short strangle",
        "credit spread",
        "debit spread",
        "risk reversal (bear)",
        "risk reversal (bull)",
        "butterfly",
        "broken-wing butterfly",
        "broken-heart butterfly",
        "call zebra",
        "put zebra",
        "call front-ratio",
        "put front-ratio",
    }
)

# The shapes that own a direction and are usually opened near even money: judged on what they can win
# and lose, never on a percentage of their premium.
DIRECTIONAL = frozenset({"super bear", "super bull", "risk reversal (bear)", "risk reversal (bull)"})
# Also judged on risk when bought cheaply against a large tail: a broken-wing butterfly's premium says
# little about what it can lose on the wide-wing side (GLD on 2026-10-13: $193 in, $1,693 at risk), and
# a broken-heart butterfly gives up nearly all its credit to put the gap between its two spreads.
RISK_JUDGED = DIRECTIONAL | {"broken-wing butterfly", "broken-heart butterfly", "ratio time spread"}

# A lizard taken in for at least one side's width has no risk on that side: an in-the-money short
# there is not a broken thesis. The right named is the side its risk is on.
RISK_SIDE = {
    "jade lizard": "P",
    "jade lizard (put wing)": "P",
    "big lizard": "P",
    "reverse jade lizard": "C",
    "reverse jade lizard (call wing)": "C",
    "reverse big lizard": "C",
}


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
