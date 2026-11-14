"""Read-only views of the shared stream cache: a fund's usable quote and its previous posted close.

Opened `?mode=ro` through `cherrypick.core`, always; the streamer owns the cache. Every read refuses
rather than guesses: a stale or crossed quote is no quote (`core.streamcache.usable_quote`), and a
close that has not posted is no reference.

**The reference close** for session D is session D-1's posted close from `stream_summary`: D's own
row's `prev_day_close` first (the exchange's settle, written as D opens), else D-1's `day_close`,
which the summary backfill writes once D-1 is over. Never a last trade: the fund trades after the
bell, and its 20:00 print is not its close.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as _cal
from cherrypick.core import streamcache as _sc
from cherrypick.core.db import connect_ro as _connect_ro

DEFAULT_MAX_QUOTE_AGE_SECONDS = 120


def stream_cache_path(config: dict) -> str:
    return str(_sc.cache_path(config))


def quote(db_path, symbol: str, *, max_quote_age_seconds: float) -> dict | None:
    """The fund's usable quote ({bid, ask, mid, age_seconds}), or None."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = _connect_ro(path)
    try:
        quotes, _ = _sc.usable_quotes(conn, [symbol.strip().upper()], time.time(), max_quote_age_seconds)
    except Exception:  # noqa: BLE001 -- an unreadable cache is "no quote", and the tick waits
        return None
    finally:
        conn.close()
    return quotes.get(symbol.strip().upper())


def reference_close(db_path, symbol: str, session: date) -> float | None:
    """The posted close of the session before `session`, or None until it has posted."""
    path = Path(db_path)
    if not path.exists():
        return None
    prev = _cal.previous_trading_day(session)
    conn = _connect_ro(path)
    try:
        rows = {
            r["trade_date"]: r
            for r in conn.execute(
                "SELECT trade_date, day_close, prev_day_close FROM stream_summary "
                "WHERE symbol = ? AND trade_date IN (?, ?)",
                (symbol.strip().upper(), session.isoformat(), prev.isoformat()),
            )
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
    own = rows.get(session.isoformat())
    if own is not None and own["prev_day_close"] is not None and float(own["prev_day_close"]) > 0:
        return float(own["prev_day_close"])
    before = rows.get(prev.isoformat())
    if before is not None and before["day_close"] is not None and float(before["day_close"]) > 0:
        return float(before["day_close"])
    return None
