"""Declare this module's stream needs: its underlyings, its open legs, and its two expirations.

Writes ``~/.cherrypick/state/stream_requests/calendars.json``. Three fields matter here:

- ``symbols`` — the underlyings (SPX), for spot and the session summary.
- ``leg_sources`` — one SELECT over this module's own ledger returning every open leg's streamer
  symbol, re-run by the producer every subscription poll, so a filled entry is subscribed within a
  poll and a closed leg ages out without a restart.
- ``expirations`` — the calendar's whole reason for the field existing: the NEXT entry session's
  computed front/back dates plus every expiration still held open. Derived from DATES only (never
  the clock), so the value changes exactly at an ET date boundary — the roll to next week's dates
  lands on the first off-session tick after Friday, giving the producer days of notice and never a
  mid-session subscription change.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol or
date is a data-availability problem the provider already surfaces as a refusal, not a crash.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.calendars import clock, db

_MODULE = "calendars"
_log = logging.getLogger("calendars_paper_loop")


def wanted_expirations(conn, symbols: list[str], today: date) -> dict[str, list[str]]:
    """Per-symbol expiration dates the cache must hold: the next entry's front/back plus whatever
    is still open in the ledger (a week-N-1 long on a Monday morning outlives the roll)."""
    dates: set[str] = set(db.open_leg_expirations(conn))
    plan = clock.week_plan(today)
    if plan is not None:
        dates.add(plan["front_expiration"])
        dates.add(plan["back_expiration"])
    return {symbol: sorted(dates) for symbol in symbols} if dates else {}


def write(config: dict, conn, db_path: str, *, today: date | None = None) -> Path:
    symbols = config.get("symbols") or ["SPX"]
    today = today or clock.now_et().date()
    leg_sources = [
        _sr.leg_source(
            db_path,
            "SELECT l.streamer_symbol FROM dc_legs l JOIN dc_positions p "
            "ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND p.status != 'closed'",
        )
    ]
    return _sr.write_request(
        _MODULE,
        symbols,
        leg_sources=leg_sources,
        expirations=wanted_expirations(conn, [s.strip().upper() for s in symbols], today),
    )


def register(config: dict, conn, db_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, log=_log)
