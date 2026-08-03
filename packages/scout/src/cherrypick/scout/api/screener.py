"""``GET /api/screener?strategy=...`` and ``GET /partial/screener``."""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import screener_service
from ..services import watchlist as _watchlist

router = APIRouter()

_DEFAULT_STRATEGY = "put_credit_spread"


async def _run(request: Request, strategy: str) -> dict:
    app = request.app
    symbols = _watchlist.load(app.state.watchlist_path)
    return await screener_service.run_screener(
        app.state.cache_db, app.state.broker_session, app.state.cfg, symbols, strategy
    )


@router.get("/api/screener")
async def get_screener(request: Request, strategy: str = _DEFAULT_STRATEGY) -> dict:
    return await _run(request, strategy)


@router.get("/partial/screener", response_class=HTMLResponse)
async def partial_screener(request: Request, strategy: str = _DEFAULT_STRATEGY) -> HTMLResponse:
    page = _templates.render("screener.html", strategy=html.escape(strategy))
    return HTMLResponse(page)
