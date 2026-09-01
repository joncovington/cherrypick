"""Pure decisions over a pre-fetched snapshot: BWB construction, the add-on vertical, worksheet
math, cash-settlement intrinsics, and the fee stack.

No I/O, no clock reads, no network — the provider fetches, this decides, book.py persists,
paper_loop.py owns the clock.

The base structure is always the same shape for every book: a put broken-wing butterfly centered
`expected_move` below spot — short x2 the body, long x1 one increment above (near wing, toward
spot), long x1 two increments below (far wing) — priced for a net credit at mid, zero floor by
design (a deliberate departure from the suite's `min_credit_pct_of_width` convention, per the
plan). Books differ only in whether/when the add-on vertical fires; that logic lives in
`triggers.py` and `management.py`, not here.
"""

from __future__ import annotations

from cherrypick.core import fees as _fees
from cherrypick.core import structures as _structures

BOOKS = ("control", "delta", "bounce", "flip")

# SPX is cash-settled, European-style: no assignment machinery is offered here. A config declaring
# anything else is out of scope by construction — this module trades exactly one underlying.
SETTLEMENT_STYLE = "cash"

STRIKE_INCREMENT = 5.0


def merged_params(config: dict, book: str) -> dict:
    """`defaults` overlaid with the book's own block — the curve/pmcc `merged_params` shape, so an
    advised book resolves through the same path as every other."""
    params = {**(config.get("defaults") or {}), **((config.get("books") or {}).get(book) or {})}
    params["book"] = book
    return params


# --------------------------------------------------------------------------- strike selection
def _puts(entries: list[dict], quotes: dict) -> dict[float, dict]:
    """{strike: entry+quote} for put entries with a usable quote, chain-listed strikes only."""
    out: dict[float, dict] = {}
    for e in entries:
        if e["option_type"] != "put":
            continue
        quote = quotes.get(e["streamer_symbol"])
        if quote is None:
            continue
        out[e["strike_price"]] = {**e, "quote": quote}
    return out


def _calls(entries: list[dict], quotes: dict) -> dict[float, dict]:
    """The call side of the chain with a usable quote, keyed by strike — `_puts`' mirror, for the
    wall book's call-side structure."""
    out = {}
    for e in entries:
        if e["option_type"] != "call":
            continue
        q = quotes.get(e["streamer_symbol"])
        if q is None:
            continue
        out[e["strike_price"]] = {**e, "quote": q}
    return out


def _nearest_strike(strikes: list[float], target: float) -> float | None:
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - target))


def plan_expected_move(snapshot: dict, params: dict) -> dict:
    """The expected move off the target expiration's ATM straddle mids at the entry tick. Refuses
    `no_expected_move` rather than guess when either ATM leg is unpriced — the structure cannot be
    placed without its anchor."""
    spot = snapshot["spot"]
    quotes = snapshot["quotes"]
    chain = snapshot["chain"]
    calls = {e["strike_price"]: e for e in chain if e["option_type"] == "call"}
    puts = {e["strike_price"]: e for e in chain if e["option_type"] == "put"}
    common = sorted(set(calls) & set(puts))
    atm_strike = _nearest_strike(common, spot)
    if atm_strike is None:
        return {"ok": False, "reason": "no_expected_move"}
    call_quote = quotes.get(calls[atm_strike]["streamer_symbol"])
    put_quote = quotes.get(puts[atm_strike]["streamer_symbol"])
    if call_quote is None or put_quote is None:
        return {"ok": False, "reason": "no_expected_move"}
    em = _structures.expected_move(call_quote["mid"], put_quote["mid"])
    return {
        "ok": True,
        "atm_strike": atm_strike,
        "atm_call_mid": call_quote["mid"],
        "atm_put_mid": put_quote["mid"],
        "expected_move": round(em, 4),
    }


