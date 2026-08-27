"""Pure decisions over a pre-fetched snapshot: call-credit-spread selection, worksheet math, and
the fee stack.

No I/O, no clock reads, no network — the provider fetches, this decides, book.py persists,
paper_loop.py owns the clock.

The structure is always one VXX call credit spread: short a call near `short_delta_target` (~0.30
delta), buy the wing `spread_width` dollars higher at the nearest listed strike, same expiration —
for every book. Keeping one shape across books means they differ only in their declared variable
(entry gate, exit rule), never in what is traded.
"""

from __future__ import annotations

import math

from cherrypick.core import fees as _fees

BOOKS = ("control", "noflip", "hook")

# VXX is a standard American-style, physically-settled equity option — the calendars/pmcc
# decomposition applies verbatim. No `cash` style is offered: this module trades exactly one
# underlying and it is always physical, so a config declaring anything else is a refusal, not a
# silent default.
SETTLEMENT_STYLE = "physical"


def merged_params(config: dict, book: str) -> dict:
    """`defaults` overlaid with the book's own block — the flies/pmcc `merged_params` shape, so an
    advised book resolves through the same path as every other."""
    params = {**(config.get("defaults") or {}), **((config.get("books") or {}).get(book) or {})}
    params["book"] = book
    return params


# --------------------------------------------------------------------------- spread selection
def _quoted_calls(entries: list[dict], quotes: dict) -> list[dict]:
    """Call entries with a usable quote attached, sorted by strike ascending."""
    out = []
    for e in entries:
        if e["option_type"] != "call":
            continue
        quote = quotes.get(e["streamer_symbol"])
        if quote is None:
            continue
        out.append({**e, "quote": quote})
    return sorted(out, key=lambda e: e["strike_price"])


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call_delta(spot: float, strike: float, dte_days: int, iv: float, rate: float = 0.0) -> float | None:
    """Black-Scholes call delta N(d1), from quantities the chain already publishes (spot, strike,
    DTE, IV) — a COMPUTATION, not a heuristic. `rate` defaults to 0.0: VXX options are short-dated
    enough (~30-45 DTE) that the risk-free-rate term moves d1 by a rounding error next to the
    vol-driven one, and this module holds no rate feed to do better. None on a degenerate input
    (zero/negative IV or DTE) — the caller treats that exactly like a missing delta, never a 0.5
    default."""
    if iv is None or iv <= 0 or dte_days is None or dte_days <= 0 or spot <= 0 or strike <= 0:
        return None
    t = dte_days / 365.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return round(_norm_cdf(d1), 6)


def select_short(
    entries: list[dict], quotes: dict, greeks: dict, spot: float, params: dict, dte_days: int | None = None
) -> dict:
    """The short call: the OTM strike (above spot) whose delta is nearest `short_delta_target`.

    Delta comes from the feed when available (`selected_by: "delta"`). When the feed's delta is
    missing but its IV is on file, delta is COMPUTED via Black-Scholes from spot/strike/DTE/IV —
    quantities the chain already publishes — rather than skipped (`selected_by:
    "delta_computed"`); every such row stays excludable read-side, the pmcc `selected_by` pattern.
    Only a strike with neither a real nor a computable delta is skipped. A chain with no strike
    clearing either path refuses `no_delta_for_selection` rather than guessing a strike off
    moneyness alone.

    The fallback itself is config-gated: `allow_delta_computed_fallback` (default True) must be
    True for the Black-Scholes path to run at all. Set False to make a missing feed delta refuse
    outright — useful to isolate whether the computed-delta fallback is itself affecting results,
    or while its accuracy against the feed's real deltas is still being validated.
    """
    target = params.get("short_delta_target", 0.30)
    allow_fallback = params.get("allow_delta_computed_fallback", True)
    best = None
    for e in _quoted_calls(entries, quotes):
        strike = e["strike_price"]
        if strike <= spot:
            continue
        g = greeks.get(e["streamer_symbol"]) or {}
        delta, selected_by = g.get("delta"), "delta"
        if delta is None and allow_fallback:
            delta = bs_call_delta(spot, strike, dte_days, g.get("iv"))
            selected_by = "delta_computed"
        if delta is None:
            continue
        distance = abs(abs(delta) - target)
        if best is None or distance < best["distance"]:
            best = {
                "entry": e,
                "strike": strike,
                "mid": e["quote"]["mid"],
                "delta": delta,
                "selected_by": selected_by,
                "distance": distance,
            }
    if best is None:
        return {"ok": False, "reason": "no_delta_for_selection"}
    return {
        "ok": True,
        "entry": best["entry"],
        "strike": best["strike"],
        "mid": best["mid"],
        "delta": best["delta"],
        "selected_by": best["selected_by"],
    }


