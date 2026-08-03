"""Batched equity-quote snapshots for the SSE quote poller (`sse.py`). Same batching discipline as
`chain_service.get_quotes`: every requested symbol chunked into ~100-per-call
`get_market_data_by_type` requests rather than one call per symbol. No cache/TTL layer here --
`sse.py`'s `QuotePoller` is itself the rate limiter (one call per tick, only while a client is
connected), so a second cache on top would just be dead weight.
"""

from __future__ import annotations

from typing import Any

from .session import BrokerSession

_CHUNK_SIZE = 100


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


async def get_quotes(broker_session: BrokerSession, symbols: list[str]) -> dict[str, dict]:
    """`{symbol: {"bid","ask","mid","mark","last","change_pct"}}` for every requested equity symbol.
    A symbol whose fetch fails, or a whole chunk that errors, is simply absent from the result --
    the poller keeps ticking with whatever quotes it got rather than failing the tick."""
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not wanted:
        return {}

    result: dict[str, dict] = {}
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
