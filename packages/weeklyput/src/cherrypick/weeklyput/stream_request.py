"""Declare this module's stream needs: the four names, the cycle's expiry, the puts it may sell.

Writes `~/.cherrypick/state/stream_requests/weeklyput.json`. The request is narrow on purpose (the
subscription budget, `packages/streamer/CLAUDE.md`):

- `symbols`: SPX, SPY, QQQ, IWM, for spot and the session's posted close (`stream_summary`).
  `nearest_window` is false for each: this module never reads the nearest expiry as such.
- `expirations`: the next cycle's expiry, only from the session before its entry session through
  the entry session (warm chain metadata and an at-the-money window, both rights, for the parity
  forward), plus any expiry still held. Derived from dates only, so it changes at an ET date
  boundary, never mid-session.
- `legs`: on the entry session, the listed puts `put_band` below spot (1.5% to 12% by default).
  The default window is 30 strikes a side, about 2% on SPX, which does not reach a 7-day 10-delta
  put; a per-symbol `window_hints` would widen every SPX window in the suite, 0DTE included, so
  the puts are named one by one instead, on the one session they are read.
- `leg_sources`: every open put, from this module's own ledger, so marks continue to expiry.
- `window_events`: Quote only. Delta is computed here from mids on the parity forward (item 46's
  rule), so the streamer's greeks are never read.

Best-effort: a failed write never breaks the loop. A missing chain is the provider's refusal.
"""

from __future__ import annotations

import logging
from datetime import date

from cherrypick.core import calendar as _cal
from cherrypick.core import streamrequests as _sr

from cherrypick.weeklyput import clock, db, provider

_MODULE = "weeklyput"
_log = logging.getLogger("weeklyput_paper_loop")

DEFAULT_PUT_BAND = (0.015, 0.12)


def names(config: dict) -> dict[str, dict]:
    """{symbol: {root, fee_symbol, settlement}} for every configured name."""
    out = {}
    for sym, block in (config.get("names") or {}).items():
        if str(sym).startswith("_") or not isinstance(block, dict) or block.get("enabled") is False:
            continue
        s = str(sym).strip().upper()
        out[s] = {
            "root": str(block.get("root") or s).upper(),
            "fee_symbol": str(block.get("fee_symbol") or s).upper(),
            "settlement": str(block.get("settlement") or ""),
        }
    return out


def put_band(config: dict) -> tuple[float, float]:
    band = (config.get("defaults") or {}).get("put_band") or DEFAULT_PUT_BAND
    return float(band[0]), float(band[1])


def wanted(conn, config: dict, cache_path: str, today: date) -> dict:
    """{"expirations": {sym: [dates]}, "legs": [streamer symbols]}: pure over the ledger and cache."""
    syms = names(config)
    dates: set[str] = set(db.open_expirations(conn))
    legs: list[str] = []
    cycle = clock.next_cycle(today)
    if cycle is not None:
        entry = clock.entry_session(cycle)
        if _cal.previous_trading_day(entry) <= today <= entry:
            dates.add(cycle.isoformat())
        if today == entry:
            band = put_band(config)
            for sym, spec in syms.items():
                spot = provider.read_spot(cache_path, sym)
                if spot:
                    legs += provider.band_put_symbols(
                        cache_path, sym, cycle.isoformat(), root=spec["root"], spot=spot, band=band
                    )
    return {
        "expirations": {sym: sorted(dates) for sym in syms} if dates else {},
        "legs": sorted(set(legs)),
    }


def write(config: dict, conn, db_path: str, *, cache_path: str, today: date | None = None):
    today = today or clock.now_et().date()
    syms = list(names(config))
    need = wanted(conn, config, cache_path, today)
    return _sr.write_request(
        _MODULE,
        syms,
        legs=need["legs"],
        leg_sources=[
            _sr.leg_source(db_path, "SELECT streamer_symbol FROM weeklyput_positions WHERE status = 'open'")
        ],
        expirations=need["expirations"],
        window_events={s: ["Quote"] for s in syms},
        nearest_window={s: False for s in syms},
    )


def register(config: dict, conn, db_path: str, *, cache_path: str) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, conn, db_path, cache_path=cache_path, log=_log)