def select_long(entries: list[dict], quotes: dict, short_strike: float, params: dict) -> dict:
    """The long wing: the LOWEST listed strike at or above `short_strike + spread_width` — the
    nearest available width that is at least the declared one, never narrower."""
    width = params.get("spread_width", 5.0)
    floor = short_strike + width
    candidates = [e for e in _quoted_calls(entries, quotes) if e["strike_price"] >= floor]
    if not candidates:
        return {"ok": False, "reason": "no_wing_strike"}
    e = candidates[0]  # ascending order — the lowest strike clearing the floor
    return {"ok": True, "entry": e, "strike": e["strike_price"], "mid": e["quote"]["mid"]}


def _spread_pct(quote: dict) -> float | None:
    if quote.get("mid") in (None, 0):
        return None
    return (quote["ask"] - quote["bid"]) / quote["mid"]


def _spread_abs(quote: dict) -> float | None:
    if quote.get("ask") is None or quote.get("bid") is None:
        return None
    return quote["ask"] - quote["bid"]


def _wing_spread_blocks(quote: dict, max_pct: float, max_abs: float) -> float | None:
    """The refusing spread_pct for the LONG wing, or None if the wing is acceptable.

    **A percentage spread test is the wrong instrument for a cheap wing.** Far-OTM VXX calls are
    routinely bid-less — on 2026-08-27 every front-expiration strike from 22 up quoted `bid 0.00` —
    and a zero bid makes `(ask - 0) / (ask/2)` exactly 2.0 whatever the option costs. Read as a
    percentage that is a "200% spread"; read in money it is two cents. 56 of that session's 62
    entry refusals were this, all at exactly 2.000.

    So the wing is refused only when the spread is wide BOTH in percent AND in absolute money. The
    short leg keeps the plain percentage test: it is the leg being sold, its premium is the whole
    credit, and paying up there is exactly what the gate exists to prevent.
    """
    pct = _spread_pct(quote)
    if pct is None or pct <= max_pct:
        return None
    dollars = _spread_abs(quote)
    if dollars is not None and dollars <= max_abs:
        return None
    return pct


def plan_entry(snapshot: dict, params: dict) -> dict:
    """The spread off one snapshot: `{"ok": True, "plan": ...}` or a refusal naming the one thing
    that blocked, so the attempts table can tell a feed problem from a market problem."""
    spot = snapshot["spot"]
    quotes = snapshot["quotes"]
    greeks = snapshot.get("greeks") or {}
    chain = snapshot["chain"]

    short_pick = select_short(chain, quotes, greeks, spot, params, snapshot.get("dte"))
    if not short_pick["ok"]:
        return short_pick
    max_leg_spread_pct = params.get("max_leg_spread_pct", 0.25)
    if (pct := _spread_pct(short_pick["entry"]["quote"])) is not None and pct > max_leg_spread_pct:
        return {"ok": False, "reason": "spread_too_wide", "detail": {"leg": "short", "spread_pct": pct}}

    long_pick = select_long(chain, quotes, short_pick["strike"], params)
    if not long_pick["ok"]:
        return long_pick
    max_wing_spread_abs = params.get("max_wing_spread_abs", 0.05)
    pct = _wing_spread_blocks(long_pick["entry"]["quote"], max_leg_spread_pct, max_wing_spread_abs)
    if pct is not None:
        return {
            "ok": False,
            "reason": "spread_too_wide",
            "detail": {
                "leg": "long",
                "spread_pct": pct,
                "spread_abs": _spread_abs(long_pick["entry"]["quote"]),
            },
        }

    metrics = worksheet_metrics(
        short_mid=short_pick["mid"],
        short_strike=short_pick["strike"],
        long_mid=long_pick["mid"],
        long_strike=long_pick["strike"],
    )
    min_pct = params.get("min_credit_pct_of_width", 0.15)
    if metrics["credit_pct_of_width"] is None or metrics["credit_pct_of_width"] < min_pct:
        return {
            "ok": False,
            "reason": "credit_below_floor",
            "detail": {"credit_pct_of_width": metrics["credit_pct_of_width"], "floor": min_pct},
        }

    short_greeks = greeks.get(short_pick["entry"]["streamer_symbol"]) or {}
    long_greeks = greeks.get(long_pick["entry"]["streamer_symbol"]) or {}
    return {
        "ok": True,
        "plan": {
            "symbol": snapshot["symbol"],
            "spot": spot,
            "expiration": snapshot["expiration"],
            "dte": snapshot["dte"],
            "short_selected_by": short_pick["selected_by"],
            **metrics,
            "legs": [
                _leg(
                    "short_call",
                    "Sell to Open",
                    short_pick["entry"],
                    short_pick["entry"]["quote"],
                    short_greeks,
                    snapshot["expiration"],
                ),
                _leg(
                    "long_call",
                    "Buy to Open",
                    long_pick["entry"],
                    long_pick["entry"]["quote"],
                    long_greeks,
                    snapshot["expiration"],
                ),
            ],
        },
    }


