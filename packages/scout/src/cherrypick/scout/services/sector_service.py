"""Symbol -> GICS-style sector, from tastytrade's own public watchlists -- not a third-party
source, and not a symbol-by-symbol lookup: ``PublicWatchlist.get(session)`` (one call) returns
every public watchlist tastytrade publishes; filtering ``group_name == "Sectors"`` gives the
eleven standard sector groupings (Technology, Healthcare, Energy, ...), each carrying its member
symbols. Stored in `services/cache.py`'s ``symbol_meta`` table -- reserved since M3 for exactly
this (``symbol``/``sector``/``industry``/``source``/``fetched_at`` columns) and unused until now,
rather than reinventing per-symbol storage in the generic ``kv_cache`` blob table. Every row is
written together from one bulk fetch, so `cache.symbol_meta_freshness` (a table-wide MAX, not a
per-symbol TTL) is the right staleness check.
"""

from __future__ import annotations

import sqlite3
import time

from .cache import read_sector_map, symbol_meta_freshness, write_sector_map
from .session import BrokerSession

_SOURCE = "tastytrade_public_watchlist"
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
    "missing can't prove membership" posture `_cap_bucket` already follows for market cap.

    On a fetch failure: the existing table contents are returned (however stale) rather than
    raising, as long as any rows exist -- a symbol_meta table is shared across every caller of
    this function, and one broker hiccup shouldn't blank the Sector chip for the rest of the
    session. Only an empty table on a failed first-ever fetch propagates the exception."""
    now = time.time() if now is None else now
    fetched_at = symbol_meta_freshness(conn)
    if fetched_at is not None and (now - fetched_at) < ttl:
        return read_sector_map(conn)

    try:
        fresh = await fetch_fn(session)
    except Exception:
        if fetched_at is not None:
            return read_sector_map(conn)
        raise
    write_sector_map(conn, fresh, now, _SOURCE)
    return fresh
