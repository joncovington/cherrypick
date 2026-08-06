"""``GET /api/calendar`` (raw JSON) and ``GET /partial/calendar`` (the htmx fragment) -- both drive
`services.calendar_service.get_calendar`, so the two surfaces can never disagree about what the
calendar shows."""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import calendar_service
from ..services import watchlist as _watchlist

router = APIRouter()


async def _build(request: Request, days: int) -> dict:
    app = request.app
    symbols = _watchlist.load(app.state.watchlist_path)
    return await calendar_service.get_calendar(
        app.state.cache_db, app.state.broker_session, app.state.cfg, symbols, days=days
    )


@router.get("/api/calendar")
async def get_calendar(request: Request, days: int = 14) -> dict:
    return await _build(request, days)


def _fmt_pct(value) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, int | float) else "—"


def _fmt(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _symbol_link(symbol) -> str:
    if not symbol:
        return "—"
    sym = html.escape(str(symbol))
    return f'<a href="#" class="builder-link" data-sym="{sym}">{sym}</a>'


def _row_html(entry: dict) -> str:
    cls = "stale" if entry.get("stale") else ""
    return (
        f'<tr class="{cls}">'
        f"<td>{_fmt(entry.get('date'))}</td>"
        f"<td>{_symbol_link(entry.get('symbol'))}</td>"
        f"<td>{_fmt(entry.get('when'))}</td>"
        f"<td>{_fmt(entry.get('consensus_eps'))}</td>"
        f"<td>{_fmt_pct(entry.get('expected_move_pct'))}</td>"
        f"<td>{_fmt(entry.get('iv_rank'))}</td>"
        f"<td>{_fmt(entry.get('liquidity_rating'))}</td>"
        f"<td>{_fmt(entry.get('source'))}</td>"
        "</tr>"
    )


@router.get("/partial/calendar", response_class=HTMLResponse)
async def partial_calendar(request: Request, days: int = 14) -> HTMLResponse:
    data = await _build(request, days)
    rows = "".join(_row_html(e) for e in data["entries"])
    if not rows:
        rows = '<tr><td colspan="8" class="note">No earnings in this window.</td></tr>'
    notices = []
    if not data["dolt_available"]:
        notices.append(
            "Dolt is unreachable -- showing watchlist-only coverage from live metrics, not the "
            "broader calendar."
        )
    if data.get("liquid_only") and not data.get("liquidity_filter_available"):
        notices.append(
            "Couldn't reach tastytrade's Liquid Symbols watchlist -- showing all names, "
            "unfiltered by liquidity."
        )
    notice = "".join(f'<p class="notice">{html.escape(n)}</p>' for n in notices)
    page = _templates.render("calendar.html", days=str(data["days"]), notice=notice, rows=rows)
    return HTMLResponse(page)
