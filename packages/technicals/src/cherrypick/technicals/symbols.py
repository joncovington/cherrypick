"""Which symbols the store holds.

Five sets, unioned: every universe CANDIDATE (not only the members -- the stage engine is scored
against the vendor's own table, whose names are mostly not liquid enough to be members), the
rotation ETFs, the benchmarks the engines measure against, every name the vendor collector has
captured (the chart engines' answer key), and the earnings calendar's liquid names (our read on
them needs their bars).
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

# The rotation funds the vendor types "Asset" (every edition, Sept 21-25). They are measured against
# the stock-and-bond benchmark (AOR); the sector and industry funds against the S&P 500 (SPY).
ASSET_ETFS = ("EFA", "IWM", "LQD", "PDBC", "SPY", "TIP", "TLT", "UUP", "VNQ")

# What the engines measure against. AOR is the asset-class benchmark (decided 2026-09-27); AGG with
# SPY gives a US-only 60/40 should that ever be wanted. SPY also stands in for SPX in the engines:
# SPX is charted (below) but no engine measures against it.
BENCHMARKS = ("SPY", "AOR", "AGG", "RSP", "QQQ", "DIA", "IWM")


# Cash indexes, charted from the broker's daily candles (scripts/fetch_index_bars.py) because Dolt
# carries none. Charts only: an index is not a stock, so `store.stocks` keeps it out of breadth,
# stages, ranks and every scored measure, though the universe lists SPX as a candidate.
INDEXES = ("SPX",)


def candidates() -> list[str]:
    """The universe builder's candidate list, or empty when it has not run."""
    try:
        body = json.loads(paths.universe_candidates().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted((body.get("names") or {}).keys())


def captured() -> list[str]:
    """Every name the vendor collector has saved a chart capture for. They are the answer key the
    chart engines are scored against, and a capture of a name the store does not hold cannot be
    scored at all -- many scan-list names are not universe candidates."""
    root = paths.market_report_dir() / "vendor-charts"
    names = {p.stem.replace(".rejected", "") for p in root.glob("????-??-??/*.json")}
    names.discard("trade-ideas")
    return sorted(names)


def earnings_names() -> list[str]:
    """The optionable names rated very or somewhat liquid on the newest earnings calendar
    (`earnings_calendar.py`). Its direction and strength are ours, read from these bars, so a name
    the store does not hold has no read and leaves the strong-read view (2026-10-17: 252 of 590 such
    rows). The calendar runs 30 days ahead, so a name entering it lands weeks before it reports."""
    root = paths.data_dir() / "earnings-calendar"
    files = sorted(root.glob("????-??-??.json"))
    if not files:
        return []
    try:
        rows = json.loads(files[-1].read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return []
    return sorted(
        r["symbol"]
        for r in rows
        if isinstance(r.get("symbol"), str) and r.get("liquidity_rank") in (1, 2) and r.get("optionable")
    )


def all_symbols() -> list[str]:
    return sorted({*candidates(), *ROTATION_ETFS, *BENCHMARKS, *captured(), *earnings_names()})
