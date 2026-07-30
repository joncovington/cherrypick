"""Live order construction for the flies scaffold — pure translation, no submission.

Turns the engine's decisions (the same `plan` dicts the paper book records) into
`cherrypick.core.broker.build_order` specs, using the OCC symbols the provider now carries
on every leg quote. Nothing here talks to a broker, a DB, or a file — `live_loop.py` owns
submission (through core.broker's preflight + governor) and `broker_cli.py` owns the CLI.

Three orders exist in this strategy (live v1 is legged-only — see docs/live-trading-plan.md):

  entry_spec       step 1 — sell the opening credit spread (STO short strike, BTO wing)
  completion_spec  step 2 — buy the completing vertical (BTO far strike, STO the centre
                   AGAIN, doubling it into the fly's -2), as a working Day limit
  close_fly_spec   step 3, conditional — close a completed fly ahead of expiry when it has an
                   ITM leg and doing so is cheaper than the exercise-assignment fee it would
                   otherwise incur (STC both wings, BTC the doubled centre); see
                   `engine.evaluate_pre_close_exit`. The one deliberate exception to rule 5's
                   "no adjustments, hold to settlement" — a cost-avoidance mechanism, not a
                   strategy adjustment.

Prices are floored/ceilinged to the option tick in the direction that favors us: a credit
asks for slightly less, a debit offers slightly less — conservative, and the exchange can't
reject the increment.
"""

from __future__ import annotations

import fly
from engine import PUT

TICK = 0.05  # SPX/XSP index options tick in nickels at these price levels


def tick_floor(price: float, tick: float = TICK) -> float:
    """Round DOWN to the tick (asking for less credit / offering less debit)."""
    return int(round(price * 100)) // int(round(tick * 100)) * int(round(tick * 100)) / 100.0


def _leg_quote(snapshot: dict, side: str, strike: float) -> dict:
    from engine import quote

    q = quote(snapshot, side, strike)
    if q is None:
        raise ValueError(f"no quote for {side} {strike}")
    if not q.get("occ_symbol"):
        raise ValueError(f"quote for {side} {strike} carries no OCC symbol (stale cache rows?)")
    return q


def _leg(q: dict, action: str, quantity: int) -> dict:
    return {
        "instrument_type": q.get("instrument_type") or "Equity Option",
        "symbol": q["occ_symbol"],
        "action": action,
        "quantity": quantity,
    }


def entry_spec(snapshot: dict, plan: dict) -> dict:
    """Step 1: the opening credit spread from an admitted `evaluate_credit_spread_entry` plan.
    Sell to open the centre (the short strike), buy to open the wing; Day limit at the plan's
    modeled credit floored to the tick."""
    side, center, width = plan["side"], plan["center"], plan["wing_width"]
    long_strike = center - width if side == PUT else center + width
    qty = plan.get("quantity", 1)
    price = tick_floor(plan["credit"])
    if price <= 0:
        raise ValueError(f"entry credit {plan['credit']!r} floors to nothing submittable")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "credit",
        "legs": [
            _leg(_leg_quote(snapshot, side, center), "sell to open", qty),
            _leg(_leg_quote(snapshot, side, long_strike), "buy to open", qty),
        ],
    }


def entry_fresh_reprice(
    spec: dict, fresh: dict, slippage_frac: float | None = None
) -> tuple[float | None, dict]:
    """Recompute the entry credit from fresh REST quotes (the shape `broker_cli.fresh_option_quotes`
    returns, keyed by OCC symbol) using the SAME mid-minus-slippage convention `fly.vertical_credit`
    already applies to cached quotes — so the fresh number is apples-to-apples with `plan["credit"]`,
    not a different pricing model. `spec` must be one built by `entry_spec` (legs[0] = short/centre,
    legs[1] = long/wing).

    Returns `(new_price, info)`. `new_price` is the fresh credit floored to the tick, or `None` when
    either leg is missing from `fresh` (a failed fetch or an unusable REST result) — the caller MUST
    skip the entry rather than fall back to the stale cached price for a live order. `info` always
    carries enough to log: `fresh_credit` when computable, or `reason`/`missing` when not."""
    slip = fly.DEFAULT_SLIPPAGE_FRAC if slippage_frac is None else slippage_frac
    short_sym, long_sym = spec["legs"][0]["symbol"], spec["legs"][1]["symbol"]
    short_q, long_q = fresh.get(short_sym), fresh.get(long_sym)
    if short_q is None or long_q is None:
        missing = [s for s, q in ((short_sym, short_q), (long_sym, long_q)) if q is None]
        return None, {"reason": "fresh_quote_missing", "missing": missing}
    fresh_credit = fly.vertical_credit(short_q, long_q, slip)
    return tick_floor(fresh_credit), {"fresh_credit": round(fresh_credit, 4)}


