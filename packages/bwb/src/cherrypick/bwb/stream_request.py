"""Declare this module's stream needs: SPX as the underlying, the current target expiration plus
every expiration still held open, and a window wide enough for the body/wings AND the add-on
bracket.

Writes ``~/.cherrypick/state/stream_requests/bwb.json``.

- ``symbols`` — SPX only.
- ``leg_sources`` — one SELECT over `bwb_legs` for open legs, so filled entries stay subscribed and
  closed legs age out.
- ``expirations`` — the one computed target expiration plus every expiration still held open (a
  7-DTE ladder holds up to ~7 distinct expirations at steady state).
- ``window_hints`` — load-bearing here: the body sits a full expected move below spot (~1-1.5% on
  SPX at $5 spacing), at or beyond a default ATM window's edge. Width is a fixed generous margin
  covering the wings AND the add-on bracket (far wing - 2 increments), escalated on recorded
  refusals the flies/pmcc way.
- No ``history_days`` — nothing here reads a daily-bar series (all triggers are intraday).

Best-effort by design: a failed write must never break the paper loop.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.bwb import clock, db

_MODULE = "bwb"
_BASE_WINDOW_STRIKES = 30  # generous default: ~1.5% of SPX at $5 spacing, plus the add-on bracket
_log = logging.getLogger("bwb_paper_loop")


def wanted_expirations(conn, symbol: str, today: date, params: dict | None = None) -> dict[str, list[str]]:
    """The cache must hold: the current target expiration plus whatever is still open in the
    ledger (the daily ladder holds up to ~7 distinct expirations at steady state)."""
    dates: set[str] = set(db.open_leg_expirations(conn))
    plan = clock.target_expiration(today, params)
    if plan is not None:
        dates.add(plan["expiration"])
    return {symbol: sorted(dates)} if dates else {}


def window_strikes(conn) -> int:
    """Escalate the window on recorded `no_strikes_in_window` refusals — the flies/pmcc pattern,
    one-sided down (only ever grows within a process's lifetime)."""
    refusals = conn.execute(
        "SELECT COUNT(*) FROM bwb_entry_attempts WHERE outcome = 'no_strikes_in_window' "
        "AND trade_date >= date('now', '-5 day')"
    ).fetchone()[0]
    return _BASE_WINDOW_STRIKES + (10 * min(int(refusals or 0), 3))


def write(config: dict, conn, db_path: str, *, cache_path: str, today: date | None = None) -> Path:
    symbol = (config.get("symbol") or "SPX").strip().upper()
    today = today or clock.now_et().date()
    defaults = config.get("defaults") or {}
    leg_sources = [
        _sr.leg_source(
            db_path,
            "SELECT l.streamer_symbol FROM bwb_legs l JOIN bwb_positions p "
            "ON p.position_id = l.position_id WHERE l.status = 'open' AND p.status != 'closed'",
        )
    ]
    return _sr.write_request(
        _MODULE,
        [symbol],
        leg_sources=leg_sources,
        expirations=wanted_expirations(conn, symbol, today, defaults),
        window_hints={symbol: window_strikes(conn)},
    )


def register(config: dict, conn, db_path: str, *, cache_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, cache_path=cache_path, log=_log)
