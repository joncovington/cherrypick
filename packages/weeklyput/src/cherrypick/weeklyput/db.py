"""The weeklyput paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/weeklyput/paper_trades.db` (the suite's convention, which the
read side resolves as `<data home>/<module>/paper_trades.db`).

**A position is one short put**: one arm, one name, one cycle (expiry), entered once and held to
the expiry's posted close. `weeklyput_entries` is the cycle's register: one row per (arm, name,
expiry), whether it entered, was refused to the end of its window (`missed`) or is still pending,
so a cycle the arm could not trade is a row and never a silence. The per-cycle result the module
is judged on (the equal-weight mean of net in bp over the names that entered) is read from these
two tables.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per short put. `position_id` = "<arm>:<symbol>:<expiration>". Money is signed cash
-- flow, whole-position dollars: entry_value is the credit (+), exit_value the settlement value (-),
-- entry_value + exit_value = gross_pnl; gross_pnl - fees - settlement - slippage = net_pnl.
-- net_bp is net over the entry forward notional (100 x F x quantity), item 57's measure.
CREATE TABLE IF NOT EXISTS weeklyput_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    arm                 TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    root                TEXT NOT NULL,
    settlement_style    TEXT NOT NULL,
    expiration          TEXT NOT NULL,
    entry_session       TEXT NOT NULL,
    entry_time          TEXT,
    streamer_symbol     TEXT NOT NULL,
    occ_symbol          TEXT,
    strike              REAL NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    entry_spot          REAL,
    entry_forward       REAL NOT NULL,
    entry_years         REAL,
    entry_bid           REAL,
    entry_ask           REAL,
    entry_mid           REAL,
    entry_iv            REAL,
    entry_delta         REAL,
    neighbours_json     TEXT,
    entry_value         REAL NOT NULL,
    entry_fees          REAL NOT NULL DEFAULT 0,
    entry_slippage      REAL NOT NULL DEFAULT 0,
    notional            REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    settle_session      TEXT,
    settle_time         TEXT,
    settle_price        REAL,
    settle_source       TEXT,
    exit_value          REAL,
    exit_reason         TEXT,
    settlement          REAL,
    gross_pnl           REAL,
    fees                REAL,
    slippage            REAL,
    net_pnl             REAL,
    net_bp              REAL,
    worst_mark_value    REAL,
    worst_mark_time     TEXT,
    assignment_exposed_at TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_weeklyput_positions_status ON weeklyput_positions(status);
CREATE INDEX IF NOT EXISTS idx_weeklyput_positions_expiry ON weeklyput_positions(expiration);

-- The cycle register: one row per (arm, symbol, expiration) the calendar asked this arm to sell.
-- outcome: pending (window not over), entered, missed (the window closed unacted; `refusal` is the
-- last reason a tick in it gave, or no_tick_in_window).
CREATE TABLE IF NOT EXISTS weeklyput_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arm            TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    expiration     TEXT NOT NULL,
    entry_session  TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    refusal        TEXT,
    position_id    TEXT,
    decided_at     TEXT,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(arm, symbol, expiration)
);

-- The open put's marks: one row per marking tick (every `mark_interval_minutes`, and the decision
-- window). A refused mark is still a row (usable = 0 with the refusal).
CREATE TABLE IF NOT EXISTS weeklyput_marks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    TEXT NOT NULL,
    ts             TEXT NOT NULL,
    session_date   TEXT NOT NULL,
    usable         INTEGER NOT NULL DEFAULT 0,
    refusal        TEXT,
    bid            REAL,
    ask            REAL,
    mid            REAL,
    spot           REAL,
    mark_value     REAL,
    extrinsic      REAL
);
CREATE INDEX IF NOT EXISTS idx_weeklyput_marks_position ON weeklyput_marks(position_id, ts);

-- The collapsed narrative journal (flies' shape).
CREATE TABLE IF NOT EXISTS weeklyput_decisions (
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
CREATE INDEX IF NOT EXISTS idx_weeklyput_decisions_date ON weeklyput_decisions(trade_date);

-- The feed ledger: one row per decision-window tick per name, refusals included.
CREATE TABLE IF NOT EXISTS weeklyput_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    quotes_fresh  INTEGER,
    quotes_stale  INTEGER,
    spot          REAL
);
CREATE INDEX IF NOT EXISTS idx_weeklyput_snapshots_date ON weeklyput_snapshots(trade_date);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS weeklyput_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_weeklyput_iterations_session
    ON weeklyput_loop_iterations(session_date, ran_at);

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
_ADDED_COLUMNS: dict[str, dict[str, str]] = {"weeklyput_positions": {}}

_ENV = "WEEKLYPUT_DB_PATH"


def default_db_path() -> str:
    return os.path.join(str(_home.home()), "data", "weeklyput", "paper_trades.db")


def resolve_path(db_path: str | None = None) -> str:
    return db_path or os.environ.get(_ENV) or default_db_path()


_store = _ledgerstore.LedgerStore("weeklyput_", _SCHEMA, _ADDED_COLUMNS)

_upsert = _store.upsert
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


# --------------------------------------------------------------------------- positions
def position(conn, position_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM weeklyput_positions WHERE position_id = ?", (position_id,)).fetchone()
    return dict(r) if r else None


def open_positions(conn, arm: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM weeklyput_positions WHERE status = 'open'", []
    if arm is not None:
        sql += " AND arm = ?"
        args.append(arm)
    return [dict(r) for r in conn.execute(sql + " ORDER BY expiration, symbol, arm", args)]


def positions(conn, arm: str | None = None, *, limit: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM weeklyput_positions", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    sql += " ORDER BY expiration DESC, symbol, arm"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


def open_position_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM weeklyput_positions WHERE status = 'open'").fetchone()[0])


def open_streamer_symbols(conn) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT streamer_symbol FROM weeklyput_positions WHERE status = 'open' ORDER BY 1"
        )
    ]


def open_expirations(conn) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT expiration FROM weeklyput_positions WHERE status = 'open' ORDER BY 1"
        )
    ]


# --------------------------------------------------------------------------- the cycle register
def entry(conn, arm: str, symbol: str, expiration: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM weeklyput_entries WHERE arm = ? AND symbol = ? AND expiration = ?",
        (arm, symbol, expiration),
    ).fetchone()
    return dict(r) if r else None


def save_entry(conn, row: dict, *, commit: bool = True) -> None:
    _store.upsert(conn, "weeklyput_entries", ("arm", "symbol", "expiration"), row, commit=commit)


def entries(conn, *, arm: str | None = None, limit: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM weeklyput_entries", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    sql += " ORDER BY expiration DESC, symbol, arm"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


def open_new_position(conn, *, position_row: dict, entry_row: dict) -> None:
    """The position and its cycle row in one transaction: an entered cycle with no position (or a
    position whose cycle still reads pending) would be a register that does not add up."""
    try:
        _store.upsert(conn, "weeklyput_positions", ("position_id",), position_row, commit=False)
        save_entry(conn, entry_row, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- marks
def record_mark(conn, **fields) -> None:
    """Telemetry: swallowed on failure, like every LedgerStore telemetry writer."""
    _store.record_mark(conn, **fields)


def last_mark_ts(conn, position_id: str) -> str | None:
    r = conn.execute(
        "SELECT ts FROM weeklyput_marks WHERE position_id = ? ORDER BY ts DESC LIMIT 1", (position_id,)
    ).fetchone()
    return r["ts"] if r else None


def marks(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM weeklyput_marks WHERE position_id = ? ORDER BY ts", (position_id,)
        )
    ]


def measurement_breaks(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date, key")]
