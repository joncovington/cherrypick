"""Symbol membership in tastytrade's own "Liquid Symbols" public watchlist -- the same
`PublicWatchlist` source `sector_service` reads, one different group/name: `group_name ==
"Liquidity"`, `name == "Liquid Symbols"` (live-verified 2026-08-06: 198 symbols). Used to
pre-filter the earnings calendar to liquid names, including the broad Dolt-sourced rows that
carry no `liquidity_rating` of their own (that field only exists on watchlist/metrics rows) --
this is a zero-per-symbol-broker-call way to grade liquidity for a name scout has never looked up
metrics for, the same problem `sector_service` solved for sector membership.

Cached as one blob via `cache.async_get_or_fetch` (the same shape `metrics_service.
get_risk_free_rate` uses for a single cached value) rather than `symbol_meta` -- that table's
columns (sector/industry) don't fit a flat liquid/not-liquid set, and a single JSON list is the
natural shape here.
"""

from __future__ import annotations

import sqlite3

from .cache import async_get_or_fetch
from .session import BrokerSession

_BUCKET = "liquidity"
_KEY = "liquid_symbols"
_DEFAULT_TTL_SECONDS = 86400.0  # a day -- this watchlist's membership doesn't move within a session


async def _default_fetch(session: BrokerSession) -> list[str]:
    from tastytrade.watchlists import PublicWatchlist

    watchlists = await session.call(PublicWatchlist.get)
    wl = next((w for w in watchlists if w.group_name == "Liquidity" and w.name == "Liquid Symbols"), None)
    if wl is None or not wl.watchlist_entries:
        return []
    return sorted({entry["symbol"].upper() for entry in wl.watchlist_entries if entry.get("symbol")})


async def get_liquid_symbols(
    conn: sqlite3.Connection,
    session: BrokerSession,
    *,
    ttl: float = _DEFAULT_TTL_SECONDS,
    fetch_fn=_default_fetch,
    now: float | None = None,
) -> set[str]:
    """The current "Liquid Symbols" membership. `async_get_or_fetch` already degrades a fetch
    failure to the last-cached (however stale) payload, or propagates if nothing is cached yet --
    caught here and turned into an empty set, since a symbol scout has no answer for must read as
    "couldn't determine liquidity", never as "no symbol is liquid" (which would filter the whole
    calendar to nothing on one broker hiccup)."""

    async def _fetch() -> list[str]:
        return await fetch_fn(session)

    try:
        symbols, _fetched_at, _stale = await async_get_or_fetch(conn, _BUCKET, _KEY, ttl, _fetch, now=now)
    except Exception:
        return set()
    return set(symbols or [])
