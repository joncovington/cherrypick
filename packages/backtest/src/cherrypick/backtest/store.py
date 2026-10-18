"""The vendor store: one SQLite file per dataset, in **the gex recorder's own schema**.

Why the recorder's schema: everything that already reads a recorded session -- `core.quotehistory`,
`flies.replay.RecordedSession`, `core.excursions` -- takes a `history_db` path. A vendor session written
in the same tables replays through the same code as a recorded one, with no second reader to drift.
The DDL below is the recorder's (`gex/service.py::_ensure_history_table`, `gex/regime.py` for
`daily_closes`); this package may not import gex, so `tests/test_store.py` holds the two equal by
reading gex's source.

One file per dataset rather than per month: the replay's trailing-range input reads earlier sessions
from the same file, and a month boundary would silently leave the first sessions of every month
without one. ~8 MB a session, so a few GB a dataset, which SQLite holds comfortably.

`vendor_sessions` is this store's own: one row per session attempted, complete or refused, with the
reason. A refusal is recorded, never written as an empty session.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import paths

#: The recorder's tables, verbatim in structure (column names, order, types, keys).
RECORDER_DDL = (
    "CREATE TABLE IF NOT EXISTS gex_spot_history ("
    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_gsh_sym_date ON gex_spot_history(symbol, trade_date)",
    "CREATE TABLE IF NOT EXISTS gex_profile_history ("
    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, strike REAL NOT NULL, "
    "call_oi REAL, put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL, put_gamma REAL, "
    "call_iv REAL, put_iv REAL)",
    "CREATE INDEX IF NOT EXISTS idx_gph_sym_ts ON gex_profile_history(symbol, ts)",
    "CREATE TABLE IF NOT EXISTS gex_quote_samples ("
    "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, "
    "expiration TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_gqs_sym_date_ts ON gex_quote_samples(symbol, trade_date, ts)",
    "CREATE TABLE IF NOT EXISTS gex_quotes ("
    "sample_id INTEGER NOT NULL, side INTEGER NOT NULL, strike REAL NOT NULL, "
    "bid REAL NOT NULL, ask REAL NOT NULL, mid REAL NOT NULL, age_s REAL, delta REAL, "
    "PRIMARY KEY (sample_id, side, strike)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS daily_closes ("
    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL, "
    "recorded_at REAL NOT NULL, source TEXT NOT NULL, "
    "PRIMARY KEY (symbol, trade_date))",
    # The recorder creates this with its first ten columns and adds the flow columns by ALTER;
    # the resulting table is spelt here in one statement, in the same column order.
    "CREATE TABLE IF NOT EXISTS gex_regime_history ("
    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, spot REAL, "
    "net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, "
    "expiration TEXT, call_volume REAL, put_volume REAL, total_call_gex_vol REAL, total_put_gex_vol REAL, "
    "call_wall_volume REAL, put_wall_volume REAL, volume_contracts REAL, volume_low_strike REAL, "
    "volume_high_strike REAL)",
    "CREATE INDEX IF NOT EXISTS idx_grh_sym_date ON gex_regime_history(symbol, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_grh_sym_ts ON gex_regime_history(symbol, ts)",
    # The recorder's market series (`gex/regime.py::ensure_tables`). The store holds one reading in it,
    # `vix`, a minute series from VIX options' underlying price (`vixfetch`), which MEIC's replay reads
    # at or before each tick through `core.regime` exactly as it reads the recorder's own.
    "CREATE TABLE IF NOT EXISTS market_regime_history ("
    "trade_date TEXT NOT NULL, ts REAL NOT NULL, reading TEXT NOT NULL, symbol TEXT, "
    "value REAL, basis_ts REAL, usable INTEGER NOT NULL, reason TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_mrh_date_reading ON market_regime_history(trade_date, reading)",
    "CREATE INDEX IF NOT EXISTS idx_mrh_ts ON market_regime_history(ts)",
)

VENDOR_DDL = (
    "CREATE TABLE IF NOT EXISTS vendor_sessions ("
    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, "
    "vendor TEXT NOT NULL, root TEXT NOT NULL, expiration TEXT, cadence_seconds REAL, "
    "samples INTEGER, quotes INTEGER, profiles INTEGER, fetched_at REAL NOT NULL, detail TEXT, "
    "PRIMARY KEY (symbol, trade_date))",
)

#: How `gex_quotes.side` encodes the right; the recorder's `QUOTE_SIDES`, read by core.quotehistory.
SIDES = {"PUT": 0, "CALL": 1}

COMPLETE = "complete"
REFUSED = "refused"


def cadence_of(settings: dict, cadence: float | None = None) -> int:
    return int(cadence if cadence is not None else settings["store"]["cadence_seconds"])


def db_path(settings: dict, symbol: str, cadence: float | None = None) -> Path:
    """One file per (dataset, cadence). Sessions at different cadences never share a file: the replay
    ticks once per stored sample, so a mixed file would replay some days at 15 s and some at 60 s."""
    return paths.vendor_dir(settings) / f"{symbol.upper()}-{cadence_of(settings, cadence)}s.db"


def connect(settings: dict, symbol: str, cadence: float | None = None) -> sqlite3.Connection:
    """Open (creating, with its marker directory) the dataset's store for writing."""
    paths.ensure_vendor_dir(settings)
    conn = sqlite3.connect(db_path(settings, symbol, cadence))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in RECORDER_DDL + VENDOR_DDL:
        conn.execute(stmt)
    conn.commit()
    return conn


def session_status(conn: sqlite3.Connection, symbol: str, day: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM vendor_sessions WHERE symbol = ? AND trade_date = ?", (symbol.upper(), day)
    ).fetchone()
    return row["status"] if row else None


def clear_session(conn: sqlite3.Connection, symbol: str, day: str) -> None:
    """Remove every row of one session, so a re-fetch never mixes two attempts."""
    sym = symbol.upper()
    conn.execute(
        "DELETE FROM gex_quotes WHERE sample_id IN "
        "(SELECT id FROM gex_quote_samples WHERE symbol = ? AND trade_date = ?)",
        (sym, day),
    )
    for table in (
        "gex_quote_samples",
        "gex_spot_history",
        "gex_profile_history",
        "gex_regime_history",
        "vendor_sessions",
    ):
        conn.execute(f"DELETE FROM {table} WHERE symbol = ? AND trade_date = ?", (sym, day))


def coverage(conn: sqlite3.Connection, symbol: str) -> dict:
    """Sessions by status, the span of complete ones, and every refusal with its reason."""
    sym = symbol.upper()
    rows = conn.execute(
        "SELECT trade_date, status, reason FROM vendor_sessions WHERE symbol = ? ORDER BY trade_date", (sym,)
    ).fetchall()
    complete = [r["trade_date"] for r in rows if r["status"] == COMPLETE]
    return {
        "symbol": sym,
        "complete": len(complete),
        "span": [complete[0], complete[-1]] if complete else None,
        "refused": [
            {"date": r["trade_date"], "reason": r["reason"]} for r in rows if r["status"] != COMPLETE
        ],
    }
