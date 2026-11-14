"""The prior session's Market Profile levels, for the morning pack. Record-only.

For SPX, NDX, /ES and /NQ: the last regular session before the pack's own (09:30-16:00 ET), its point
of control, value area, initial balance, high, low and close, whether its high and low were poor or
tailed, its single prints, and the untested POCs of the sessions before it. Read from the bars the
`profile-bars` job keeps (`~/.cherrypick/data/market-files/profile_bars.db`, read-only) and built by
`cherrypick.core.marketprofile`, so the numbers are the console's Profile tab's.

**Strictly before the session.** Only bars dated before the pack's session are read. The recorder runs
at 17:15 ET, so at 08:30 the store ends at the prior session's 17:00: an untested POC is untested
*through the prior session's close*, and an overnight touch is not seen here (the intraday chart sees
it). The block says so in `untested_through`.

No gate, phase or score reads this block.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import marketprofile as _mp

SYMBOLS = ("SPX", "NDX", "/ES", "/NQ")
# Sessions back for the untested POCs, and calendar days of bars read to reach them.
UNTESTED_SESSIONS = 20
LOOKBACK_DAYS = 45


def profile_bars_db() -> Path:
    return _home.data_dir("market-files") / "profile_bars.db"


def _bars(conn: sqlite3.Connection, symbol: str, session: str) -> list[dict]:
    since = (_dt.date.fromisoformat(session) - _dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT ts_ms, open, high, low, close, volume FROM profile_bars "
        "WHERE symbol = ? AND et_day >= ? AND et_day < ? ORDER BY ts_ms",
        (symbol, since, session),
    ).fetchall()
    return [{"t": ts / 1000.0, "o": o, "h": h, "l": lo, "c": c, "v": v} for ts, o, h, lo, c, v in rows]


def _symbol(symbol: str, bars: list[dict]) -> dict:
    dates = _mp.session_dates(bars)
    if not dates:
        return {"session": None, "reason": "no_session_recorded"}
    prior = dates[-1]
    row = _mp.row_size_for(symbol, bars[-1]["c"])
    p = _mp.build_profile(bars, row_size=row, bar_seconds=60, session=prior)
    untested = _mp.untested_pocs(
        bars, sessions=dates[-(UNTESTED_SESSIONS + 1) : -1], row_size=row, bar_seconds=60
    )
    return {
        "session": prior.isoformat(),
        "basis": "prior",
        "row_size": row,
        "periods": p.periods,
        "poc": p.poc,
        # The value area by its rows' outer edges: VAL's lower edge, VAH's upper edge.
        "val": p.val,
        "vah": round(p.vah + row, 10),
        "ib_low": p.ib_low,
        "ib_high": p.ib_high,
        "high": p.high,
        "low": p.low,
        "close": p.close,
        "poor_high": p.poor_high,
        "poor_low": p.poor_low,
        "tail_high": p.tail_high,
        "tail_low": p.tail_low,
        "single_prints": len(p.single_prints),
        "volume_poc": p.volume_poc,
        "untested_pocs": [{"session": d.isoformat(), "poc": poc} for d, poc in untested],
        "untested_through": prior.isoformat(),
        "reason": None,
    }


def block_for(session: str, path: Path | None = None) -> dict:
    """The `profile` block for a pack of `session` (ISO date)."""
    path = path or profile_bars_db()
    if not path.exists():
        return {"source": "profile_bars", "symbols": {}, "reason": "no_profile_bars"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return {"source": "profile_bars", "symbols": {}, "reason": f"unreadable: {exc}"}
    try:
        symbols = {}
        for sym in SYMBOLS:
            try:
                symbols[sym] = _symbol(sym, _bars(conn, sym, session))
            except sqlite3.Error as exc:
                symbols[sym] = {"session": None, "reason": f"unreadable: {exc}"}
    finally:
        conn.close()
    return {"source": "profile_bars", "symbols": symbols, "reason": None}
