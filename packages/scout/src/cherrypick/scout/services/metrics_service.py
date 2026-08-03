"""``tastytrade.metrics.get_market_metrics`` — IV rank/percentile, liquidity rating, earnings date +
consensus EPS, P/E, dividends, beta. Already called by MEIC but mostly discarded there; this is the
first service in the suite to actually surface it.

One batched call per refresh: every symbol whose per-symbol cache entry is missing or past its TTL is
refetched together in a single `get_market_metrics` call (not one call per stale symbol), and the
result is stored per-symbol so a later request for a still-fresh symbol is a pure cache hit. The
fetch function is injectable so tests never need real credentials or network access.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from .session import BrokerSession

FetchBatchFn = Callable[[BrokerSession, list[str]], Awaitable[dict[str, dict]]]

_BUCKET = "metrics"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize(info: Any) -> dict:
    """Pull the fields scout's calendar/screener actually use out of a `MarketMetricInfo`, converting
    `Decimal`/`date`/`datetime` fields to JSON-safe values (the cache stores plain JSON)."""
    earnings = getattr(info, "earnings", None)
    earnings_dict = None
    if earnings is not None:
        earnings_dict = {
            "expected_report_date": _jsonable(getattr(earnings, "expected_report_date", None)),
            "time_of_day": getattr(earnings, "time_of_day", None),
            "consensus_estimate": _jsonable(getattr(earnings, "consensus_estimate", None)),
            "actual_eps": _jsonable(getattr(earnings, "actual_eps", None)),
            "estimated": getattr(earnings, "estimated", None),
        }
    return {
        "symbol": info.symbol,
        "iv_rank": _jsonable(getattr(info, "implied_volatility_index_rank", None)),
        "iv_percentile": _jsonable(getattr(info, "implied_volatility_percentile", None)),
        "liquidity_rating": getattr(info, "liquidity_rating", None),
        "liquidity_rank": _jsonable(getattr(info, "liquidity_rank", None)),
        "beta": _jsonable(getattr(info, "beta", None)),
        "price_earnings_ratio": _jsonable(getattr(info, "price_earnings_ratio", None)),
        "dividend_yield": _jsonable(getattr(info, "dividend_yield", None)),
        "earnings": earnings_dict,
        "updated_at": _jsonable(getattr(info, "updated_at", None)),
    }


async def _default_fetch_batch(session: BrokerSession, symbols: list[str]) -> dict[str, dict]:
    from tastytrade import metrics as _metrics

    infos = await session.call(_metrics.get_market_metrics, symbols)
    return {info.symbol.upper(): _serialize(info) for info in infos}


async def get_metrics(
    conn: sqlite3.Connection,
    session: BrokerSession,
    symbols: list[str],
    ttl: float,
    *,
    fetch_batch_fn: FetchBatchFn = _default_fetch_batch,
    now: float | None = None,
) -> dict[str, dict]:
    """Return ``{symbol: metrics_dict}`` for every requested symbol. Symbols whose broker fetch fails
    (no credentials, a rate limit, a network hiccup) are simply absent from the result -- calendar_service
    treats a missing symbol as "no metrics available", never an error."""
    from .cache import peek, put

    now = time.time() if now is None else now
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not wanted:
        return {}

    result: dict[str, dict] = {}
    stale_or_missing: list[str] = []
    for sym in wanted:
        cached = peek(conn, _BUCKET, sym)
        if cached is not None and (now - cached[1]) < ttl:
            result[sym] = cached[0]
        else:
            stale_or_missing.append(sym)

    if stale_or_missing:
        try:
            fresh = await fetch_batch_fn(session, stale_or_missing)
        except Exception:
            fresh = {}
        for sym in stale_or_missing:
            payload = fresh.get(sym)
            if payload is not None:
                put(conn, _BUCKET, sym, payload, now)
                result[sym] = payload

    return result
