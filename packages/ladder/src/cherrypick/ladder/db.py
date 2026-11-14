"""The ladder paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/ladder/paper_trades.db` (the suite's convention, which the read
side resolves as `<data home>/<module>/paper_trades.db`).

**A position is one lot**: one arm, one fund, bought once at a limit and sold once at its target.
Each arm keeps its own book; two arms never share a lot. `position_id` is
`<arm>:<symbol>:<buy session>:<n>`, `n` counting that arm's lots on that fund that session.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per lot. Money is signed cash flow, whole-position dollars: entry_value is the purchase
-- (-), exit_value the sale (+), entry_value + exit_value = gross_pnl;
-- gross_pnl - fees - settlement - slippage = net_pnl. Fills are at the limit, so slippage is 0 by
-- construction and settlement is 0 for shares.
CREATE TABLE IF NOT EXISTS ladder_positions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL UNIQUE,
    arm              TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    entry_rule       TEXT NOT NULL,
    target_rule      TEXT NOT NULL,
    quantity         INTEGER NOT NULL,
    ref_close        REAL,
    chained_from     REAL,
    buy_level        REAL NOT NULL,
    buy_price        REAL NOT NULL,
    buy_session      TEXT NOT NULL,
    buy_time         TEXT NOT NULL,
    buy_bid          REAL,
    buy_ask          REAL,
    status           TEXT NOT NULL DEFAULT 'open',
    target_price     REAL,
    target_stage     TEXT,
    low_bid          REAL,
    low_bid_time     TEXT,
    last_bid         REAL,
    last_mark_time   TEXT,
    sell_price       REAL,
    sell_session     TEXT,
    sell_time        TEXT,
    sell_bid         REAL,
    sell_ask         REAL,
    exit_reason      TEXT,
    hold_days        INTEGER,
    entry_value      REAL NOT NULL,
    exit_value       REAL,
    gross_pnl        REAL,
    fees             REAL,
    settlement       REAL,
    slippage         REAL,
    net_pnl          REAL,
    net_pct          REAL,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ladder_positions_status ON ladder_positions(status);
CREATE INDEX IF NOT EXISTS idx_ladder_positions_arm ON ladder_positions(arm, symbol, buy_session);

-- The collapsed narrative journal (flies' shape).
CREATE TABLE IF NOT EXISTS ladder_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    arm         TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    accepted    INTEGER NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_ts    TEXT,
    last_ts     TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ladder_decisions_date ON ladder_decisions(trade_date);

-- The feed ledger: one row per tick per fund, refusals included.
CREATE TABLE IF NOT EXISTS ladder_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    bid           REAL,
    ask           REAL,
    ref_close     REAL
);
CREATE INDEX IF NOT EXISTS idx_ladder_snapshots_date ON ladder_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS ladder_loop_iterations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at         REAL NOT NULL,
    session_date   TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL,
    open_positions INTEGER,
    marks_written  INTEGER,
    actions_taken  INTEGER,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_ladder_iterations_session ON ladder_loop_iterations(session_date, ran_at);

-- Dates across which results must never be pooled.
CREATE TABLE IF NOT EXISTS measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    recorded_at REAL,
    UNIQUE(break_date, key)
);
"""

# Columns added after the first release, per table. Empty at birth.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {"ladder_positions": {}}

_ENV = "LADDER_DB_PATH"


def default_db_path() -> str:
    return os.path.join(str(_home.home()), "data", "ladder", "paper_trades.db")


def resolve_path(db_path: str | None = None) -> str:
    return db_path or os.environ.get(_ENV) or default_db_path()


_store = _ledgerstore.LedgerStore("ladder_", _SCHEMA, _ADDED_COLUMNS)

stale_writer_columns = _store.stale_writer_columns
save_position = _store.save_position
record_decision = _store.record_decision
record_snapshot = _store.record_snapshot
record_iteration = _store.record_iteration
record_measurement_break = _store.record_measurement_break


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger for writing. WAL + NORMAL, matching every other module ledger."""
    conn = _core_db.connect(resolve_path(db_path), pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _store.migrate(conn)
    return conn


def connect_ro(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger READ-ONLY, for the read side: never migrates, never creates."""
    return _core_db.connect_ro(resolve_path(db_path))


def position(conn, position_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM ladder_positions WHERE position_id = ?", (position_id,)).fetchone()
    return dict(r) if r else None


def open_lots(conn, arm: str | None = None, symbol: str | None = None) -> list[dict]:
    """Open lots, oldest first."""
    sql, args = "SELECT * FROM ladder_positions WHERE status = 'open'", []
    if arm is not None:
        sql += " AND arm = ?"
        args.append(arm)
    if symbol is not None:
        sql += " AND symbol = ?"
        args.append(symbol)
    return [dict(r) for r in conn.execute(sql + " ORDER BY buy_time, id", args)]


def positions(
    conn, arm: str | None = None, *, status: str | None = None, limit: int | None = None
) -> list[dict]:
    sql, where, args = "SELECT * FROM ladder_positions", [], []
    if arm is not None:
        where.append("arm = ?")
        args.append(arm)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY buy_time DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


def open_count(conn, arm: str | None = None) -> int:
    sql, args = "SELECT COUNT(*) FROM ladder_positions WHERE status = 'open'", []
    if arm is not None:
        sql += " AND arm = ?"
        args.append(arm)
    return int(conn.execute(sql, args).fetchone()[0])


def bought_in_session(conn, arm: str, symbol: str, session: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM ladder_positions WHERE arm = ? AND symbol = ? AND buy_session = ?",
            (arm, symbol, session),
        ).fetchone()[0]
    )


def open_lot(conn, row: dict) -> None:
    _store.upsert(conn, "ladder_positions", ("position_id",), row)


def measurement_breaks(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date, key")]
