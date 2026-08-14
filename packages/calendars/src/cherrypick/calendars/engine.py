"""Pure decisions over a pre-fetched snapshot: entry planning, structure math, and the fee stack.

No I/O, no clock reads, no network — the same split every module in the suite keeps (provider
fetches, engine decides, book persists, paper_loop owns the clock), which is both what makes the
strategy testable and the suite guardrail on loop-decision paths.

The strike selection is the earnings double-calendar's own hard-won shape: the expected move comes
from the front ATM straddle (via `cherrypick.core.structures`, the one home for the 0.85), and the
strike is chosen from the INTERSECTION of front and back chain strikes — different expirations list
different strike increments (SPX weeklies at 5 points against 25-point cycles), and selecting each
leg independently was caught live producing calendars whose legs sat on different strikes.
"""

from __future__ import annotations

from cherrypick.core import fees as _fees
from cherrypick.core import structures as _structures

BOOKS = ("control", "path")

# Leg roles per side. The front leg is SOLD, the back leg is BOUGHT — a long calendar, paid for as
# a net debit, whose maximum loss is that debit (defined risk with no margin beyond it).
SIDE_ROLES = {"put": ("front_put", "back_put"), "call": ("front_call", "back_call")}


def merged_params(config: dict, book: str) -> dict:
    """`defaults` overlaid with the book's own block — the flies `merged_params` shape, so an
    advised book resolves through the same path as every other."""
    params = {**(config.get("defaults") or {}), **((config.get("books") or {}).get(book) or {})}
    params["book"] = book
    return params


# --------------------------------------------------------------------------- entry planning
def _atm_mid(entries: list[dict], quotes: dict, option_type: str, spot: float) -> tuple[float, dict] | None:
    """(mid, entry) of the quoted strike nearest spot for one option type, or None."""
    candidates = [
        (abs(e["strike_price"] - spot), e)
        for e in entries
        if e["option_type"] == option_type and quotes.get(e["streamer_symbol"]) is not None
    ]
    if not candidates:
        return None
    _, entry = min(candidates, key=lambda pair: pair[0])
    return quotes[entry["streamer_symbol"]]["mid"], entry


def _quoted_strikes(entries: list[dict], quotes: dict, option_type: str) -> dict[float, dict]:
    """strike -> entry, for entries of one type that have a usable quote."""
    return {
        e["strike_price"]: e
        for e in entries
        if e["option_type"] == option_type and quotes.get(e["streamer_symbol"]) is not None
    }


def plan_entry(snapshot: dict, params: dict) -> dict:
    """The week's double calendar off one snapshot: `{"ok": True, "plan": ...}` or a refusal.

    Refusal reasons are the entry-attempt vocabulary — each names the one thing that blocked, so
    the attempts table can say whether a skipped week was a feed problem, a listing problem, or a
    structure problem.
    """
    spot = snapshot["spot"]
    quotes = snapshot["quotes"]
    front, back = snapshot["front"], snapshot["back"]

    atm_call = _atm_mid(front, quotes, "call", spot)
    atm_put = _atm_mid(front, quotes, "put", spot)
    if atm_call is None or atm_put is None:
        return {"ok": False, "reason": "no_em_quotes"}
    call_mid, _ = atm_call
    put_mid, _ = atm_put
    em = _structures.expected_move(call_mid, put_mid, factor=params.get("em_factor", 0.85))
    if em <= 0:
        return {"ok": False, "reason": "no_em_quotes", "detail": "straddle mid is zero"}

    targets = {"put": spot - em, "call": spot + em}
    sides: dict[str, dict] = {}
    for side, target in targets.items():
        front_by_strike = _quoted_strikes(front, quotes, side)
        back_by_strike = _quoted_strikes(back, quotes, side)
        shared = set(front_by_strike) & set(back_by_strike)
        if not shared:
            return {"ok": False, "reason": "no_intersection_strike", "detail": side}
        strike = min(shared, key=lambda s: abs(s - target))
        front_entry, back_entry = front_by_strike[strike], back_by_strike[strike]
        front_quote = quotes[front_entry["streamer_symbol"]]
        back_quote = quotes[back_entry["streamer_symbol"]]
        debit = back_quote["mid"] - front_quote["mid"]
        if debit <= 0:
            # A calendar priced at a credit is a torn read, not free money.
            return {"ok": False, "reason": "non_positive_debit", "detail": side}
        greeks = snapshot.get("greeks") or {}
        sides[side] = {
            "strike": strike,
            "target": target,
            "debit": round(debit, 4),
            "legs": [
                _leg("front", side, front_entry, front_quote, greeks, snapshot["front_expiration"]),
                _leg("back", side, back_entry, back_quote, greeks, snapshot["back_expiration"]),
            ],
        }

    front_iv = _avg_iv(sides, snapshot, which="front")
    back_iv = _avg_iv(sides, snapshot, which="back")
    return {
        "ok": True,
        "plan": {
            "symbol": snapshot["symbol"],
            "spot": spot,
            "em": round(em, 4),
            "em_pct": round(em / spot, 6),
            "front_atm_call_mid": call_mid,
            "front_atm_put_mid": put_mid,
            "front_expiration": snapshot["front_expiration"],
            "back_expiration": snapshot["back_expiration"],
            "front_iv": front_iv,
            "back_iv": back_iv,
            "term_structure": (round((back_iv - front_iv) / back_iv, 6) if front_iv and back_iv else None),
            "sides": sides,
        },
    }


