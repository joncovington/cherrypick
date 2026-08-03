"""``GET /partial/builder/{sym}`` -- the leg-basket builder shell. Chain data and payoff computation
happen client-side (`static/js/payoff.js`) against the already-existing `/api/symbol/{sym}/{
expirations,chain}` and `/api/payoff` routes; this route only renders the container."""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .. import templates as _templates

router = APIRouter()


@router.get("/partial/builder/{sym}", response_class=HTMLResponse)
async def partial_builder(sym: str) -> HTMLResponse:
    page = _templates.render("builder.html", symbol=html.escape(sym.strip().upper()))
    return HTMLResponse(page)
