"""Option chain expirations/strikes (TTL `chain_ttl_seconds`, default 5 min), batched option-quote
snapshots (`get_market_data_by_type`, chunked ~100 symbols/call, TTL 60 s), and live per-option
greeks. The builder (M4) uses expirations to populate the leg-picker and quotes to price a leg
basket; the screener (M5) reuses expirations for DTE selection.

Greeks come from DXLink `Greeks` events (the REST quote endpoint doesn't carry them -- an earlier
version of this module over-generalized that into "no live greeks source exists", which was wrong:
the dxfeed feed serves them per option streamer-symbol, as the suite's shared streamer has always
demonstrated by writing `stream_greeks`). `get_greeks` follows the suite's source order: the shared
stream cache first (free when the streamer daemon happens to cover the symbol), then one
short-lived, bounded `DXLinkStreamer` subscription for whatever's still missing -- the same
opened-on-demand/never-resident pattern as `candle_service`'s history fetch.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from . import streamcache as _streamcache
from .cache import async_get_or_fetch, peek, put
from .session import BrokerSession

_EXPIRATIONS_BUCKET = "chain_expirations"
_QUOTES_BUCKET = "chain_quotes"
_GREEKS_BUCKET = "chain_greeks"
_QUOTE_CHUNK_SIZE = 100
_DEFAULT_QUOTES_TTL_SECONDS = 60.0
_DEFAULT_GREEKS_TTL_SECONDS = 60.0
_GREEKS_IDLE_TIMEOUT_SECONDS = 2.0
_GREEKS_HARD_TIMEOUT_SECONDS = 10.0


def _serialize_option(option: Any) -> dict:
    return {
        "symbol": option.symbol,
        "streamer_symbol": getattr(option, "streamer_symbol", None),
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


def _serialize_greeks_event(event: Any) -> dict:
    def _f(v):
        try:
            f = float(v)
            return None if f != f else f  # NaN guard -- dxfeed sends NaN for missing fields
        except (TypeError, ValueError):
            return None

    return {
        "delta": _f(event.delta),
        "gamma": _f(event.gamma),
        "theta": _f(event.theta),
        "vega": _f(event.vega),
        "iv": _f(getattr(event, "volatility", None)),
        "price": _f(getattr(event, "price", None)),
    }


async def _dxlink_greeks(session: BrokerSession, streamer_symbols: list[str]) -> dict[str, dict]:
    """One short-lived, bounded DXLink subscription collecting a `Greeks` event per symbol.
    Whatever arrived before a failure/timeout is returned -- partial beats none."""
    try:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Greeks
    except ImportError:
        return {}
    try:
        tt_session = session.get_raw_session()
    except Exception:
        return {}

    collected: dict[str, dict] = {}
    wanted = set(streamer_symbols)
    try:
        async with DXLinkStreamer(tt_session) as streamer:
            await streamer.subscribe(Greeks, sorted(wanted))
            deadline = time.monotonic() + _GREEKS_HARD_TIMEOUT_SECONDS
            while wanted - set(collected) and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                wait_for = max(0.01, min(_GREEKS_IDLE_TIMEOUT_SECONDS, remaining))
                try:
                    event = await asyncio.wait_for(streamer.get_event(Greeks), timeout=wait_for)
                except TimeoutError:
                    break
                if event.event_symbol in wanted:
                    collected[event.event_symbol] = _serialize_greeks_event(event)
    except Exception:
        pass
    return collected


async def get_greeks(
    conn: sqlite3.Connection,
    session: BrokerSession,
    streamer_symbols: list[str],
    *,
    ttl: float = _DEFAULT_GREEKS_TTL_SECONDS,
    now: float | None = None,
) -> dict[str, dict]:
    """`{streamer_symbol: {"delta","gamma","theta","vega","iv","price"}}` -- scout's own TTL cache
    first, then the shared stream cache, then one bounded DXLink subscription for the remainder.
    A symbol with no greeks anywhere is simply absent, never an error."""
    now = time.time() if now is None else now
    wanted = sorted({s for s in streamer_symbols if s})
    if not wanted:
        return {}

    result: dict[str, dict] = {}
    missing: list[str] = []
    for sym in wanted:
        cached = peek(conn, _GREEKS_BUCKET, sym)
        if cached is not None and (now - cached[1]) < ttl:
            result[sym] = cached[0]
        else:
            missing.append(sym)

    if missing:
        shared = _streamcache.open_ro()
        if shared is not None:
            try:
                for sym, payload in _streamcache.read_greeks(shared, missing, ttl, now=now).items():
                    put(conn, _GREEKS_BUCKET, sym, payload, now)
                    result[sym] = payload
            finally:
                shared.close()
        missing = [s for s in missing if s not in result]

    if missing:
        for sym, payload in (await _dxlink_greeks(session, missing)).items():
            put(conn, _GREEKS_BUCKET, sym, payload, now)
            result[sym] = payload

    return result


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
