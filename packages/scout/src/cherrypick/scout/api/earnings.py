"""``GET /api/earnings-screens`` and ``GET /api/earnings-upcoming`` (raw JSON) and
``GET /partial/earnings`` (the htmx fragment) -- scout's read-only view onto the earnings package's
``entry_reviews`` table (the recorded per-symbol screening metric vector, accepted or rejected, from
either of its two SQLite databases) plus the same forward-looking earnings calendar the Calendar tab
already computes. See ``services/earnings_metrics_service.py`` for the read-only DB access and the
composition with ``calendar_service.get_calendar`` -- this router stays thin, same posture as
``api/calendar.py``.
"""

from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import templates as _templates
from ..services import earnings_metrics_service

router = APIRouter()


@router.get("/api/earnings-screens")
def get_earnings_screens(date: str | None = None, mode: str = "paper") -> dict:
    return earnings_metrics_service.get_screens(date, mode)


@router.get("/api/earnings-upcoming")
async def get_earnings_upcoming(request: Request, days: int = 10) -> dict:
    app = request.app
    return await earnings_metrics_service.get_upcoming(
        app.state.cache_db, app.state.broker_session, app.state.cfg, days=days
    )


def _fmt(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _fmt_pct(value) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, int | float) else "—"


def _fmt_ratio(value) -> str:
    """IV/RV ratio, 1 decimal place."""
    return f"{value:.1f}" if isinstance(value, int | float) else "—"


def _fmt_term_structure(value) -> str:
    """Term structure, 3 decimal places -- the sign and small magnitude (e.g. -0.004 threshold)
    are the whole point of this number, so 1 decimal would round most real readings to 0.0."""
    return f"{value:.3f}" if isinstance(value, int | float) else "—"


def _fmt_pct2(value) -> str:
    """IV rank / IV percentile, 2 decimal places -- both arrive as 0..1 fractions despite the
    SDK's naming (see metrics_service._iv_30d_frac's own note on this)."""
    return f"{value * 100:.2f}%" if isinstance(value, int | float) else "—"


def _fmt_market_cap(value) -> str:
    """Market cap as an abbreviated dollar figure ($4.26B / $785.24M / ...) rather than a bare
    number of dollars -- these values run into the billions, where a raw digit string is far
    harder to scan than a unit-suffixed one."""
    if not isinstance(value, int | float):
        return "—"
    abs_value = abs(value)
    sign = "-" if value < 0 else ""
    if abs_value >= 1e12:
        return f"{sign}${abs_value / 1e12:.2f}T"
    if abs_value >= 1e9:
        return f"{sign}${abs_value / 1e9:.2f}B"
    if abs_value >= 1e6:
        return f"{sign}${abs_value / 1e6:.2f}M"
    if abs_value >= 1e3:
        return f"{sign}${abs_value / 1e3:.2f}K"
    return f"{sign}${abs_value:.2f}"


def _fmt_price(value) -> str:
    return f"${value:,.2f}" if isinstance(value, int | float) else "—"


def _fmt_volume(value) -> str:
    """Share volume as a comma-grouped whole number (e.g. "3,178,272") -- matches the
    EarningsEdgeDetection reference display; unlike market cap, a bare volume count is small
    enough to read directly, so this isn't abbreviated."""
    return f"{value:,.0f}" if isinstance(value, int | float) else "—"


_TIER_LABELS = {"recommended": "Recommended", "near_miss": "Near miss", "fail": "Fail"}


def _fmt_tier(entry: dict) -> str:
    """A recommended/near_miss/fail badge (see cherrypick.earnings.symbol_watch.classify_tier),
    or an unscored dash when the chain-dependent inputs never resolved. `tier_reasons` -- the
    specific gates that tripped -- surface as the badge's hover title rather than their own
    column; a clean "Recommended" carries no reasons and no title."""
    tier = entry.get("tier")
    reasons = entry.get("tier_reasons") or []
    css_tier = (tier or "unscored").replace("_", "-")
    label = _TIER_LABELS.get(tier, "—")
    title_attr = f' title="{html.escape("; ".join(reasons))}"' if reasons else ""
    return f'<span class="tier tier-{css_tier}"{title_attr}>{label}</span>'


