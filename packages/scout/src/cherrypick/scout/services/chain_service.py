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


# Income-grid tiers, reverse-engineered from a reference platform's displayed grids: live deltas
# for its Conservative/Optimal/Aggressive strike picks clustered at ~0.12-0.15 / 0.24-0.28 /
# 0.33-0.38 across two symbols and all three tenors (verified with this module's own get_greeks),
# and its published covered-call guidance independently names 15-20 delta as conservative.
INCOME_TIERS = {"conservative": 0.15, "optimal": 0.25, "aggressive": 0.35}
INCOME_BUCKETS = (("short", 20, 39), ("medium", 40, 70), ("long", 71, 180))


async def income_grid(
    conn: sqlite3.Connection,
    session: BrokerSession,
    cfg: dict,
    symbol: str,
    spot: float,
    *,
    kind: str = "put",
    risk_free_rate: float = 0.0,
    now: float | None = None,
) -> dict:
    """Risk-tier x DTE-bucket candidate strikes: for each bucket the nearest expiration inside its
    window, and per tier the strike whose live |delta| lands nearest the tier target. Cells carry
    credit (mid), raw/annualized return, POW, and delta so the UI needs no further calls. A bucket
    with no expiration, or a tier with no greeks coverage, is simply absent."""
    from datetime import UTC, date, datetime

    from ..analytics import describe as _describe
    from ..analytics.pop import prob_below

    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, tz=UTC).date()
    option_type = "P" if kind == "put" else "C"

    expirations = await get_expirations(conn, session, cfg, symbol)
    chosen: list[tuple[str, date, int, list[dict]]] = []
    for name, lo, hi in INCOME_BUCKETS:
        in_window = []
        for iso, options in expirations["expirations"].items():
            dte = (date.fromisoformat(iso) - today).days
            if lo <= dte <= hi:
                in_window.append((dte, iso, options))
        if in_window:
            dte, iso, options = min(in_window)
            chosen.append((name, date.fromisoformat(iso), dte, options))

    # One greeks pass across every candidate strike of every chosen expiration. Candidates are
    # pre-narrowed to the OTM-side band the <=0.35-delta tiers can actually land in.
    def _candidates(options: list[dict]) -> list[dict]:
        lo, hi = (0.5 * spot, 1.02 * spot) if option_type == "P" else (0.98 * spot, 1.5 * spot)
        keep = [o for o in options if o["option_type"] == option_type and lo <= o["strike"] <= hi]
        return [o for o in keep if o.get("streamer_symbol")]

    all_streamers = [o["streamer_symbol"] for _n, _e, _d, options in chosen for o in _candidates(options)]
    greeks = await get_greeks(conn, session, all_streamers, now=now)

    grid: dict[str, dict] = {}
    picked_symbols: list[str] = []
    for name, exp, dte, options in chosen:
        cells = {}
        candidates = [
            (o, abs(greeks[o["streamer_symbol"]]["delta"]))
            for o in _candidates(options)
            if greeks.get(o["streamer_symbol"], {}).get("delta") is not None
        ]
        for tier, target in INCOME_TIERS.items():
            if not candidates:
                continue
            option, delta = min(candidates, key=lambda c: abs(c[1] - target))
            iv = greeks[option["streamer_symbol"]].get("iv")
            t = dte / 365.0
            pow_ = None
            if iv:
                below = prob_below(spot, option["strike"], iv, t, risk_free_rate)
                pow_ = (1.0 - below) if option_type == "P" else below
            cells[tier] = {
                "symbol": option["symbol"],
                "strike": option["strike"],
                "expiration": exp.isoformat(),
                "dte": dte,
                "delta": round(delta, 3),
                "pow": pow_,
            }
            picked_symbols.append(option["symbol"])
        grid[name] = {"expiration": exp.isoformat(), "dte": dte, "tiers": cells}

    quotes = await get_quotes(conn, session, picked_symbols, now=now)
    for bucket in grid.values():
        for cell in bucket["tiers"].values():
            quote = quotes.get(cell["symbol"]) or {}
            mid = quote.get("mid")
            cell["mid"] = mid
            if mid and kind == "put":
                credit = mid * 100
                max_risk = (cell["strike"] - mid) * 100
                cell["credit"] = credit
                cell["raw_return"] = _describe.raw_return(credit, max_risk)
                cell["annualized_return"] = _describe.annualized_return(credit, max_risk, cell["dte"])
    return {"ok": True, "symbol": expirations["symbol"], "kind": kind, "as_of": now, "grid": grid}


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