def select_strikes(spot: float, expected_move: float, params: dict, listed: list[float]) -> dict:
    """body (short x2) at spot - expected_move; near wing one increment ABOVE the body (toward
    spot); far wing `far_wing_increments` (>=2) increments BELOW the body. All snapped to the
    nearest LISTED strike. Refuses `no_strikes_in_window` if any leg's target has no listed
    strike within one full increment."""
    increment = params.get("strike_increment", STRIKE_INCREMENT)
    near_incr = int(params.get("near_wing_increments", 1))
    far_incr = int(params.get("far_wing_increments", 2))
    if far_incr < 2:
        return {"ok": False, "reason": "far_wing_increments_below_floor"}
    if not listed:
        return {"ok": False, "reason": "no_strikes_in_window"}

    body_target = spot - expected_move
    body = _nearest_strike(listed, body_target)
    near = _nearest_strike(listed, body + near_incr * increment)
    far = _nearest_strike(listed, body - far_incr * increment)
    if body is None or near is None or far is None:
        return {"ok": False, "reason": "no_strikes_in_window"}
    if not (far < body < near):
        return {"ok": False, "reason": "no_strikes_in_window"}
    return {"ok": True, "body": body, "near": near, "far": far}


def _leg(
    role: str, action: str, entry: dict, quote: dict, greeks: dict | None, expiration: str, option_type: str = "put"
) -> dict:
    g = greeks or {}
    return {
        "leg_role": role,
        "occ_symbol": entry["occ_symbol"],
        "streamer_symbol": entry["streamer_symbol"],
        "expiration": expiration,
        "strike": entry["strike_price"],
        "option_type": option_type,
        "action": action,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "mid": quote["mid"],
        "iv": g.get("iv"),
        "delta": g.get("delta"),
    }


def plan_entry(snapshot: dict, params: dict) -> dict:
    """The base BWB off one snapshot: `{"ok": True, "plan": ...}` or a refusal naming the one thing
    that blocked."""
    em_result = plan_expected_move(snapshot, params)
    if not em_result["ok"]:
        return em_result

    spot = snapshot["spot"]
    quotes = snapshot["quotes"]
    greeks = snapshot.get("greeks") or {}
    put_book = _puts(snapshot["chain"], quotes)
    listed = sorted(put_book)

    strikes = select_strikes(spot, em_result["expected_move"], params, listed)
    if not strikes["ok"]:
        return strikes

    body_e, near_e, far_e = put_book[strikes["body"]], put_book[strikes["near"]], put_book[strikes["far"]]
    max_leg_spread_pct = params.get("max_leg_spread_pct", 0.25)
    for name, e in (("body", body_e), ("near", near_e), ("far", far_e)):
        pct = _spread_pct(e["quote"])
        if pct is not None and pct > max_leg_spread_pct:
            return {"ok": False, "reason": "spread_too_wide", "detail": {"leg": name, "spread_pct": pct}}

    metrics = bwb_metrics(
        body_mid=body_e["quote"]["mid"],
        near_mid=near_e["quote"]["mid"],
        far_mid=far_e["quote"]["mid"],
        body_strike=strikes["body"],
        near_strike=strikes["near"],
        far_strike=strikes["far"],
    )
    credit_floor = params.get("credit_floor", 0.0)
    if metrics["credit"] is None or metrics["credit"] <= credit_floor:
        return {"ok": False, "reason": "no_credit", "detail": {"credit": metrics["credit"]}}

    expiration = snapshot["expiration"]
    legs = [
        _leg("near_long", "Buy to Open", near_e, near_e["quote"], greeks.get(near_e["streamer_symbol"]), expiration),
        _leg(
            "body_short_1", "Sell to Open", body_e, body_e["quote"], greeks.get(body_e["streamer_symbol"]), expiration
        ),
        _leg(
            "body_short_2", "Sell to Open", body_e, body_e["quote"], greeks.get(body_e["streamer_symbol"]), expiration
        ),
        _leg("far_long", "Buy to Open", far_e, far_e["quote"], greeks.get(far_e["streamer_symbol"]), expiration),
    ]
    return {
        "ok": True,
        "plan": {
            "symbol": snapshot["symbol"],
            "spot": spot,
            "expiration": expiration,
            "dte": snapshot["dte"],
            **em_result,
            **metrics,
            "legs": legs,
        },
    }


