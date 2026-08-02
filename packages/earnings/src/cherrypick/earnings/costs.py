"""Cost-adjusted paper fills — thin shim over cherrypick.core.fees.

The tastytrade cost model (open-only commission, clearing/regulatory pass-throughs, and the slippage
haircut off each leg's bid-ask width) now lives in the shared core so every suite module uses the same
math. This module re-exports the API existing call sites import (strategy_test_runner, tests). See
cherrypick.core.fees for the implementation, source, and rationale.
"""

from __future__ import annotations

from cherrypick.core.fees import (
    DEFAULT_COSTS,
    apply_entry_costs,
    apply_exit_costs,
)

__all__ = ["DEFAULT_COSTS", "apply_entry_costs", "apply_exit_costs"]