def _leg(which: str, side: str, entry: dict, quote: dict, greeks: dict, expiration: str) -> dict:
    g = greeks.get(entry["streamer_symbol"]) or {}
    return {
        "leg_role": f"{which}_{side}",
        "occ_symbol": entry["occ_symbol"],
        "streamer_symbol": entry["streamer_symbol"],
        "expiration": expiration,
        "strike": entry["strike_price"],
        "option_type": side,
        "action": "Sell to Open" if which == "front" else "Buy to Open",
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "iv": g.get("iv"),
        "delta": g.get("delta"),
    }


def _avg_iv(sides: dict, snapshot: dict, *, which: str) -> float | None:
    values = []
    for side in sides.values():
        for leg in side["legs"]:
            if leg["leg_role"].startswith(which) and leg.get("iv"):
                values.append(leg["iv"])
    return round(sum(values) / len(values), 6) if values else None


# --------------------------------------------------------------------------- structure math
def combo_value(leg_marks: dict) -> float | None:
    """The structure's per-share value at current marks: what closing it would COLLECT at mid
    (back mid minus front mid, per side). None on any missing leg — never zero, `not recorded`
    and `worthless` are different facts."""
    values = {}
    for role, mark in leg_marks.items():
        if mark is None or mark.get("mid") is None:
            return None
        values[role] = mark["mid"]
    long_legs = sum(v for role, v in values.items() if role.startswith("back"))
    short_legs = sum(v for role, v in values.items() if role.startswith("front"))
    return round(long_legs - short_legs, 4)


def settle_intrinsic(strike: float, option_type: str, spot: float) -> float:
    """Cash-settlement value of one leg at the settlement print."""
    if option_type == "put":
        return round(max(0.0, strike - spot), 4)
    return round(max(0.0, spot - strike), 4)


def leg_pnl(leg: dict) -> float | None:
    """One closed/settled leg's per-share P&L: sold legs earn entry minus close, bought legs earn
    close minus entry. None while the leg is open or unpriced."""
    close = leg.get("close_value")
    entry = leg.get("entry_mid")
    if close is None or entry is None:
        return None
    if leg.get("action") == "Sell to Open":
        return round(entry - close, 4)
    return round(close - entry, 4)


# --------------------------------------------------------------------------- the fee stack
def _slippage_dollars(leg_quotes: list[dict], quantity: int, config: dict) -> float:
    """The suite's slippage model (12.5% of each leg's spread, capped at 15% of its mid), in
    dollars — the same knobs `cherrypick.core.fees.DEFAULT_COSTS` carries, read from the same
    config block, so a recalibration there reaches this module through config rather than drift."""
    costs = {**_fees.DEFAULT_COSTS, **(config.get("tastytrade_costs") or {})}
    frac = costs["slippage_frac_of_spread"]
    cap = costs.get("slippage_cap_frac_of_mid")
    total = 0.0
    for q in leg_quotes:
        bid, ask = q.get("bid", 0.0) or 0.0, q.get("ask", 0.0) or 0.0
        slip = max(ask - bid, 0.0) * frac
        if cap is not None:
            slip = min(slip, cap * max((bid + ask) / 2.0, 0.0))
        total += slip * quantity
    return round(total * 100, 2)


def entry_cost(symbol: str, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening ONE side (2 legs, 1 sell) — the index fee schedule (commission, clearing,
    ORF, the SPX $0.60 exchange fee, TAF on the sell) plus modeled slippage."""
    fee = _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, quantity, config)
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def close_cost(symbol: str, leg_quotes: list[dict], quantity: int, config: dict, *, sell_legs: int) -> dict:
    """Cost of actively closing `len(leg_quotes)` legs, `sell_legs` of them sold to close."""
    fee = _fees.ic_close_fee(symbol, quantity, legs=len(leg_quotes), sell_legs=sell_legs, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, quantity, config)
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def settlement_fee(itm_settlements: int) -> float:
    """$5 per DISTINCT ITM settlement symbol (never per contract), charged the next business day."""
    return _fees.ic_expire_fee(itm_settlements)
