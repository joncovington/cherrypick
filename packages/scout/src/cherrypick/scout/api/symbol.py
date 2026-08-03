"""``GET /api/symbol/{sym}/candles``, ``GET /api/symbol/{sym}/stats``, ``GET /partial/symbol/{sym}``.

`expirations`/`chain` (the plan's other two symbol sub-routes) land with `chain_service` in M4 --
adding them now would mean either a broker call this milestone doesn't otherwise need or a stub that
always 404s, neither of which is better than just not routing them yet.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import candle_service, metrics_service

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
        "liquidity_rating": info.get("liquidity_rating"),
        "beta": info.get("beta"),
    }


@router.get("/partial/symbol/{sym}", response_class=HTMLResponse)
async def partial_symbol(sym: str) -> HTMLResponse:
    page = _templates.render("symbol.html", symbol=html.escape(sym.strip().upper()))
    return HTMLResponse(page)
