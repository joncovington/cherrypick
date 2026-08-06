"""Strategy templates: turn a named strategy + one expiration's chain into concrete legs, plus the
Flip / widen / narrow transforms an order editor needs. Pure + stdlib, same posture as the rest of
``analytics/`` -- the chain (with quotes/greeks already attached) comes in as plain dicts, legs go
out as the same leg-dict shape the builder and ``/api/payoff`` already speak.

Strike selection prefers a **live-delta target** when greeks are present and falls back to a
moneyness proxy when not (documented per template). Delta targets follow the reference platform's
own published mechanics where known: credit verticals sell ~50 delta and buy ~25 (its
credit-spread methodology), covered calls sell 15-20 delta (its income guidance), strangles sell
~16 delta (the 1-sigma practitioner standard recorded in docs/strategy-screening-parameters.md).

DEBIT verticals follow a rule measured from nine observed reference suggestion cards
(2026-08-03): the LONG leg snaps to the nearest at/just-in-the-money strike, and the SHORT leg
sits ~one expected move (spot * iv * sqrt(dte/365)) from spot -- the sold strikes clustered at
0.84-1.05x EM across both sides and multiple tenors when recomputed from each card's own
simulator IV. When iv/dte aren't supplied, the old delta targets remain the fallback.

Every builder returns a list of leg dicts or ``None`` when the chain can't support the shape --
callers show "not available for this expiration", never a half-built basket.
"""

from __future__ import annotations

_MID = "mid"


def _typed(chain: list[dict], option_type: str) -> list[dict]:
    return sorted(
        (o for o in chain if o.get("option_type") == option_type and o.get("quote")),
        key=lambda o: o["strike"],
    )


def _by_delta(options: list[dict], target: float) -> dict | None:
    """The option whose |delta| is nearest `target`; None when no option carries a delta."""
    with_delta = [o for o in options if (o.get("greeks") or {}).get("delta") is not None]
    if not with_delta:
        return None
    return min(with_delta, key=lambda o: abs(abs(o["greeks"]["delta"]) - target))


def _by_moneyness(options: list[dict], spot: float, otm_frac: float, side: str) -> dict | None:
    """Fallback strike pick when greeks are absent: the strike nearest `otm_frac` out-of-the-money
    on the relevant side (0.0 = ATM)."""
    target = spot * (1 + otm_frac) if side == "call" else spot * (1 - otm_frac)
    return min(options, key=lambda o: abs(o["strike"] - target)) if options else None


# Rough delta -> OTM-fraction fallbacks so a greeks-less chain still yields sane strikes.
_DELTA_TO_OTM = {0.50: 0.0, 0.35: 0.03, 0.25: 0.05, 0.16: 0.08}


def _pick(options: list[dict], spot: float, delta_target: float, side: str) -> dict | None:
    picked = _by_delta(options, delta_target)
    if picked is None:
        picked = _by_moneyness(options, spot, _DELTA_TO_OTM.get(delta_target, 0.05), side)
    return picked


def _leg(option: dict, quantity: int) -> dict:
    quote = option.get("quote") or {}
    greeks = option.get("greeks") or {}
    return {
        "kind": "call" if option["option_type"] == "C" else "put",
        "strike": option["strike"],
        "quantity": quantity,
        "price": quote.get(_MID) or 0.0,
        "symbol": option.get("symbol"),
        "expiration": option.get("expiration"),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
    }


def _distinct(*options: dict | None) -> bool:
    """All present, and no two picks landed on the same (strike, type) -- a thin chain can snap
    two different delta targets onto one option, which is not a real spread."""
    picked = [o for o in options if o is not None]
    if len(picked) != len(options):
        return False
    return len({(o["strike"], o["option_type"]) for o in picked}) == len(picked)


def _nearest_itm(options: list[dict], spot: float, side: str) -> dict | None:
    """The nearest at/just-in-the-money strike -- the observed long-leg rule for debit spreads
    and long options (e.g. the 135 call suggested at a 138.88 spot)."""
    if side == "call":
        itm = [o for o in options if o["strike"] <= spot]
        return max(itm, key=lambda o: o["strike"]) if itm else (options[0] if options else None)
    itm = [o for o in options if o["strike"] >= spot]
    return min(itm, key=lambda o: o["strike"]) if itm else (options[-1] if options else None)


def _expected_move(spot: float, iv: float | None, dte: float | None) -> float | None:
    if not iv or not dte or iv <= 0 or dte <= 0:
        return None
    return spot * iv * (dte / 365.0) ** 0.5


