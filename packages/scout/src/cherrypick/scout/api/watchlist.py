"""``GET /api/watchlist`` / ``POST /api/watchlist`` — the user-curated symbol list.

The one mutating route in M1. Gated by ``SecurityMiddleware`` (CSRF + content-type + origin) same
as every other mutating route in the package.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..services import watchlist as _watchlist

router = APIRouter()


class WatchlistEdit(BaseModel):
    action: str = "set"  # "set" | "add" | "remove"
    symbols: list[str] = []


@router.get("/api/watchlist")
def get_watchlist(request: Request) -> dict:
    symbols = _watchlist.load(request.app.state.watchlist_path)
    return {"ok": True, "symbols": symbols}


@router.post("/api/watchlist")
def post_watchlist(edit: WatchlistEdit, request: Request) -> dict:
    path = request.app.state.watchlist_path
    if edit.action == "add":
        symbols = _watchlist.add(path, edit.symbols)
    elif edit.action == "remove":
        symbols = _watchlist.remove(path, edit.symbols)
    else:
        symbols = _watchlist.save(path, edit.symbols)
    return {"ok": True, "symbols": symbols}
