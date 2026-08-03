"""``GET /api/screener?strategy=...[&iv=...&liquidity=...&cap=...]`` and ``GET /partial/screener``.

The three optional chip-filter params are comma-separated bucket names (e.g. ``cap=large,mega``);
an unknown bucket name is a 400, not silently ignored -- a typo'd filter that quietly matched
everything would read as "no results were filtered" when the opposite happened.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import screener_service
from ..services import watchlist as _watchlist

router = APIRouter()

_DEFAULT_STRATEGY = "put_credit_spread"

_FILTER_BUCKETS = {
    "iv": screener_service.IV_BUCKETS,
    "liquidity": screener_service.LIQUIDITY_BUCKETS,
    "cap": screener_service.CAP_BUCKETS,
}


def _parse_filters(iv: str | None, liquidity: str | None, cap: str | None) -> dict:
    filters: dict = {}
    for name, raw in (("iv", iv), ("liquidity", liquidity), ("cap", cap)):
        if raw is None or not raw.strip():
            continue
        buckets = {b.strip() for b in raw.split(",") if b.strip()}
        unknown = buckets - _FILTER_BUCKETS[name]
        if unknown:
            raise HTTPException(400, f"unknown {name} bucket(s): {sorted(unknown)}")
        filters[name] = buckets
    return filters


async def _run(request: Request, strategy: str, filters: dict) -> dict:
    app = request.app
    symbols = _watchlist.load(app.state.watchlist_path)
    return await screener_service.run_screener(
        app.state.cache_db, app.state.broker_session, app.state.cfg, symbols, strategy, filters=filters
    )


@router.get("/api/screener")
async def get_screener(
    request: Request,
    strategy: str = _DEFAULT_STRATEGY,
    iv: str | None = Query(None, description="comma-separated: lt50,gte50"),
    liquidity: str | None = Query(None, description="comma-separated: not,somewhat,very"),
    cap: str | None = Query(None, description="comma-separated: small,medium,large,mega"),
) -> dict:
    return await _run(request, strategy, _parse_filters(iv, liquidity, cap))


@router.get("/partial/screener", response_class=HTMLResponse)
async def partial_screener(request: Request, strategy: str = _DEFAULT_STRATEGY) -> HTMLResponse:
    page = _templates.render("screener.html", strategy=html.escape(strategy))
    return HTMLResponse(page)
