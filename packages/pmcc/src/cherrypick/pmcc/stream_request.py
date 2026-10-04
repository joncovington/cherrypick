"""Declare this module's stream needs: its underlying, open legs, expirations, and window widths.

Writes ``~/.cherrypick/state/stream_requests/pmcc.json``. Three fields matter here:

- ``symbols`` — the configured symbols, plus any symbol still holding an open position, for spot.
- ``leg_sources`` — one SELECT over this module's own ledger returning every open leg's streamer
  symbol, re-run by the producer every subscription poll, so a filled entry is subscribed within a
  poll and a closed leg ages out without a restart — and a deep leg, once OPEN, stays quoted
  regardless of the ATM window.
- ``expirations`` — the two computed dates (~7DTE short, ~21DTE long) plus every expiration still
  held open. Derived from DATES only (never the clock), so the value changes exactly at an ET date
  boundary — never a mid-session subscription change.
- ``window_hints`` — LOAD-BEARING here, unlike most modules: the 85-90-delta long lives noticeably
  below spot, outside any default ATM window, so entry-time quotes for it exist only if the
  producer honors the widened per-symbol window ``stream_window.py`` computes and escalates.

Best-effort by design: a failed write must never break the paper loop. An unregistered symbol or
date is a data-availability problem the provider already surfaces as a refusal, not a crash.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from cherrypick.core import streamrequests as _sr

from cherrypick.pmcc import clock, db, stream_window

_MODULE = "pmcc"
_log = logging.getLogger("pmcc_paper_loop")


def wanted_expirations(
    conn,
    symbols: list[str],
    today: date,
    params: dict | None = None,
    *,
    entry_symbols: list[str] | None = None,
) -> dict[str, list[str]]:
    """Per-symbol expiration dates the cache must hold: the current plan's short/long pair for a
    symbol the module still ENTERS (`entry_symbols`, default all of `symbols`), plus whatever that
    symbol's OWN open legs hold (a surviving long outlives the plan).

    Per symbol since 2026-10-04. Before, every symbol was handed the union of every symbol's dates,
    so one symbol's open long asked for a window on every other symbol -- and once longs sit on
    LEAP months that differ per symbol, for dates the other symbol does not even list, which the
    producer records as an error on every pass."""
    plan = clock.expiration_plan(today, params)
    planned = {plan["short_expiration"], plan["long_expiration"]} if plan is not None else set()
    entering = {s.upper() for s in (entry_symbols if entry_symbols is not None else symbols)}
    out: dict[str, list[str]] = {}
    for symbol in symbols:
        dates = set(db.open_leg_expirations_for(conn, symbol))
        if symbol.upper() in entering:
            dates |= planned
        if dates:
            out[symbol] = sorted(dates)
    return out


def write(config: dict, conn, db_path: str, *, cache_path: str, today: date | None = None) -> Path:
    entry_symbols = [s.strip().upper() for s in (config.get("symbols") or ["TQQQ"])]
    # A symbol retired from the config keeps its spot, its quotes and its own dates until its last
    # position closes -- otherwise its open legs could neither be marked nor managed out.
    symbols = entry_symbols + [s for s in db.open_position_symbols(conn) if s not in entry_symbols]
    today = today or clock.now_et().date()
    defaults = config.get("defaults") or {}
    leg_sources = [
        _sr.leg_source(
            db_path,
            "SELECT l.streamer_symbol FROM pmcc_legs l JOIN pmcc_positions p "
            "ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND p.status != 'closed'",
        )
    ]
    # The roster and the cap decide whether a widened window is worth subscribing at all: a symbol
    # every arm already holds cannot be entered until one closes — one to two WEEKS for a
    # hold-to-expiration cycle — and the open position's marks come from `leg_sources`, never from
    # the window. See `stream_window.hints_for_symbols`.
    # The SAME roster the entry phase uses, advised twin included — `session_books` IS that roster.
    # Passing `engine.ARMS` alone asked "can control still enter?" and dropped the widened window
    # the moment control filled, starving the advised twin that was still trying: control took XSP
    # on 2026-08-24 and the twin then recorded 658 `no_deep_itm_long` refusals across 08-25/08-26.
    from cherrypick.pmcc import paper_loop as _paper_loop  # circular at module scope

    arms, _ = _paper_loop.session_books(config, today.isoformat())
    hints = stream_window.hints_for_symbols(
        conn,
        cache_path,
        entry_symbols,
        today.isoformat(),
        config,
        arms=arms,
        max_positions=int(defaults.get("max_positions", 3)),
    )
    return _sr.write_request(
        _MODULE,
        symbols,
        leg_sources=leg_sources,
        window_hints=hints,
        expirations=wanted_expirations(conn, symbols, today, defaults, entry_symbols=entry_symbols),
        # What the loop reads off its windows (audited 2026-09-30): call quotes and greeks on the
        # plan's short and long dates -- the ATM short and the delta-band deep-ITM long. Never the
        # nearest expiration as such (a plan date that coincides with it is served as a requested
        # date), never option trades. Open interest is read only by the `calibrate` CLI's ladder
        # (`provider.ladder_snapshot`), an informational column, which now reads empty for these
        # symbols; that is the price of ~2,900 fewer subscriptions across TQQQ and XSP.
        window_events={s: ["Quote", "Greeks"] for s in symbols},
        nearest_window={s: False for s in symbols},
    )


def register(config: dict, conn, db_path: str, *, cache_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, cache_path=cache_path, log=_log)
