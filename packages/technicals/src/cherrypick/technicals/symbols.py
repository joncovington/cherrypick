"""Which symbols the store holds.

Three sets, unioned: every universe CANDIDATE (not only the members -- the stage engine is scored
against the vendor's own table, whose names are mostly not liquid enough to be members), the
rotation ETFs, and the benchmarks the engines measure against.
"""

from __future__ import annotations

import json

from . import paths

# The rotation ETFs: the union of every fund the vendor's rotation section placed in a state across
# the saved Sept 21-25 editions (32), plus the three sector funds it listed in no state that week
# (XLK, XLV, XLC) -- 35, the plan's "~35".
ROTATION_ETFS = (
    # sectors
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    # industries
    "COPX", "FDN", "GDX", "HACK", "IGV", "ITA", "IYT", "JETS", "KRE", "KWEB", "PAVE", "TAN", "XHB",
    "XOP", "XRT",
    # asset classes
    "EFA", "IWM", "LQD", "PDBC", "SPY", "TIP", "TLT", "UUP", "VNQ",
)  # fmt: skip

# What the engines measure against. AOR is the asset-class benchmark (decided 2026-09-27); AGG with
# SPY gives a US-only 60/40 should that ever be wanted. SPY also stands in for SPX, which Dolt does
# not carry.
BENCHMARKS = ("SPY", "AOR", "AGG", "RSP", "QQQ", "DIA", "IWM")


def candidates() -> list[str]:
    """The universe builder's candidate list, or empty when it has not run."""
    try:
        body = json.loads(paths.universe_candidates().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted((body.get("names") or {}).keys())


def all_symbols() -> list[str]:
    return sorted({*candidates(), *ROTATION_ETFS, *BENCHMARKS})
