"""Cost-adjusted paper fills — thin shim over cherrypick.core.fees.

The tastytrade cost model (open-only commission, clearing/regulatory pass-throughs, and the slippage
haircut off each leg's bid-ask width) now lives in the shared core so every suite module uses the same
math. This module re-exports the API existing call sites import (strat_test_harness, tests). See
cherrypick.core.fees for the implementation, source, and rationale.

One earnings-specific rule sits on top: `natural_exit_costs`. Exits are priced on the natural side
(`scanner.compute_generic_exit_debit`: shorts bought at the ask, longs sold at the bid), which already
concedes half of every leg's spread inside gross. Charging the core's slippage haircut on top took the
spread twice; until 2026-10-23 every exit did. The entry side is priced at mid, so its haircut stays.
"""

from __future__ import annotations

from cherrypick.core.fees import (
    DEFAULT_COSTS,
    apply_entry_costs,
    apply_exit_costs,
)

__all__ = ["DEFAULT_COSTS", "apply_entry_costs", "apply_exit_costs", "natural_exit_costs"]


def natural_exit_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """`apply_exit_costs` for a fill already priced at the natural side: fees only, slippage 0.

    The fill concedes the spread in gross, so the haircut is not a cost here; total_cost is
    the stack less its haircut, and the returned `slippage` is 0 rather than absent so the
    ledger records "none charged", not "not recorded"."""
    out = apply_exit_costs(order, leg_quotes, quantity, config)
    return {**out, "slippage": 0.0, "total_cost": round(out["total_cost"] - out["slippage"], 2)}