def select_wall_strikes(call_wall: float, spot: float, params: dict, listed: list[float]) -> dict:
    """`select_strikes` mirrored for the wall book: body (short x2) at the CALL WALL; near wing one
    increment BELOW the body (toward spot); far wing `far_wing_increments` (>=2) increments ABOVE.
    Snapped to listed strikes, and the wall must still sit above spot AFTER snapping — a body at or
    below spot is short calls in the money, a directional bet, not a "wall holds" bet."""
    increment = params.get("strike_increment", STRIKE_INCREMENT)
    near_incr = int(params.get("near_wing_increments", 1))
    far_incr = int(params.get("far_wing_increments", 2))
    if far_incr < 2:
        return {"ok": False, "reason": "far_wing_increments_below_floor"}
    if not listed:
        return {"ok": False, "reason": "no_strikes_in_window"}

    body = _nearest_strike(listed, call_wall)
    if body is None:
        return {"ok": False, "reason": "no_strikes_in_window"}
    if body <= spot:
        return {"ok": False, "reason": "call_wall_not_above_spot"}
    near = _nearest_strike(listed, body - near_incr * increment)
    far = _nearest_strike(listed, body + far_incr * increment)
    if near is None or far is None:
        return {"ok": False, "reason": "no_strikes_in_window"}
    if not (near < body < far):
        return {"ok": False, "reason": "no_strikes_in_window"}
    return {"ok": True, "body": body, "near": near, "far": far}


