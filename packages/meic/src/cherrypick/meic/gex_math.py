"""Shared GEX math — thin shim over cherrypick.core.gex.

The dollar-gamma / cumulative zero-gamma math and the compute_gex orchestration now live in the shared
core, so tt.py's get_gex and dashboard.py's GEX chart use one implementation (the reason this math was
extracted in the first place: two hand-maintained copies once drifted ~75x apart). This module
re-exports the API both call sites import. See cherrypick.core.gex for the implementation and rationale.
"""

from __future__ import annotations

from cherrypick.core.gex import (
    compute_gex,
    compute_gex_profile,
    dollar_gamma,
    interpolate_zero_gamma,
)

__all__ = ["dollar_gamma", "interpolate_zero_gamma", "compute_gex", "compute_gex_profile"]
