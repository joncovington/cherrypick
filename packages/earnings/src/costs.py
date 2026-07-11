"""Cost-adjusted paper fills — thin shim over cherrypick.core.fees (src/_core submodule).

The tastytrade cost model (open-only commission, clearing/regulatory pass-throughs, and the slippage
haircut off each leg's bid-ask width) now lives in the shared core so every suite module uses the same
math. This module re-exports the API existing call sites import (strategy_test_runner, tests). See
cherrypick.core.fees for the implementation, source, and rationale.
"""

from __future__ import annotations

# Make the cherrypick-core submodule importable without an install (mirrors credentials.py).
import sys as _sys
from pathlib import Path as _Path

_CORE = _Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in _sys.path:
    _sys.path.insert(0, str(_CORE))

from cherrypick.core.fees import (  # noqa: E402
    DEFAULT_COSTS,
    apply_entry_costs,
    apply_exit_costs,
)

__all__ = ["DEFAULT_COSTS", "apply_entry_costs", "apply_exit_costs"]
