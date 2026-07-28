"""Live order construction for the flies scaffold — pure translation, no submission.

Turns the engine's decisions (the same `plan` dicts the paper book records) into
`cherrypick.core.broker.build_order` specs, using the OCC symbols the provider now carries
on every leg quote. Nothing here talks to a broker, a DB, or a file — `live_loop.py` owns
submission (through core.broker's preflight + governor) and `broker_cli.py` owns the CLI.

Two orders exist in this strategy, and only two (live v1 is legged-only — see
docs/live-trading-plan.md):

  entry_spec       step 1 — sell the opening credit spread (STO short strike, BTO wing)
  completion_spec  step 2 — buy the completing vertical (BTO far strike, STO the centre
                   AGAIN, doubling it into the fly's -2), as a working Day limit

Prices are floored/ceilinged to the option tick in the direction that favors us: a credit
asks for slightly less, a debit offers slightly less — conservative, and the exchange can't
reject the increment.
"""

from __future__ import annotations

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
