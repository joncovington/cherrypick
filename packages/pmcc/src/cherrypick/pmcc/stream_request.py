"""Declare this module's stream needs: its underlyings, open legs, expirations, and window widths.

Writes ``~/.cherrypick/state/stream_requests/pmcc.json``. Four fields matter here:

- ``symbols`` — the underlyings (TNA, TQQQ, UPRO), for spot and the session summary (the keltner
  gate and the daily-bar mirror both live off ``stream_summary``).
- ``leg_sources`` — one SELECT over this module's own ledger returning every open leg's streamer
  symbol, re-run by the producer every subscription poll, so a filled entry is subscribed within a
  poll and a closed leg ages out without a restart — and a deep leg, once OPEN, stays quoted
  regardless of the ATM window.
- ``expirations`` — the two computed dates (~9DTE short, ~21DTE long) plus every expiration still
  held open. Derived from DATES only (never the clock), so the value changes exactly at an ET date
  boundary — never a mid-session subscription change.
- ``window_hints`` — LOAD-BEARING here, unlike most modules: the 99-delta long lives 30–45% below
  spot, far outside any default ATM window, so entry-time quotes for it exist only if the producer
  honors the widened per-symbol window ``stream_window.py`` computes and escalates.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol or
date is a data-availability problem the provider already surfaces as a refusal, not a crash.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.pmcc import clock, db, provider, stream_window

_MODULE = "pmcc"
_log = logging.getLogger("pmcc_paper_loop")


def wanted_expirations(
    conn, symbols: list[str], today: date, params: dict | None = None
) -> dict[str, list[str]]:
    """Per-symbol expiration dates the cache must hold: the current plan's short/long pair plus
    whatever is still open in the ledger (a rolled short or a surviving long outlives the plan)."""
    dates: set[str] = set(db.open_leg_expirations(conn))
    plan = clock.expiration_plan(today, params)
    if plan is not None:
        dates.add(plan["short_expiration"])
        dates.add(plan["long_expiration"])
    return {symbol: sorted(dates) for symbol in symbols} if dates else {}


def write(config: dict, conn, db_path: str, *, cache_path: str, today: date | None = None) -> Path:
    symbols = [s.strip().upper() for s in (config.get("symbols") or ["TNA", "TQQQ", "UPRO"])]
    today = today or clock.now_et().date()
    defaults = config.get("defaults") or {}
    leg_sources = [
        {
            "db": db_path,
            "query": (
                "SELECT l.streamer_symbol FROM pmcc_legs l JOIN pmcc_positions p "
                "ON p.position_id = l.position_id "
                "WHERE l.status = 'open' AND p.status != 'closed'"
            ),
        }
    ]
    hints = stream_window.hints_for_symbols(
        conn,
        cache_path,
        symbols,
        today.isoformat(),
        config,
        deep_window_pct=defaults.get("deep_window_pct", provider.DEFAULT_DEEP_WINDOW_PCT),
    )
    return _sr.write_request(
        _MODULE,
        symbols,
        leg_sources=leg_sources,
        window_hints=hints,
        expirations=wanted_expirations(conn, symbols, today, defaults),
    )


def register(config: dict, conn, db_path: str, *, cache_path: str) -> None:
    """Best-effort: never raises into the caller."""
    try:
        write(config, conn, db_path, cache_path=cache_path)
    except Exception as exc:  # noqa: BLE001 — registration is advisory, never fatal to the loop
        _log.warning("stream request registration failed: %s", exc)
