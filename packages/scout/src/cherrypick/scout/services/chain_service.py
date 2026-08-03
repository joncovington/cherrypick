"""Option chain expirations/strikes (TTL `chain_ttl_seconds`, default 5 min) and batched option-quote
snapshots (`get_market_data_by_type`, chunked ~100 symbols/call, TTL 60 s). The builder (M4) uses
expirations to populate the leg-picker and quotes to price a leg basket; the screener (M5) reuses
expirations for DTE selection.

Live per-option greeks (delta/gamma/theta/vega) aren't wired up here -- `get_market_data_by_type`'s
`MarketData` doesn't carry them, and the SDK's option-chain call doesn't either (only historical
greeks arrive via a full Dolt `option_chain` snapshot, which is EOD, not live). `payoff.py`'s
`Leg`/`net_greeks` already treat a leg's greeks as optional, so a quote-only leg still prices and
plots correctly -- it just won't contribute to the net-greeks panel until a live greeks source exists.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .cache import async_get_or_fetch
from .session import BrokerSession

_EXPIRATIONS_BUCKET = "chain_expirations"
_QUOTES_BUCKET = "chain_quotes"
_QUOTE_CHUNK_SIZE = 100
_DEFAULT_QUOTES_TTL_SECONDS = 60.0


def _serialize_option(option: Any) -> dict:
    return {
        "symbol": option.symbol,
        "strike": float(option.strike_price),
        "expiration": option.expiration_date.isoformat(),
        "option_type": option.option_type.value,
    }


async def get_expirations(conn: sqlite3.Connection, session: BrokerSession, cfg: dict, symbol: str) -> dict:
    symbol = symbol.strip().upper()
    ttl = cfg.get("refresh", {}).get("chain_ttl_seconds", 300)

    async def _fetch() -> dict:
        from tastytrade import instruments as _instruments

        chain = await session.call(_instruments.get_option_chain, symbol)
        return {
            expiration.isoformat(): [_serialize_option(o) for o in options]
            for expiration, options in sorted(chain.items())
        }

    payload, fetched_at, stale = await async_get_or_fetch(conn, _EXPIRATIONS_BUCKET, symbol, ttl, _fetch)
    return {"ok": True, "symbol": symbol, "as_of": fetched_at, "stale": stale, "expirations": payload}


def _serialize_quote(quote: Any) -> dict:
    return {
        "bid": float(quote.bid) if quote.bid is not None else None,
        "ask": float(quote.ask) if quote.ask is not None else None,
        "mid": float(quote.mid) if quote.mid is not None else None,
        "mark": float(quote.mark) if quote.mark is not None else None,
    }


async def get_quotes(
    conn: sqlite3.Connection,
    session: BrokerSession,
    option_symbols: list[str],
    *,
    ttl: float = _DEFAULT_QUOTES_TTL_SECONDS,
    now: float | None = None,
) -> dict[str, dict]:
    """`{option_symbol: {"bid","ask","mid","mark"}}` for every requested symbol, batching every
    stale/missing symbol into ~100-per-call `get_market_data_by_type` requests (that endpoint's
    practical batch ceiling) rather than one call per symbol. A symbol whose fetch fails is simply
    absent, never an error."""
    from .cache import peek, put

    now = time.time() if now is None else now
    wanted = sorted({s.strip() for s in option_symbols if s and s.strip()})
    if not wanted:
        return {}

    result: dict[str, dict] = {}
    stale_or_missing: list[str] = []
    for sym in wanted:
        cached = peek(conn, _QUOTES_BUCKET, sym)
        if cached is not None and (now - cached[1]) < ttl:
            result[sym] = cached[0]
        else:
            stale_or_missing.append(sym)

    for i in range(0, len(stale_or_missing), _QUOTE_CHUNK_SIZE):
        chunk = stale_or_missing[i : i + _QUOTE_CHUNK_SIZE]
        try:
            from tastytrade import market_data as _market_data

            quotes = await session.call(_market_data.get_market_data_by_type, options=chunk)
        except Exception:
            continue
        for quote in quotes:
            payload = _serialize_quote(quote)
            put(conn, _QUOTES_BUCKET, quote.symbol, payload, now)
            result[quote.symbol] = payload

    return result
