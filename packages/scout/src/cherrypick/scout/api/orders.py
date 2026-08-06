"""``POST /api/order/dry-run`` (validate a leg basket without saving), the staged-ticket surface
(``GET``/``POST /api/staged``, ``POST /api/staged/delete``), and ``GET /partial/staged`` for the
Staged nav view. Every mutating route here rides `SecurityMiddleware` (CSRF + content-type + origin)
the same as `api/watchlist.py`. `services/staging.py` owns the one broker-order call site; nothing
here talks to the broker directly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import templates as _templates
from ..services import staging as _staging

router = APIRouter()


class OrderLeg(BaseModel):
    symbol: str
    quantity: float
    price: float


class DryRunRequest(BaseModel):
    legs: list[OrderLeg]


class StageRequest(BaseModel):
    symbol: str
    strategy: str = "custom"
    legs: list[OrderLeg]
    credit: float | None = None
    max_risk: float | None = None
    note: str | None = None


class DeleteRequest(BaseModel):
    id: str


@router.post("/api/order/dry-run")
async def post_dry_run(body: DryRunRequest, request: Request) -> dict:
    legs = [leg.model_dump() for leg in body.legs]
    return await _staging.dry_run_order(request.app.state.broker_session, legs)


@router.get("/api/staged")
def get_staged(request: Request) -> dict:
    return {"ok": True, "tickets": _staging.list_staged(request.app.state.cache_db)}


@router.post("/api/staged")
async def post_staged(body: StageRequest, request: Request) -> dict:
    legs = [leg.model_dump() for leg in body.legs]
    ticket = await _staging.stage_ticket(
        request.app.state.cache_db,
        request.app.state.broker_session,
        symbol=body.symbol.strip().upper(),
        strategy=body.strategy,
        legs=legs,
        credit=body.credit,
        max_risk=body.max_risk,
        note=body.note,
    )
    return {"ok": True, "ticket": ticket}


@router.post("/api/staged/delete")
def post_staged_delete(body: DeleteRequest, request: Request) -> dict:
    if not _staging.delete_staged(request.app.state.cache_db, body.id):
        raise HTTPException(404, "ticket not found")
    return {"ok": True}


@router.get("/partial/staged", response_class=HTMLResponse)
def partial_staged() -> HTMLResponse:
    return HTMLResponse(_templates.render("staged.html"))