def _fmt_watch_notice(watch: dict) -> str:
    """Status banner for the earnings package's own scheduled forward-preview scan
    (symbol_watch.json) -- the source of every Upcoming-row field beyond what the batched
    metrics call already supplies (symbol/timing/IV rank/market cap). Three states: never run
    yet (a fresh install, or the scheduled task hasn't fired once), a pass actively in progress
    (the "N of M done" spinner -- see partial_earnings' watch_poll_attr, which keeps this page
    polling only while this state holds), or the timestamp of the last completed pass."""
    if not watch or watch.get("never_run"):
        return (
            '<p class="notice">Forward-looking metrics (expected move, term structure, IV/RV, '
            "winrate, historical move stats) haven't been computed yet -- waiting on the "
            "earnings module's scheduled scan to run for the first time.</p>"
        )
    if watch.get("pass_completed_at") is None:
        total, done = watch.get("total") or 0, watch.get("done") or 0
        return (
            '<p class="notice loading">Scanning upcoming earnings for forward-looking metrics… '
            f"{done} of {total} symbols done.</p>"
        )
    completed = watch.get("pass_completed_at")
    when = datetime.fromtimestamp(completed).strftime("%Y-%m-%d %H:%M") if completed else "—"
    return f'<p class="note">Forward-looking metrics last refreshed {html.escape(when)}.</p>'


def _outcome(row: dict) -> str:
    tier = row.get("best_tier")
    if row.get("selected"):
        return tier or "selected"
    return tier or "rejected"


def _screen_row_html(row: dict) -> str:
    cls = "accepted" if row.get("selected") else "rejected"
    return (
        f'<tr class="{cls}">'
        f"<td>{_fmt(row.get('symbol'))}</td>"
        f"<td>{_fmt(row.get('timing'))}</td>"
        f"<td>{_fmt(row.get('strategy'))}</td>"
        f"<td>{_fmt(_outcome(row))}</td>"
        f"<td>{_fmt(row.get('reason'))}</td>"
        f"<td>{_fmt(row.get('composite_score'))}</td>"
        f"<td>{_fmt_ratio(row.get('iv_rv_ratio'))} ({_fmt(row.get('iv_rv_source'))})</td>"
        f"<td>{_fmt_pct(row.get('winrate'))} (n={_fmt(row.get('winrate_sample'))})</td>"
        f"<td>{_fmt_term_structure(row.get('term_structure'))}</td>"
        f"<td>{_fmt_pct(row.get('expected_move_pct'))}</td>"
        f"<td>{_fmt_pct(row.get('avg_actual_move_pct'))}</td>"
        f"<td>{_fmt(row.get('implied_vs_avg_actual'))}</td>"
        f"<td>{_fmt(row.get('move_tail_veto'))}</td>"
        f"<td>{_fmt_pct(row.get('net_combo_spread_pct'))}</td>"
        f"<td>{_fmt_pct2(row.get('iv_rank'))} / {_fmt_pct2(row.get('iv_percentile'))}</td>"
        f"<td>{_fmt_market_cap(row.get('market_cap'))}</td>"
        "</tr>"
    )


def _date_option_html(scan_date: str, selected_date: str | None) -> str:
    attr = " selected" if scan_date == selected_date else ""
    escaped = html.escape(scan_date)
    return f'<option value="{escaped}"{attr}>{escaped}</option>'