def plan_wall_entry(snapshot: dict, params: dict, call_wall: float | None) -> dict:
    """The wall book's call-side BWB off the same snapshot: +1 near / -2 body / +1 far in CALLS,
    body at the GEX call wall, net credit required.

    Origin: the gex module's pin study (2026-08-31, 23 sessions) — a BOUND bet, not a pin bet: the
    close finished at or below the morning wall 19-21/23 while the tent captured 2/23. At ~7 DTE
    this book asks a question the study did not answer (does the wall bound price over a WEEK?),
    which is exactly why it gets its own book instead of borrowing the study as evidence.

    Two deliberate differences from `plan_entry`, both stated rather than silent:

    * No expected-move dependency — the wall is the placement, so a session with no wall reading
      is a refusal (`call_wall_unavailable`), never an EM fallback wearing this book's name.
    * The spread gate is percent AND absolute money, per leg (the curve/calendars rule). These are
      OTM calls at and above the wall, where a done short quotes 0.00 bid against a penny ask —
      a 200% ratio and a one-cent width. The put books' gate stays percentage-only: their legs sit
      an expected move below spot where that arithmetic has not bitten, and changing what THEY
      admit would be its own measurement break.
    """
    if call_wall is None:
        return {"ok": False, "reason": "call_wall_unavailable"}
    spot = snapshot["spot"]
    if call_wall <= spot:
        return {"ok": False, "reason": "call_wall_not_above_spot"}

    quotes = snapshot["quotes"]
    greeks = snapshot.get("greeks") or {}
    call_book = _calls(snapshot["chain"], quotes)
    listed = sorted(call_book)

    strikes = select_wall_strikes(call_wall, spot, params, listed)
    if not strikes["ok"]:
        return strikes

    body_e, near_e, far_e = call_book[strikes["body"]], call_book[strikes["near"]], call_book[strikes["far"]]
    max_pct = params.get("max_leg_spread_pct", 0.25)
    max_abs = params.get("max_leg_spread_abs", 0.05)
    for name, e in (("body", body_e), ("near", near_e), ("far", far_e)):
        pct = _spread_pct(e["quote"])
        width = (
            None
            if e["quote"].get("ask") is None or e["quote"].get("bid") is None
            else e["quote"]["ask"] - e["quote"]["bid"]
        )
        if pct is not None and pct > max_pct and (width is None or width > max_abs):
            return {"ok": False, "reason": "spread_too_wide", "detail": {"leg": name, "spread_pct": pct}}

    # The same worksheet keys as `bwb_metrics`, with the widths read from the mirrored geometry:
    # narrow is body-to-near (below, toward spot), wide is body-to-far (above). The loss directions
    # swap sides with the structure — the risk is a rally through the far wing.
    credit = 2 * body_e["quote"]["mid"] - near_e["quote"]["mid"] - far_e["quote"]["mid"]
    narrow_width = strikes["body"] - strikes["near"]
    wide_width = strikes["far"] - strikes["body"]
    credit_floor = params.get("credit_floor", 0.0)
    if credit <= credit_floor:
        return {"ok": False, "reason": "no_credit", "detail": {"credit": round(credit, 4)}}

    expiration = snapshot["expiration"]
    legs = [
        _leg(
            "near_long", "Buy to Open", near_e, near_e["quote"], greeks.get(near_e["streamer_symbol"]),
            expiration, option_type="call",
        ),
        _leg(
            "body_short_1", "Sell to Open", body_e, body_e["quote"], greeks.get(body_e["streamer_symbol"]),
            expiration, option_type="call",
        ),
        _leg(
            "body_short_2", "Sell to Open", body_e, body_e["quote"], greeks.get(body_e["streamer_symbol"]),
            expiration, option_type="call",
        ),
        _leg(
            "far_long", "Buy to Open", far_e, far_e["quote"], greeks.get(far_e["streamer_symbol"]),
            expiration, option_type="call",
        ),
    ]
    return {
        "ok": True,
        "plan": {
            "symbol": snapshot["symbol"],
            "spot": spot,
            "expiration": expiration,
            "dte": snapshot["dte"],
            "call_wall": call_wall,
            "body_strike": strikes["body"],
            "body_mid": round(body_e["quote"]["mid"], 4),
            "near_strike": strikes["near"],
            "near_mid": round(near_e["quote"]["mid"], 4),
            "far_strike": strikes["far"],
            "far_mid": round(far_e["quote"]["mid"], 4),
            "credit": round(credit, 4),
            "narrow_width": round(narrow_width, 4),
            "wide_width": round(wide_width, 4),
            "max_loss_up": round(wide_width - credit, 4),
            "max_loss_down": round(narrow_width - credit, 4),
            "max_loss": round(max(wide_width, narrow_width) - credit, 4),
            "legs": legs,
        },
    }


def _spread_pct(quote: dict) -> float | None:
    if quote.get("mid") in (None, 0):
        return None
    return (quote["ask"] - quote["bid"]) / quote["mid"]


def bwb_metrics(
    *, body_mid: float, near_mid: float, far_mid: float, body_strike: float, near_strike: float, far_strike: float
) -> dict:
    """The base structure's worksheet, computed once and stored as MEASURES. `credit` is what the
    structure pays at mid: sell 2x body, buy 1x near, buy 1x far."""
    credit = 2 * body_mid - near_mid - far_mid
    narrow_width = near_strike - body_strike
    wide_width = body_strike - far_strike
    max_loss_up = round(narrow_width - credit, 4)  # spot rallies through near wing
    max_loss_down = round(wide_width - credit, 4)  # spot crashes through far wing
    return {
        "body_strike": body_strike,
        "body_mid": round(body_mid, 4),
        "near_strike": near_strike,
        "near_mid": round(near_mid, 4),
        "far_strike": far_strike,
        "far_mid": round(far_mid, 4),
        "credit": round(credit, 4),
        "narrow_width": round(narrow_width, 4),
        "wide_width": round(wide_width, 4),
        "max_loss_up": max_loss_up,
        "max_loss_down": max_loss_down,
        "max_loss": max(max_loss_up, max_loss_down),
    }


