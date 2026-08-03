"""``GET /api/symbol/{sym}/{candles|stats|quote|levels|analysis|expirations|chain}``,
``GET /partial/symbol/{sym}``."""

from __future__ import annotations

import html

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..analytics import levels as _levels
from ..analytics import narrative as _narrative
from ..analytics import trend as _trend
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


@router.get("/api/symbol/{sym}/levels")
async def get_levels(request: Request, sym: str) -> dict:
    """Support/resistance levels + SMA overlay series from the cached daily bars -- the wiring
    `analytics/levels.py` was built for in M3 but never got (computed, tested, and then never
    called by any route until now). `nearest_support`/`nearest_resistance` are the two label
    values the symbol view surfaces: the closest clustered level on each side of the last
    close."""
    candles = await _candles(request, sym)
    bars = candles["bars"]
    levels = _levels.support_resistance(bars) if bars else []
    smas = _levels.moving_averages(bars) if bars else {}
    last_close = bars[-1]["c"] if bars else None

    nearest_support = None
    nearest_resistance = None
    if last_close is not None:
        below = [lv for lv in levels if lv["kind"] == "support" and lv["price"] < last_close]
        above = [lv for lv in levels if lv["kind"] == "resistance" and lv["price"] > last_close]
        nearest_support = max(below, key=lambda lv: lv["price"]) if below else None
        nearest_resistance = min(above, key=lambda lv: lv["price"]) if above else None

    return {
        "ok": True,
        "symbol": candles["symbol"],
        "as_of": candles["as_of"],
        "stale": candles["stale"],
        "levels": levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        # LWC-ready line-series points, Nones (warmup) omitted.
        "smas": {
            name: [
                {"time": bar["t"], "value": value}
                for bar, value in zip(bars, series, strict=True)
                if value is not None
            ]
            for name, series in smas.items()
        },
    }


@router.get("/api/symbol/{sym}/analysis")
async def get_analysis(request: Request, sym: str) -> dict:
    """Plain-language analysis for the symbol view: a trend-following scan headline (when the
    setup exists) plus one concrete Price Action observation -- all generated from data scout
    already computes (candles, clustered levels, the provisional trend classifier, metrics
    earnings dates). See `analytics/narrative.py` for the priority order and honesty posture."""
    app = request.app
    symbol = sym.strip().upper()
    candles = await _candles(request, sym)
    bars = candles["bars"]
    if not bars:
        return {"ok": True, "symbol": symbol, "as_of": candles["as_of"], "stale": True,
                "headline": None, "price_action": None, "trend_1m": None, "trend_6m": None}

    levels = _levels.support_resistance(bars)
    closes = [b["c"] for b in bars]
    p = _trend.DEFAULT_PARAMS["price_ma_count"]
    trend_1m = _trend.price_ma_count(closes, *p["1m"])
    trend_6m = _trend.price_ma_count(closes, *p["6m"])

    metrics_ttl = app.state.cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)
    metrics = await metrics_service.get_metrics(
        app.state.cache_db, app.state.broker_session, [symbol], metrics_ttl
    )
    earnings = (metrics.get(symbol) or {}).get("earnings")

    name = symbol  # a display-name source (instrument description) is a possible later refinement
    return {
        "ok": True,
        "symbol": symbol,
        "as_of": candles["as_of"],
        "stale": candles["stale"],
        "trend_1m": trend_1m,
        "trend_6m": trend_6m,
        "headline": _narrative.scan_headline(name, trend_1m, trend_6m),
        "price_action": _narrative.price_action(name, bars, levels, trend_6m, earnings),
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