def _upcoming_row_html(entry: dict) -> str:
    """A liquid-universe earnings screener row (Date/Symbol/Timing plus a tier badge and the
    EarningsEdgeDetection-derived metric set: Price, Volume, Winrate, IV/RV, Term Structure,
    Expected Move %, Avg Actual Move %, IV Rank, Mkt Cap) -- a subset of _screen_row_html's
    columns; Strategy/Outcome/Reason/Score/Implied-vs-actual/Move-tail-veto/Net-combo-spread are
    dropped here since a preview makes no accept/reject decision (see the tier badge instead) and
    this section spans a multi-day window rather than one scan_date. Every field beyond Date/
    Symbol comes from the earnings package's own scheduled forward-preview scan
    (symbol_watch.json, merged+filtered in by earnings_metrics_service.get_upcoming -- see
    _fmt_watch_notice for that scan's own status); a row only appears here once that scan has
    actually reached it (see get_upcoming's own docstring), so none of these read as blank "—"
    placeholders the way the old design's unfiltered rows could."""
    return (
        "<tr>"
        f"<td>{_fmt(entry.get('date'))}</td>"
        f"<td>{_fmt(entry.get('symbol'))}</td>"
        f"<td>{_fmt(entry.get('when'))}</td>"
        f"<td>{_fmt_tier(entry)}</td>"
        f"<td>{_fmt_price(entry.get('price'))}</td>"
        f"<td>{_fmt_volume(entry.get('avg_volume'))}</td>"
        f"<td>{_fmt_pct(entry.get('winrate'))} (n={_fmt(entry.get('winrate_sample'))})</td>"
        f"<td>{_fmt_ratio(entry.get('iv_rv_ratio'))} ({_fmt(entry.get('iv_rv_source'))})</td>"
        f"<td>{_fmt_term_structure(entry.get('term_structure'))}</td>"
        f"<td>{_fmt_pct(entry.get('expected_move_pct'))}</td>"
        f"<td>{_fmt_pct(entry.get('avg_actual_move_pct'))}</td>"
        f"<td>{_fmt_pct2(entry.get('iv_rank'))}</td>"
        f"<td>{_fmt_market_cap(entry.get('market_cap'))}</td>"
        "</tr>"
    )


@router.get("/partial/earnings", response_class=HTMLResponse)
async def partial_earnings(
    request: Request, date: str | None = None, mode: str = "paper", days: int = 10
) -> HTMLResponse:
    app = request.app
    screens = earnings_metrics_service.get_screens(date, mode)
    upcoming = await earnings_metrics_service.get_upcoming(
        app.state.cache_db, app.state.broker_session, app.state.cfg, days=days
    )

    screen_dates = earnings_metrics_service.get_screen_dates(mode)
    selected_date = screens.get("scan_date")
    if screen_dates:
        date_options = "".join(_date_option_html(d, selected_date) for d in screen_dates)
    else:
        date_options = '<option value="">No scan dates available</option>'

    mode_options = "".join(
        f'<option value="{m}"{" selected" if m == mode else ""}>{label}</option>'
        for m, label in (("paper", "Paper"), ("live", "Live"))
    )

    screen_rows = "".join(_screen_row_html(r) for r in screens.get("rows", []))
    if not screen_rows:
        note = screens.get("note") or "No entry reviews recorded for this date."
        screen_rows = f'<tr><td colspan="16" class="note">{html.escape(note)}</td></tr>'

    upcoming_rows = "".join(_upcoming_row_html(e) for e in upcoming.get("entries", []))
    if not upcoming_rows:
        upcoming_rows = '<tr><td colspan="13" class="note">No upcoming earnings found.</td></tr>'

    market_notice = ""
    if upcoming.get("ok") and upcoming.get("market_hours") is False:
        market_notice = (
            '<p class="notice">Market closed -- earnings dates and market cap (from '
            "tastytrade's reference metrics) refresh regardless of session; forward-looking "
            "metrics below carry whatever timestamp the earnings module's last completed scan "
            "shows.</p>"
        )

    watch = upcoming.get("watch") or {}
    watch_notice = _fmt_watch_notice(watch)
    # Keep polling only while a pass is actively running (started, not yet completed) -- once the
    # server stops including this attribute on a swapped-in response, htmx has nothing left to
    # retrigger and the page goes quiet on its own; a page loaded outside any active pass never
    # starts polling at all.
    watch_in_progress = watch.get("pass_started_at") is not None and watch.get("pass_completed_at") is None
    watch_poll_attr = (
        ' hx-get="/partial/earnings" hx-trigger="every 5s" hx-target="#content" '
        'hx-include="#earnings-date,#earnings-mode,#earnings-days"'
        if watch_in_progress
        else ""
    )

    page = _templates.render(
        "earnings.html",
        mode=html.escape(mode),
        date_options=date_options,
        mode_options=mode_options,
        screen_rows=screen_rows,
        upcoming_rows=upcoming_rows,
        market_notice=market_notice,
        watch_notice=watch_notice,
        watch_poll_attr=watch_poll_attr,
        days=str(days),
    )
    return HTMLResponse(page)
