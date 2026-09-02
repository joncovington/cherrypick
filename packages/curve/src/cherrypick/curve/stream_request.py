"""Declare this module's stream needs: VXX as the underlying, VIX/VIX3M as quote-only legs, the
one target expiration, and 270 days of daily history.

Writes ``~/.cherrypick/state/stream_requests/curve.json``. Fields, per the plan:

- ``symbols`` — VXX only. It genuinely is an underlying here: spot + chain.
- ``legs`` — VIX, VIX3M as static extra symbols. Quote-only: they carry no chain here, so they
  belong in `legs`, not `symbols` — declaring them as `symbols` would have the producer maintain
  0DTE chains for two symbols nothing in this module reads (the overview 2026-08-17 incident this
  module's plan explicitly cites).
- ``leg_sources`` — every open leg's streamer symbol, re-run by the producer every subscription
  poll, so a filled entry is subscribed within a poll and a closed leg ages out with no restart.
- ``expirations`` — the one computed target expiration plus every expiration still held open.
- ``history_days`` — 270 for VXX, VIX, and VIX3M: the regime series wants a year of context on day
  one (not four), and `regime-history` reads whatever the cache actually holds.
- No ``window_hints``: a ~30-delta short call and a wing a few dollars out both sit inside any
  default ATM window.

Best-effort by design: a failed write must never break the paper loop.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.curve import clock, db

_MODULE = "curve"
_HISTORY_DAYS = 270
_log = logging.getLogger("curve_paper_loop")


def wanted_expirations(conn, symbol: str, today: date, params: dict | None = None) -> dict[str, list[str]]:
    """The cache must hold: the current target expiration plus whatever is still open in the
    ledger (a position rides into expiration week under `close_dte`'s slack)."""
    dates: set[str] = set(db.open_leg_expirations(conn))
    plan = clock.target_expiration(today, params)
    if plan is not None:
        dates.add(plan["expiration"])
    return {symbol: sorted(dates)} if dates else {}


def write(config: dict, conn, db_path: str, *, cache_path: str, today: date | None = None) -> Path:
    symbol = (config.get("symbol") or "VXX").strip().upper()
    today = today or clock.now_et().date()
    defaults = config.get("defaults") or {}
    leg_sources = [
        _sr.leg_source(
            db_path,
            "SELECT l.streamer_symbol FROM curve_legs l JOIN curve_positions p "
            "ON p.position_id = l.position_id WHERE l.status = 'open' AND p.status != 'closed'",
        )
    ]
    return _sr.write_request(
        _MODULE,
        [symbol],
        legs=["VIX", "VIX3M"],
        leg_sources=leg_sources,
        expirations=wanted_expirations(conn, symbol, today, defaults),
        history_days={symbol: _HISTORY_DAYS, "VIX": _HISTORY_DAYS, "VIX3M": _HISTORY_DAYS},
    )


def register(config: dict, conn, db_path: str, *, cache_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, cache_path=cache_path, log=_log)
