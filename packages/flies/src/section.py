"""Section adapter — the compact card the suite dashboard renders for this module.

`run.py section --json` emits a `cherrypick.core.viz` payload, which the orchestrator renders with no
flies-specific code. The rich interactive views stay in this module's own `dashboard --serve`.

The `bars` contract was built for net-GEX-by-strike: signed values, `tone_by_sign` colouring, and a
`focus` marker. It maps exactly onto **payoff-by-price**, so the card ends up drawing the profit forest
itself — green across the band where the book profits, red outside it, focus line at spot. No new
rendering code anywhere; the existing contract already says everything this strategy needs to show.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analytics  # noqa: E402
import db as dbmod  # noqa: E402

_NOTE = ("payoff at expiry across price · green = book profits · a legged fly's floor is its own "
         "credit; a funded book's floor holds only inside the funding spreads' wings")


def _money(v) -> str:
    return "–" if v is None else f"${v:,.0f}"


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def _pick_arm(books: list[dict], requested: str | None) -> str | None:
    """Which arm's curve to draw. An explicit request wins; otherwise the book with the most positions,
    since an empty book's flat line tells the viewer nothing."""
    if requested:
        return requested
    if not books:
        return None
    return max(books, key=lambda b: (b.get("credit_collected") or 0) + (b.get("debits_paid") or 0)
               )["arm"]


def build_section(db_path: str | None = None, day: str | None = None,
                  arm: str | None = None) -> dict:
    """Return a cherrypick.core.viz section payload, or {ok: False, error}."""
    conn = dbmod.connect(db_path)
    try:
        day = day or analytics.today()
        overview = analytics.session_overview(conn, day)
        chosen = _pick_arm(overview["books"], arm)
        if chosen is None:
            # Not an error. Before the first entry of the day there is genuinely nothing to draw, and
            # a card that shouted "error" every morning would train the operator to ignore it.
            return {
                "ok": True,
                "title": "Flies — no positions yet",
                "subtitle": f"{day} · 0DTE net-credit butterflies",
                "metrics": [{"label": "Positions", "value": "0"}],
                "note": _NOTE,
            }

        curve = analytics.payoff_curve(conn, day, chosen)
        stats = overview["stats"]
        completion = overview["completion"]
        floor = (curve.get("floor") or {})
        band = floor.get("band")

        metrics = [
            {"label": "Net P&L", "value": _money(stats["net_pnl"]),
             "tone": "pos" if (stats["net_pnl"] or 0) >= 0 else "neg"},
            {"label": "Positions", "value": str(len(overview["positions"])), "tone": "accent"},
            {"label": "Risk-free", "value": f"{overview['risk_free_count']}/{overview['fly_count']}",
             "tone": "pos" if overview["risk_free_count"] else None},
            {"label": "Completion", "value": _pct(completion["completion_rate"])},
            # The floor is stated with the band it holds over, always. A floor without its band is the
            # claim this module exists to avoid making.
            {"label": "Book floor", "value": _money(floor.get("worst")),
             "tone": "pos" if floor.get("floor_holds") else "neg"},
        ]
        if band:
            metrics.append({"label": "Floor holds", "value": f"{band[0]:.0f}–{band[1]:.0f}"})

        subtitle = f"{day} · arm {chosen} · {curve['positions']} position(s)"
        if floor.get("unbounded_below"):
            subtitle += " · loses outside the band"

        return {
            "ok": True,
            "title": f"Flies — {chosen}",
            "subtitle": subtitle,
            "metrics": metrics,
            "bars": {
                "labels": curve["prices"],
                "focus": _spot(overview),
                "series": [
                    {"name": "P&L at expiry", "values": curve["pnl"], "tone_by_sign": True},
                ],
            },
            "note": _NOTE,
        }
    finally:
        conn.close()


def _spot(overview: dict):
    """Latest underlying price seen today, for the focus marker."""
    for p in reversed(overview["positions"]):
        if p.get("underlying_at_entry") is not None:
            return p["underlying_at_entry"]
    return None
