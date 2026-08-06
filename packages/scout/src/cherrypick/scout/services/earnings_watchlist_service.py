"""Symbol membership in tastytrade's own "All Earnings" public watchlist -- the same
`PublicWatchlist` source `sector_service`/`liquidity_service` read, group `"Earnings"`
(live-verified 2026-08-06: two watchlists there, "All Earnings" at 85 symbols and the smaller
curated "tasty Earnings" at 26; this reads the broader one, since the calendar's whole point is
catching a name the user hasn't added to their own watchlist yet).

This exists because the module docstring's original premise for using Dolt as the calendar's
broad-coverage source -- "a third-party source is acceptable only where tastytrade has no
equivalent" -- turned out to be wrong: tastytrade DOES publish a broad earnings watchlist. This
symbol set feeds `calendar_service.get_calendar`'s metrics call so those symbols get real dates
from `metrics_service.get_metrics` (the same live, per-symbol-accurate source the user's own
watchlist already uses) instead of Dolt's periodic third-party snapshot. Dolt remains the
fallback for the genuine long tail beyond even this broader tastytrade list.
"""

from __future__ import annotations

import sqlite3

from .cache import async_get_or_fetch
from .session import BrokerSession

_BUCKET = "earnings_watchlist"
_KEY = "symbols"
_DEFAULT_TTL_SECONDS = 86400.0  # a day -- this watchlist's membership doesn't move within a session
_WATCHLIST_NAME = "All Earnings"


async def _default_fetch(session: BrokerSession) -> list[str]:
    from tastytrade.watchlists import PublicWatchlist

    watchlists = await session.call(PublicWatchlist.get)
    wl = next((w for w in watchlists if w.group_name == "Earnings" and w.name == _WATCHLIST_NAME), None)
    if wl is None or not wl.watchlist_entries:
        return []
    return sorted({entry["symbol"].upper() for entry in wl.watchlist_entries if entry.get("symbol")})


async def get_earnings_watchlist_symbols(
    conn: sqlite3.Connection,
    session: BrokerSession,
    *,
    ttl: float = _DEFAULT_TTL_SECONDS,
    fetch_fn=_default_fetch,
    now: float | None = None,
) -> set[str]:
    """The current "All Earnings" membership, or an empty set on a fetch failure with nothing
    cached yet -- an empty result means "couldn't reach tastytrade's list", so the caller falls
    back to Dolt/the user's own watchlist rather than silently narrowing the calendar."""

    async def _fetch() -> list[str]:
        return await fetch_fn(session)

    try:
        symbols, _fetched_at, _stale = await async_get_or_fetch(conn, _BUCKET, _KEY, ttl, _fetch, now=now)
    except Exception:
        return set()
    return set(symbols or [])