def completion_spec(snapshot: dict, position: dict, plan: dict) -> dict:
    """Step 2: the completing vertical from an admitted `evaluate_completion` plan. Buy to
    open the far strike, sell to open the centre again (the fly's -2). Day limit at the
    plan's priced debit, floored to the tick (offering less), and never above the engine's
    own gate (`gate_debit = credit - fee_buffer`) — the working order must not be able to
    fill at a price the completion gate would have refused."""
    side, center = position["side"], position["center"]
    qty = position.get("quantity", 1)
    price = min(tick_floor(plan["debit"]), tick_floor(plan["gate_debit"]))
    if price <= 0:
        raise ValueError(f"completion debit {plan['debit']!r} floors to nothing submittable")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "debit",
        "legs": [
            _leg(_leg_quote(snapshot, side, plan["long_strike"]), "buy to open", qty),
            _leg(_leg_quote(snapshot, side, center), "sell to open", qty),
        ],
    }


def completing_long_strike(position: dict) -> float:
    """The completing vertical's far strike, from the position row alone. A put spread sold at
    centre C with wing W needs +1 at C+W to finish the fly (C-W is already the entry's long leg);
    a call spread mirrors it at C-W."""
    center, width = position["center"], position["wing_width"]
    return center + width if position["side"] == PUT else center - width


def max_safe_completion_debit(position: dict, min_floor_dollars: float, fee_buffer: float) -> float:
    """The highest debit the resting completion order may pay without violating EITHER gate.

    Two independent gates bound the price:
      - fee_buffer (points):      debit <= credit - fee_buffer
      - min_floor_dollars ($):    fly.position_floor(the resulting fly) >= min_floor
    The engine checks both per-tick in paper; a resting order priced only at the buffer gate
    could fill into a fly whose floor is below the dollar gate, so the bound must be the min of
    the two. `fees_after_completion` = the fees already recorded on the row (the entry stack)
    plus the completing vertical's own stack. Deliberately NOT reserving the resulting fly's
    worst-case exercise-assignment fee here (unlike an uncompleted vertical) — `fly.position_floor`
    no longer does either, since `engine.evaluate_pre_close_exit` bounds a fly's realistic
    ITM-assignment cost going forward; see that function's docstring for the full reasoning and
    the one tail risk (the closing order itself failing) this deliberately does not reserve for.
    """
    credit = position["net"]
    qty = position.get("quantity", 1)
    fees_after = (position.get("fees") or 0.0) + fly.vertical_open_fee(position["symbol"], qty)
    floor_bound = credit - (min_floor_dollars + fees_after) / (fly.CONTRACT_MULTIPLIER * qty)
    return min(credit - fee_buffer, floor_bound)


def resting_completion_spec(snapshot: dict, position: dict, params: dict) -> dict:
    """The RESTING completion order: legs fully determined by the position row, priced once at
    the max safe debit — the working limit IS the completion gate, so it can sit at the broker
    all session catching every transient dip a discrete poll would miss. No engine evaluation is
    needed or consulted; only the OCC symbols come from the snapshot's quotes."""
    side, center = position["side"], position["center"]
    qty = position.get("quantity", 1)
    bound = max_safe_completion_debit(
        position, params.get("min_floor_dollars", 0.0), params.get("fee_buffer", 0.10)
    )
    price = tick_floor(bound)
    if price <= 0:
        raise ValueError(
            f"max safe completion debit {bound!r} floors to nothing submittable "
            f"(credit {position['net']!r} too small against fees/floor)"
        )
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "debit",
        "legs": [
            _leg(_leg_quote(snapshot, side, completing_long_strike(position)), "buy to open", qty),
            _leg(_leg_quote(snapshot, side, center), "sell to open", qty),
        ],
    }


def close_fly_spec(snapshot: dict, position: dict, plan: dict) -> dict:
    """Close a completed fly ahead of expiry, from an admitted `evaluate_pre_close_exit` plan:
    sell both wings, buy back the doubled centre. Day limit at the plan's priced close credit,
    floored to the tick (asking for slightly less, so it can still cross a moving market in the
    closing minutes)."""
    side, center, width = position["side"], position["center"], position["wing_width"]
    qty = position.get("quantity", 1)
    price = tick_floor(plan["close_credit"])
    if price <= 0:
        raise ValueError(f"close credit {plan['close_credit']!r} floors to nothing submittable")
    return {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": price,
        "price_effect": "credit",
        "legs": [
            _leg(_leg_quote(snapshot, side, center - width), "sell to close", qty),
            _leg(_leg_quote(snapshot, side, center), "buy to close", qty * 2),
            _leg(_leg_quote(snapshot, side, center + width), "sell to close", qty),
        ],
    }
