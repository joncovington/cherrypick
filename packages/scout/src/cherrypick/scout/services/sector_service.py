"""Symbol -> GICS-style sector, from tastytrade's own public watchlists -- not a third-party
source, and not a symbol-by-symbol lookup: ``PublicWatchlist.get(session)`` (one call) returns
every public watchlist tastytrade publishes; filtering ``group_name == "Sectors"`` gives the
eleven standard sector groupings (Technology, Healthcare, Energy, ...), each carrying its member
symbols. Cached as one blob (`get_risk_free_rate`'s shape, not `metrics_service`'s per-symbol
one) since sector membership is a single fetch regardless of how many symbols the caller asks
about, and changes on the order of index-rebalance events, not per request.
"""

from __future__ import annotations

import sqlite3
import time

from .cache import async_get_or_fetch
from .session import BrokerSession

_BUCKET = "sectors"
_KEY = "map"
_DEFAULT_TTL_SECONDS = 86400.0  # a day -- sector membership doesn't move within a session

SECTORS = (
    "Basic Materials",
    "Communication Services",
    "Consumer Defensive",
    "Consumer Discretionary",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)


async def _default_fetch(session: BrokerSession) -> dict[str, str]:
    from tastytrade.watchlists import PublicWatchlist

    watchlists = await session.call(PublicWatchlist.get)
    symbol_to_sector: dict[str, str] = {}
    for wl in watchlists:
        if wl.group_name != "Sectors" or not wl.watchlist_entries:
            continue
        for entry in wl.watchlist_entries:
            symbol = entry.get("symbol")
            if symbol:
                symbol_to_sector[symbol.upper()] = wl.name
    return symbol_to_sector


async def get_sector_map(
    conn: sqlite3.Connection,
    session: BrokerSession,
    *,
    ttl: float = _DEFAULT_TTL_SECONDS,
    fetch_fn=_default_fetch,
    now: float | None = None,
) -> dict[str, str]:
    """``{SYMBOL: sector_name}`` for every symbol tastytrade's Sectors watchlists carry. A symbol
    absent from the result (an ETF, an index, a name not in any tastytrade sector watchlist) has
    no known sector -- the screener's Sector chip excludes it while the chip is active, the same
    "missing can't prove membership" posture `_cap_bucket` already follows for market cap."""
    now = time.time() if now is None else now

    async def _fetch() -> dict[str, str]:
        return await fetch_fn(session)

    sector_map, _fetched_at, _stale = await async_get_or_fetch(conn, _BUCKET, _KEY, ttl, _fetch, now=now)
    return sector_map or {}
