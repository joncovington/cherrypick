"""Read-only access to the suite's **shared** stream cache
(`cherrypick.core.streamcache`, `~/.cherrypick/data/marketdata/stream_cache.db`) -- the standalone
streamer daemon's output, when that daemon happens to be running. This is the suite's "streamer
before API calls" rule applied to scout: `quote_service` checks this cache first for anything it
carries and only falls back to a direct broker REST call for a symbol that's missing or stale here.

Read-only, never writes, never imports `tastytrade` -- scout is a reader of this cache, never a
producer (same posture MEIC's and flies' readers take; the neutral `marketdata` scope, not
`data/scout`, is what marks it as shared rather than module-owned). The streamer not running, or not
yet having a fresh row for a given symbol, is a normal and expected state, never an error: every
function here degrades to "nothing found" rather than raising, so a caller's REST fallback is always
safe to reach for.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from cherrypick.core import home as _home


def cache_path() -> Path:
    """The shared stream cache's canonical location."""
    return _home.data_dir("marketdata") / "stream_cache.db"


def open_ro(path: Path | None = None) -> sqlite3.Connection | None:
    """A read-only connection to the shared cache, or `None` if it doesn't exist / can't be opened.
    A stream cache built by a producer with a newer/older schema still opens fine here -- the
    `SELECT`s below name only columns that have existed in this table since it was introduced."""
    path = path or cache_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def read_equity_quotes(
    conn: sqlite3.Connection, symbols: list[str], max_age_seconds: float, *, now: float | None = None
) -> dict[str, dict]:
    """`{symbol: {"bid","ask","mid","last","change_pct"}}` for every requested symbol whose
    `stream_trades`/`stream_quotes` row is fresher than `max_age_seconds`. A symbol with no row, or
    only a stale one, is simply absent from the result -- the caller falls back to REST for exactly
    the symbols this didn't cover, never for the whole batch."""
    now = time.time() if now is None else now
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)

    try:
        trades = {
            row["symbol"]: row
            for row in conn.execute(
                f"SELECT symbol, last, change, updated_at FROM stream_trades "
                f"WHERE symbol IN ({placeholders})",
                wanted,
            )
            if (now - row["updated_at"]) <= max_age_seconds
        }
        quotes = {
            row["symbol"]: row
            for row in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes "
                f"WHERE symbol IN ({placeholders})",
                wanted,
            )
            if (now - row["updated_at"]) <= max_age_seconds
        }
    except sqlite3.Error:
        return {}

    result: dict[str, dict] = {}
    for symbol in trades.keys() | quotes.keys():
        trade = trades.get(symbol)
        quote = quotes.get(symbol)
        last = trade["last"] if trade else None
        change = trade["change"] if trade else None
        prev_close = (last - change) if last is not None and change is not None else None
        result[symbol] = {
            "bid": quote["bid"] if quote else None,
            "ask": quote["ask"] if quote else None,
            "mid": quote["mid"] if quote else None,
            "mark": quote["mid"] if quote else None,
            "last": last,
            "change_pct": (change / prev_close) if change is not None and prev_close else None,
        }
    return result