# --------------------------------------------------------------------------- the add-on vertical
def plan_addon(snapshot: dict, far_strike: float, params: dict) -> dict:
    """A put credit spread bracketing the far wing: SELL one increment above it, BUY one increment
    below it. Refuses `addon_not_credit` (never simply skips) when it does not price as a credit —
    the caller keeps the trigger armed on that refusal."""
    increment = params.get("strike_increment", STRIKE_INCREMENT)
    quotes = snapshot["quotes"]
    greeks = snapshot.get("greeks") or {}
    put_book = _puts(snapshot["chain"], quotes)
    listed = sorted(put_book)

    short_target = far_strike + increment
    long_target = far_strike - increment
    short_strike = _nearest_strike(listed, short_target)
    long_strike = _nearest_strike(listed, long_target)
    if short_strike is None or long_strike is None or not (long_strike < short_strike):
        return {"ok": False, "reason": "no_strikes_in_window"}

    short_e, long_e = put_book[short_strike], put_book[long_strike]
    credit = round(short_e["quote"]["mid"] - long_e["quote"]["mid"], 4)
    addon_floor = params.get("addon_credit_floor", 0.0)
    if credit <= addon_floor:
        return {"ok": False, "reason": "addon_not_credit", "detail": {"credit": credit}}

    expiration = snapshot["expiration"]
    legs = [
        _leg(
            "addon_short", "Sell to Open", short_e, short_e["quote"], greeks.get(short_e["streamer_symbol"]), expiration
        ),
        _leg(
            "addon_long", "Buy to Open", long_e, long_e["quote"], greeks.get(long_e["streamer_symbol"]), expiration
        ),
    ]
    return {
        "ok": True,
        "plan": {
            "short_strike": short_strike,
            "long_strike": long_strike,
            "credit": credit,
            "legs": legs,
        },
    }


def close_cost(items: list[dict]) -> float | None:
    """Net cost to close every leg at mid right now (buy back shorts, sell longs). Each item is
    `{"action": ..., "mid": ...}` (a leg row merged with its current quote). None if any leg's mid
    is unpriced."""
    total = 0.0
    for it in items:
        if it.get("mid") is None:
            return None
        sign = -1 if it.get("action") == "Sell to Open" else 1
        total += sign * it["mid"]
    return round(total, 4)


def settle_intrinsic(strike: float, spot: float, option_type: str = "put") -> float:
    """One leg's intrinsic value at cash settlement. Defaulting to put kept every existing call
    site meaning what it always meant; the wall book's call legs pass their own type."""
    if option_type == "call":
        return round(max(0.0, spot - strike), 4)
    return round(max(0.0, strike - spot), 4)


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
def _slippage_dollars(leg_quotes: list[dict], config: dict) -> float:
    """The suite's slippage model (12.5% of each leg's spread, capped at 15% of its mid)."""
    costs = {**_fees.DEFAULT_COSTS, **(config.get("tastytrade_costs") or {})}
    frac = costs["slippage_frac_of_spread"]
    cap = costs.get("slippage_cap_frac_of_mid")
    total = 0.0
    for q in leg_quotes:
        bid, ask = q.get("bid", 0.0) or 0.0, q.get("ask", 0.0) or 0.0
        slip = max(ask - bid, 0.0) * frac
        if cap is not None:
            slip = min(slip, cap * max((bid + ask) / 2.0, 0.0))
        total += slip
    return round(total * 100, 2)


def entry_cost(symbol: str, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening the base BWB (4 legs, 2 sold)."""
    fee = _fees.ic_open_fee(symbol, quantity, legs=4, sell_legs=2, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, config) * quantity
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def addon_entry_cost(symbol: str, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening the add-on vertical (2 legs, 1 sold)."""
    fee = _fees.ic_open_fee(symbol, quantity, legs=2, sell_legs=1, ndigits=4)
    slippage = _slippage_dollars(leg_quotes, config) * quantity
    return {"fee": round(fee, 2), "slippage": slippage, "total": round(fee + slippage, 2)}


def settlement_fee(itm_settlements: int) -> float:
    """$5 per DISTINCT ITM settlement symbol (never per contract), the next business day."""
    return _fees.ic_expire_fee(itm_settlements)
