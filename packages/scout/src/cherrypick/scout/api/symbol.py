"""``GET /api/symbol/{sym}/{candles|stats|quote|expirations|chain}``, ``GET /partial/symbol/{sym}``."""

from __future__ import annotations

import html

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import candle_service, chain_service, metrics_service, quote_service

router = APIRouter()


async def _candles(request: Request, sym: str) -> dict:
    app = request.app
    return await candle_service.get_candles(app.state.cache_db, app.state.broker_session, app.state.cfg, sym)


def _compute_stats(bars: list[dict]) -> dict:
    if not bars:
        return {
            "last_close": None,
            "change_pct": None,
            "week52_high": None,
            "week52_low": None,
            "avg_volume_30d": None,
        }
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else None
    change_pct = ((last["c"] - prev["c"]) / prev["c"]) if prev and prev["c"] else None
    year = bars[-252:] if len(bars) > 252 else bars
    recent_volume = [b["v"] for b in bars[-30:] if b["v"] is not None]
    return {
        "last_close": last["c"],
        "change_pct": change_pct,
        "week52_high": max(b["h"] for b in year),
        "week52_low": min(b["l"] for b in year),
        "avg_volume_30d": (sum(recent_volume) / len(recent_volume)) if recent_volume else None,
    }


@router.get("/api/symbol/{sym}/candles")
async def get_candles(request: Request, sym: str) -> dict:
    return await _candles(request, sym)


@router.get("/api/symbol/{sym}/stats")
async def get_stats(request: Request, sym: str) -> dict:
    app = request.app
    candles = await _candles(request, sym)
    metrics_ttl = app.state.cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)
    metrics = await metrics_service.get_metrics(
        app.state.cache_db, app.state.broker_session, [sym], metrics_ttl
    )
    info = metrics.get(sym.strip().upper(), {})
    return {
        "ok": True,
        "symbol": candles["symbol"],
        "as_of": candles["as_of"],
        "stale": candles["stale"] or not info,
        **_compute_stats(candles["bars"]),
        "iv_rank": info.get("iv_rank"),
        "iv_30d": info.get("iv_30d"),
        "liquidity_rating": info.get("liquidity_rating"),
        "beta": info.get("beta"),
    }


@router.get("/api/symbol/{sym}/quote")
async def get_quote(request: Request, sym: str) -> dict:
    """A fast spot + IV lookup for the builder's symbol-selection prefill -- deliberately **not**
    `_candles`-backed. `/stats` needs full candle history for week52-range/avg-volume, but the
    builder only ever reads `last_close`/`iv_30d`; routing that through `candle_service` meant every
    symbol selection waited on a cold DXLink candle backfill (worst case tens of seconds) to answer
    a question `quote_service` (stream-cache-first, REST fallback, no DXLink at all) already answers
    in a fraction of the time. See CLAUDE.md's latency-pass note."""
    app = request.app
    symbol = sym.strip().upper()
    cfg = app.state.cfg.get("refresh", {})
    quotes = await quote_service.get_quotes(
        app.state.broker_session,
        [symbol],
        stream_cache_max_age_seconds=cfg.get("stream_cache_max_age_seconds", 10),
    )
    metrics = await metrics_service.get_metrics(
        app.state.cache_db, app.state.broker_session, [symbol], cfg.get("metrics_ttl_seconds", 900)
    )
    quote = quotes.get(symbol, {})
    info = metrics.get(symbol, {})
    return {
        "ok": True,
        "symbol": symbol,
        "last": quote.get("last"),
        "iv_30d": info.get("iv_30d"),
        "iv_rank": info.get("iv_rank"),
        "stale": not quote and not info,
    }


@router.get("/api/symbol/{sym}/expirations")
async def get_expirations(request: Request, sym: str) -> dict:
    app = request.app
    return await chain_service.get_expirations(
        app.state.cache_db, app.state.broker_session, app.state.cfg, sym
    )


@router.get("/api/symbol/{sym}/chain")
async def get_chain(request: Request, sym: str, expiration: str = Query(...)) -> dict:
    app = request.app
    expirations = await chain_service.get_expirations(
        app.state.cache_db, app.state.broker_session, app.state.cfg, sym
    )
    options = expirations["expirations"].get(expiration, [])
    option_symbols = [o["symbol"] for o in options]
    quotes = await chain_service.get_quotes(app.state.cache_db, app.state.broker_session, option_symbols)
    for option in options:
        option["quote"] = quotes.get(option["symbol"])
    return {
        "ok": True,
        "symbol": expirations["symbol"],
        "expiration": expiration,
        "as_of": expirations["as_of"],
        "stale": expirations["stale"],
        "options": options,
    }


@router.get("/partial/symbol/{sym}", response_class=HTMLResponse)
async def partial_symbol(sym: str) -> HTMLResponse:
    page = _templates.render("symbol.html", symbol=html.escape(sym.strip().upper()))
    return HTMLResponse(page)