def _leg(role: str, action: str, entry: dict, quote: dict, greeks: dict, expiration: str) -> dict:
    return {
        "leg_role": role,
        "occ_symbol": entry["occ_symbol"],
        "streamer_symbol": entry["streamer_symbol"],
        "expiration": expiration,
        "strike": entry["strike_price"],
        "option_type": "call",
        "action": action,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "iv": greeks.get("iv"),
        "delta": greeks.get("delta"),
    }


def worksheet_metrics(*, short_mid: float, short_strike: float, long_mid: float, long_strike: float) -> dict:
    """The worksheet, computed once and stored as MEASURES (never only as buckets — a threshold can
    be re-cut later, a bucket cannot). All per share; the ledger scales by ×100×quantity."""
    width = long_strike - short_strike
    credit = short_mid - long_mid
    return {
        "short_strike": short_strike,
        "short_mid": round(short_mid, 4),
        "long_strike": long_strike,
        "long_mid": round(long_mid, 4),
        "width": round(width, 4),
        "credit": round(credit, 4),
        "max_loss": round(width - credit, 4) if width else None,
        "credit_pct_of_width": round(credit / width, 6) if width else None,
    }


def spread_close_cost(short_quote: dict, long_quote: dict) -> float | None:
    """What CLOSING the spread would cost at mid right now (buy back the short, sell the long) — the
    number the profit-take rule reads. None if either leg is unpriced."""
    if short_quote is None or long_quote is None:
        return None
    return round(short_quote["mid"] - long_quote["mid"], 4)


def settle_intrinsic(strike: float, spot: float) -> float:
    """A call's intrinsic value at the settlement print."""
    return round(max(0.0, spot - strike), 4)


# --------------------------------------------------------------------------- physical settlement
#
# The calendars/pmcc decomposition, unchanged: book delivered shares at the SETTLEMENT SPOT, not the
# strike. A short call assigned ITM delivers SHORT shares; a long call exercised ITM (rare here —
# close_dte keeps most positions out of expiration week) delivers LONG shares. Both are covered/sold
# the next session, together, so a Friday settlement can carry shares over the weekend.
def assignment_from(leg: dict, spot: float, quantity: int) -> dict | None:
    """The share position one ITM leg delivers at expiry, or None if it expires worthless."""
    if settle_intrinsic(leg["strike"], spot) <= 0:
        return None
    sold = leg.get("action") == "Sell to Open"
    return {
        "direction": "short" if sold else "long",
        "shares": 100 * int(quantity or 1),
        "basis": round(float(spot), 4),
        "strike": leg["strike"],
        "option_type": "call",
    }


# Re-exported: calendars/pmcc/curve all model physical settlement and must not disagree about the
# money.
from cherrypick.core import settlement as _settlement  # noqa: E402

share_pnl = _settlement.share_pnl  # noqa: F401


def leg_pnl(leg: dict) -> float | None:
    """One closed/settled leg's per-share P&L. None while the leg is open or unpriced."""
    close = leg.get("close_value")
    entry = leg.get("entry_mid")
    if close is None or entry is None:
        return None
    if leg.get("action") == "Sell to Open":
        return round(entry - close, 4)
    return round(close - entry, 4)


# --------------------------------------------------------------------------- the fee stack
def _slippage_dollars(leg_quotes: list[dict], quantity: int, config: dict) -> float:
    """The suite's slippage model (12.5% of each leg's spread, capped at 15% of its mid) — the same
    knobs `cherrypick.core.fees.DEFAULT_COSTS` carries, read from the same config block."""
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
    """Cost of opening the spread (2 legs, 1 sold): commission/clearing/ORF/TAF (no broad-based
    index exchange fee — VXX is an ETN, off that schedule) plus modeled slippage."""
    fee = _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, quantity, config)
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def close_cost(symbol: str, leg_quotes: list[dict], quantity: int, config: dict, *, sell_legs: int) -> dict:
    """Cost of actively closing the spread (2 legs, `sell_legs` sold)."""
    fee = _fees.ic_close_fee(symbol, quantity, legs=len(leg_quotes), sell_legs=sell_legs, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, quantity, config)
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def settlement_fee(itm_settlements: int) -> float:
    """$5 per DISTINCT ITM settlement symbol (never per contract), the next business day."""
    return _fees.ic_expire_fee(itm_settlements)


def assignment_fee(assignment: dict, dispose_price: float) -> float:
    """Everything one physical assignment/exercise costs from delivery to disposal."""
    return _fees.assignment_round_trip_fee(
        assignment["shares"],
        assignment["basis"],
        dispose_price,
        direction=assignment["direction"],
    )