def build(
    name: str, chain: list[dict], spot: float, iv: float | None = None, dte: float | None = None
) -> list[dict] | None:
    """Legs for the named template, or None when the chain can't support it. `iv`/`dte` enable the
    expected-move short-strike rule for debit verticals (see module docstring)."""
    calls, puts = _typed(chain, "C"), _typed(chain, "P")

    if name == "long_call":
        option = _nearest_itm(calls, spot, "call")  # observed rule: nearest at/just-ITM strike
        return [_leg(option, 1)] if option else None
    if name == "long_put":
        option = _nearest_itm(puts, spot, "put")
        return [_leg(option, 1)] if option else None
    if name == "short_put":
        option = _pick(puts, spot, 0.25, "put")
        return [_leg(option, -1)] if option else None
    if name == "covered_call":
        option = _pick(calls, spot, 0.18, "call")
        if not option:
            return None
        return [
            {
                "kind": "stock",
                "strike": None,
                "quantity": 1,
                "price": spot,
                "symbol": None,
                "expiration": None,
                "bid": None,
                "ask": None,
                "delta": None,
                "gamma": None,
                "theta": None,
                "vega": None,
            },
            _leg(option, -1),
        ]
    if name in ("put_vertical_credit", "put_vertical_debit"):
        if name.endswith("debit"):
            buy = _nearest_itm(puts, spot, "put")
            em = _expected_move(spot, iv, dte)
            if em is not None:
                below = [o for o in puts if o["strike"] < (buy["strike"] if buy else spot)]
                sell = min(below, key=lambda o: abs(o["strike"] - (spot - em))) if below else None
            else:
                sell = _pick(puts, spot, 0.25, "put")
            if not _distinct(buy, sell):
                return None
            return [_leg(buy, 1), _leg(sell, -1)]
        near = _pick(puts, spot, 0.50, "put")
        far = _pick(puts, spot, 0.25, "put")
        if not _distinct(near, far):
            return None
        return [_leg(near, -1), _leg(far, 1)]
    if name in ("call_vertical_credit", "call_vertical_debit"):
        if name.endswith("debit"):
            buy = _nearest_itm(calls, spot, "call")
            em = _expected_move(spot, iv, dte)
            if em is not None:
                above = [o for o in calls if o["strike"] > (buy["strike"] if buy else spot)]
                sell = min(above, key=lambda o: abs(o["strike"] - (spot + em))) if above else None
            else:
                sell = _pick(calls, spot, 0.25, "call")
            if not _distinct(buy, sell):
                return None
            return [_leg(buy, 1), _leg(sell, -1)]
        near = _pick(calls, spot, 0.50, "call")
        far = _pick(calls, spot, 0.25, "call")
        if not _distinct(near, far):
            return None
        return [_leg(near, -1), _leg(far, 1)]
    if name == "short_straddle":
        call, put = _pick(calls, spot, 0.50, "call"), _pick(puts, spot, 0.50, "put")
        return [_leg(call, -1), _leg(put, -1)] if call and put else None
    if name == "short_strangle":
        call, put = _pick(calls, spot, 0.16, "call"), _pick(puts, spot, 0.16, "put")
        if not (call and put) or call["strike"] <= put["strike"]:
            return None
        return [_leg(call, -1), _leg(put, -1)]
    if name == "iron_condor":
        short_call, short_put = _pick(calls, spot, 0.25, "call"), _pick(puts, spot, 0.25, "put")
        long_call, long_put = _pick(calls, spot, 0.16, "call"), _pick(puts, spot, 0.16, "put")
        legs = [short_call, long_call, short_put, long_put]
        if not _distinct(*legs) or short_call["strike"] <= short_put["strike"]:
            return None
        return [_leg(short_put, -1), _leg(long_put, 1), _leg(short_call, -1), _leg(long_call, 1)]
    return None


TEMPLATES = (
    "long_call",
    "long_put",
    "short_put",
    "covered_call",
    "put_vertical_credit",
    "put_vertical_debit",
    "call_vertical_credit",
    "call_vertical_debit",
    "short_straddle",
    "short_strangle",
    "iron_condor",
)


def flip(legs: list[dict], chain: list[dict], spot: float) -> list[dict] | None:
    """Mirror a directional basket: calls become puts (and vice versa) at strikes reflected around
    spot, snapped to listed strikes -- the order editor's Flip Strategy button. Stock legs and
    baskets a reflection can't map (missing strike, no mirror option listed) return None."""
    calls, puts = _typed(chain, "C"), _typed(chain, "P")
    flipped: list[dict] = []
    for leg in legs:
        if leg["kind"] == "stock":
            return None
        mirror_kind = "put" if leg["kind"] == "call" else "call"
        pool = puts if mirror_kind == "put" else calls
        if not pool or leg.get("strike") is None:
            return None
        target = 2 * spot - leg["strike"]
        option = min(pool, key=lambda o: abs(o["strike"] - target))
        flipped.append(_leg(option, leg["quantity"]))
    strikes = {(lg["kind"], lg["strike"]) for lg in flipped}
    return flipped if len(strikes) == len(flipped) else None


def adjust_width(legs: list[dict], chain: list[dict], step: int) -> list[dict] | None:
    """Move every LONG option leg one listed strike further from (step=+1) or closer to (step=-1)
    its short partner -- the editor's -/+ Width control. Only meaningful for baskets with both
    short and long options; otherwise None."""
    shorts = [lg for lg in legs if lg["quantity"] < 0 and lg["kind"] != "stock"]
    if not shorts or all(lg["quantity"] >= 0 for lg in legs):
        return None
    out: list[dict] = []
    for leg in legs:
        if leg["quantity"] >= 0 and leg["kind"] != "stock":
            same_type = _typed(chain, "C" if leg["kind"] == "call" else "P")
            strikes = [o["strike"] for o in same_type]
            if leg["strike"] not in strikes:
                return None
            idx = strikes.index(leg["strike"])
            anchor = min(shorts, key=lambda s: abs(s["strike"] - leg["strike"]))
            outward = 1 if leg["strike"] >= anchor["strike"] else -1
            new_idx = idx + outward * step
            if not 0 <= new_idx < len(strikes):
                return None
            option = same_type[new_idx]
            if option["strike"] == anchor["strike"]:
                return None  # width would collapse to zero
            out.append(_leg(option, leg["quantity"]))
        else:
            out.append(dict(leg))
    return out
