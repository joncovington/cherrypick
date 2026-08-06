"""Live order construction for the flies scaffold — pure translation, no submission.

Turns the engine's decisions (the same `plan` dicts the paper book records) into
`cherrypick.core.broker.build_order` specs, using the OCC symbols the provider now carries
on every leg quote. Nothing here talks to a broker, a DB, or a file — `live_loop.py` owns
submission (through core.broker's preflight + governor) and `broker_cli.py` owns the CLI.

Two orders exist in this strategy (live v1 is legged-only — see docs/live-trading-plan.md):

  entry_spec       step 1 — sell the opening credit spread (STO short strike, BTO wing)
  completion_spec  step 2 — buy the completing vertical (BTO far strike, STO the centre
                   AGAIN, doubling it into the fly's -2), as a working Day limit

There is no closing order: every position holds to cash settlement. A third, conditional
`close_fly_spec`/`close_vertical_spec` pair existed until 2026-08-01 to close ITM positions in
the final minutes when that beat the assignment fee; it was removed after measuring a
~$34/position loss in paper and zero fires in live (CLAUDE.md rule 5).

Prices are floored/ceilinged to the option tick in the direction that favors us: a credit
asks for slightly less, a debit offers slightly less — conservative, and the exchange can't
reject the increment.
"""

from __future__ import annotations

from cherrypick.flies import fly
from cherrypick.flies.engine import PUT

TICK = 0.05  # SPX/XSP index options tick in nickels at these price levels


def tick_floor(price: float, tick: float = TICK) -> float:
    """Round DOWN to the tick (asking for less credit / offering less debit)."""
    return int(round(price * 100)) // int(round(tick * 100)) * int(round(tick * 100)) / 100.0


def _leg_quote(snapshot: dict, side: str, strike: float) -> dict:
    from cherrypick.flies.engine import quote

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
    modeled credit floored to the tick.

    Live v1 is legged-only (docs/live-trading-plan.md) -- `debit_first`/`bwb_roll` are paper-only
    research arms with no live order builders of their own, and `live_loop.py` never calls their
    evaluate_* functions. This explicit shape check is the second, independent guard: a plan from
    either paper-only mode carries a different key set (`debit` or `far_width`, never `credit`
    alone) and is refused here with a clear error rather than a confusing KeyError two lines down.
    """
    if "credit" not in plan or "far_width" in plan:
        raise ValueError("entry_spec is legged-only -- refusing a non-legged plan for live trading")
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
    plus the completing vertical's own stack, PLUS the worst-case exercise-assignment fee the
    resulting fly would owe — `fly.position_floor` reserves that (since 2026-08-01, when the
    pre-close ITM exit that used to bound it was removed), so this bound must reserve exactly the
    same amount or a resting order could fill into a fly the floor gate would have refused.
    `tests/test_live_scaffold.py` pins the two against each other.
    """
    credit = position["net"]
    qty = position.get("quantity", 1)
    fees_after = (position.get("fees") or 0.0) + fly.vertical_open_fee(position["symbol"], qty)
    reserve = fly.expire_fee(fly.WORST_CASE_ITM_LEGS["fly"])
    floor_bound = credit - (min_floor_dollars + fees_after + reserve) / (fly.CONTRACT_MULTIPLIER * qty)
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
