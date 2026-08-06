"""Equity-quote snapshots for the SSE quote poller (`sse.py`). **Streamer before API calls**: every
symbol is first looked up in the suite's shared stream cache (`services/streamcache.py` --
`~/.cherrypick/data/marketdata/stream_cache.db`, populated by the standalone streamer daemon when
it's running); only symbols missing there, or whose row is older than
`refresh.stream_cache_max_age_seconds`, fall back to a direct broker call. The fallback batches every
such symbol into ~100-per-call `get_market_data_by_type` requests rather than one call per symbol,
same discipline as `chain_service.get_quotes`. No TTL cache layer of scout's own here -- `sse.py`'s
`QuotePoller` is itself the rate limiter (one call per tick, only while a client is connected), so a
second cache on top would just be dead weight.
"""

from __future__ import annotations

from typing import Any

from . import streamcache as _streamcache
from .session import BrokerSession

_CHUNK_SIZE = 100
_DEFAULT_STREAM_CACHE_MAX_AGE_SECONDS = 10.0
_UNSET = object()  # distinguishes "no stream_cache_conn given" (open fresh) from an explicit None


def _serialize_quote(quote: Any) -> dict:
    last = float(quote.last) if quote.last is not None else None
    prev_close = float(quote.prev_close) if quote.prev_close is not None else None
    change_pct = (last - prev_close) / prev_close if last is not None and prev_close else None
    return {
        "bid": float(quote.bid) if quote.bid is not None else None,
        "ask": float(quote.ask) if quote.ask is not None else None,
        "mid": float(quote.mid) if quote.mid is not None else None,
        "mark": float(quote.mark) if quote.mark is not None else None,
        "last": last,
        "change_pct": change_pct,
    }


async def get_quotes(
    broker_session: BrokerSession,
    symbols: list[str],
    *,
    stream_cache_max_age_seconds: float = _DEFAULT_STREAM_CACHE_MAX_AGE_SECONDS,
    stream_cache_conn: Any = _UNSET,
) -> dict[str, dict]:
    """`{symbol: {"bid","ask","mid","mark","last","change_pct"}}` for every requested equity symbol.
    A symbol whose fetch fails, or a whole chunk that errors, is simply absent from the result --
    the poller keeps ticking with whatever quotes it got rather than failing the tick.

    Checks the shared stream cache first (see the module docstring); `stream_cache_conn` is
    injectable for tests (pass `None` to force the all-REST path a stream-cache-less environment
    takes) and otherwise opens fresh each call -- cheap for a small read-only WAL file, and correct
    even if the streamer daemon starts or stops between polls."""
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not wanted:
        return {}

    opened_here = stream_cache_conn is _UNSET
    conn = _streamcache.open_ro() if opened_here else stream_cache_conn
    result: dict[str, dict] = {}
    if conn is not None:
        try:
            result.update(_streamcache.read_equity_quotes(conn, wanted, stream_cache_max_age_seconds))
        finally:
            if opened_here:
                conn.close()

    wanted = [s for s in wanted if s not in result]
    for i in range(0, len(wanted), _CHUNK_SIZE):
        chunk = wanted[i : i + _CHUNK_SIZE]
        try:
            from tastytrade import market_data as _market_data

            quotes = await broker_session.call(_market_data.get_market_data_by_type, equities=chunk)
        except Exception:
            continue
        for quote in quotes:
            result[quote.symbol] = _serialize_quote(quote)
    return result
