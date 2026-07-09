"""Cost-adjusted paper fills: models tastytrade's actual commission schedule
plus a fill-quality (slippage) haircut, so paper P&L isn't the classic
mid-price/zero-cost optimism bias -- worst for options, where spreads are
wide and commissions/contract add up (see docs/paper-trading-profiles.md
and the strategy-testing plan for the research behind this).

Source: tastytrade.com/pricing and the "Commissions & Fees" doc (last
updated 2026-04-06). tastytrade's signature model is *open-only*: $1/contract
to open, $0 to close, capped at $10/leg -- not the more common
charge-both-sides model. Pass-through exchange/clearing/regulatory fees
(ORF, FINRA TAF, SEC) apply on both open and close regardless. These
pass-through rates change periodically; re-check the source doc and update
config.json's "tastytrade_costs" block rather than hardcoding here.

Slippage is a separate, non-tastytrade concept: a haircut off the mid price
representing realistic fill quality (you rarely fill at the exact midpoint
of a wide options market), modeled as a configurable fraction of each leg's
bid-ask width.

Commission math note: `quantity` here is the actual contract count for the
whole order (from sizing.compute_position_size), not the per-leg template
quantity in a get_order result's `order["legs"]` (which is always 1 -- one
spread's worth of legs, scaled by `quantity` contracts).
"""

DEFAULT_COSTS = {
    "commission_open_per_contract": 1.00,
    "commission_close_per_contract": 0.00,
    "commission_cap_per_leg": 10.00,
    "clearing_fee_per_contract": 0.10,
    "regulatory_fee_per_contract": 0.04,
    "slippage_frac_of_spread": 0.25,
}


def _costs_config(config: dict) -> dict:
    return {**DEFAULT_COSTS, **config.get("tastytrade_costs", {})}


def _leg_count(order: dict) -> int:
    legs = order.get("order", {}).get("legs", [])
    return len(legs)


def _commission(num_legs: int, quantity: int, per_contract: float, cap_per_leg: float) -> float:
    """Open-only model: cost is min(quantity * per_contract, cap) for each
    leg, summed. Passing `commission_close_per_contract` (0.00 by default)
    naturally yields $0 to close without special-casing."""
    return num_legs * min(quantity * per_contract, cap_per_leg)


def _pass_through(num_legs: int, quantity: int, clearing: float, regulatory: float) -> float:
    return num_legs * quantity * (clearing + regulatory)


def _slippage(leg_quotes: list[dict], quantity: int, frac_of_spread: float) -> float:
    total_spread = sum(max(q.get("ask", 0.0) - q.get("bid", 0.0), 0.0) for q in leg_quotes)
    return total_spread * frac_of_spread * 100 * quantity


def apply_entry_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening `order` at `quantity` contracts, given `leg_quotes`
    (one {"bid","ask"} dict per leg, same order as order["order"]["legs"]).
    Returns {"commission", "pass_through_fees", "slippage", "total_cost"},
    each rounded to cents."""
    costs_cfg = _costs_config(config)
    num_legs = _leg_count(order)
    commission = _commission(
        num_legs, quantity,
        costs_cfg["commission_open_per_contract"], costs_cfg["commission_cap_per_leg"],
    )
    pass_through = _pass_through(
        num_legs, quantity,
        costs_cfg["clearing_fee_per_contract"], costs_cfg["regulatory_fee_per_contract"],
    )
    slippage = _slippage(leg_quotes, quantity, costs_cfg["slippage_frac_of_spread"])
    total = commission + pass_through + slippage
    return {
        "commission": round(commission, 2),
        "pass_through_fees": round(pass_through, 2),
        "slippage": round(slippage, 2),
        "total_cost": round(total, 2),
    }


def apply_exit_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of closing `order` at `quantity` contracts. Same shape as
    apply_entry_costs; commission uses commission_close_per_contract (0.00
    by tastytrade's open-only default, but kept as a real computation, not
    hardcoded to zero, in case a broker/schedule config ever charges to
    close)."""
    costs_cfg = _costs_config(config)
    num_legs = _leg_count(order)
    commission = _commission(
        num_legs, quantity,
        costs_cfg["commission_close_per_contract"], costs_cfg["commission_cap_per_leg"],
    )
    pass_through = _pass_through(
        num_legs, quantity,
        costs_cfg["clearing_fee_per_contract"], costs_cfg["regulatory_fee_per_contract"],
    )
    slippage = _slippage(leg_quotes, quantity, costs_cfg["slippage_frac_of_spread"])
    total = commission + pass_through + slippage
    return {
        "commission": round(commission, 2),
        "pass_through_fees": round(pass_through, 2),
        "slippage": round(slippage, 2),
        "total_cost": round(total, 2),
    }
