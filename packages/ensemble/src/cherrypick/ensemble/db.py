"""The ensemble paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/ensemble/paper_trades.db` (the suite's convention, which the read
side resolves as `<data home>/<module>/paper_trades.db`).

**A position is one structure**: one arm, one entry session, entered once and closed by the arm's exit
(a touch, a profit target, a time exit) or held to the expiry's posted close. `ensemble_legs` holds its
legs (one row each, with the streamer symbol the stream request keeps subscribed while the position is
open). `ensemble_entries` is the register of what each arm was asked to do each session, so a session an
arm could not trade is a row and never a silence. `ensemble_trail` is the loop's own one-minute spot
record: the stream cache keeps only the latest print, and the opening range and the intraday RSI and
stochastic read the day's path.

Money follows `docs/conventions/money-layout.md`: signed cash flow in whole-position dollars,
`entry_value + exit_value = gross_pnl` and `gross_pnl - fees - settlement - slippage = net_pnl`, each cost
in its own column. `net_pnl` is net at the suite's slippage model (the verdict fill); `net_stress` (halfway
from mid to the natural fill) and `net_mid` are context.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per structure. `position_id` = "<arm>:<entry_session>" (an arm enters at most once a session).
-- Money is signed cash flow, whole-position dollars: entry_value is the credit (+) or debit (-) at
-- the mid, exit_value what closing brought (+) or cost (-) at the mid or the settlement intrinsic,
-- entry_value + exit_value = gross_pnl; gross_pnl - fees - settlement - slippage = net_pnl.
-- `entry_json` is the engine's Entry, so a close is priced by the code that opened it; `exit_json` is the
-- arm's exit rules as they stood at entry, so a position is managed by the rules it was entered under.
CREATE TABLE IF NOT EXISTS ensemble_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL UNIQUE,
    arm                 TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    root                TEXT,
    entry_session       TEXT NOT NULL,
    entry_minute        INTEGER,
    entry_time          TEXT,
    expiration          TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    entry_spot          REAL,
    entry_mid           REAL,
    max_loss            REAL,
    max_profit          REAL,
    capital             REAL,
    entry_value         REAL NOT NULL,
    entry_fees          REAL NOT NULL DEFAULT 0,
    entry_slippage      REAL NOT NULL DEFAULT 0,
    entry_json          TEXT NOT NULL,
    exit_json           TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    exit_reason         TEXT,
    exit_session        TEXT,
    exit_minute         INTEGER,
    exit_time           TEXT,
    exit_spot           REAL,
    exit_mid            REAL,
    exit_value          REAL,
    settle_price        REAL,
    settle_source       TEXT,
    gross_pnl           REAL,
    fees                REAL,
    settlement          REAL,
    slippage            REAL,
    net_pnl             REAL,
    slippage_stress     REAL,
    net_stress          REAL,
    net_mid             REAL,
    worst_mark_value    REAL,
    worst_mark_time     TEXT,
    best_mark_value     REAL,
    best_mark_time      TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ensemble_positions_status ON ensemble_positions(status);
CREATE INDEX IF NOT EXISTS idx_ensemble_positions_arm ON ensemble_positions(arm, entry_session);

-- One row per leg of a position. `leg_role` is the arm's own name for it (short_put, long_call, ...).
-- side: +1 bought, -1 sold. status follows the position (open until it closes).
CREATE TABLE IF NOT EXISTS ensemble_legs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    leg_role         TEXT NOT NULL,
    right            TEXT NOT NULL,
    side             INTEGER NOT NULL,
    strike           REAL NOT NULL,
    streamer_symbol  TEXT,
    occ_symbol       TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, leg_role)
);
CREATE INDEX IF NOT EXISTS idx_ensemble_legs_status ON ensemble_legs(status);

-- The session register: one row per (arm, symbol, entry_session) an arm's weekday asked it to trade.
-- outcome: pending (its entry window is not over), entered, or missed (the window closed with no entry;
-- `refusal` is the last reason a tick in it gave, or no_tick_in_window).
CREATE TABLE IF NOT EXISTS ensemble_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arm            TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    entry_session  TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    refusal        TEXT,
    detail         TEXT,
    position_id    TEXT,
    decided_at     TEXT,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(arm, symbol, entry_session)
);

-- An open position's marks: one row per marking tick. A refused mark is still a row (usable = 0).
-- mark_value is what closing would bring at the mid, whole-position dollars, signed.
CREATE TABLE IF NOT EXISTS ensemble_marks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    TEXT NOT NULL,
    ts             TEXT NOT NULL,
    session_date   TEXT NOT NULL,
    usable         INTEGER NOT NULL DEFAULT 0,
    refusal        TEXT,
    spot           REAL,
    mark_value     REAL
);
CREATE INDEX IF NOT EXISTS idx_ensemble_marks_position ON ensemble_marks(position_id, ts);

-- The collapsed narrative journal (flies' shape): one row per (date, arm, mode, reason) with a count.
-- `reason` is the engine's first failing check ("gate:sd_change", "entered_today", ...) or "enter";
-- `detail` is the value that check read, as JSON.
CREATE TABLE IF NOT EXISTS ensemble_decisions (
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
CREATE INDEX IF NOT EXISTS idx_ensemble_decisions_date ON ensemble_decisions(trade_date);

-- The feed ledger: one row per decision tick per symbol, refusals included.
CREATE TABLE IF NOT EXISTS ensemble_snapshots (
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
CREATE INDEX IF NOT EXISTS idx_ensemble_snapshots_date ON ensemble_snapshots(trade_date);

-- The loop's own one-minute spot record (the cache keeps only the last print).
CREATE TABLE IF NOT EXISTS ensemble_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    minute      INTEGER NOT NULL,
    spot        REAL NOT NULL,
    UNIQUE(symbol, trade_date, minute)
);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS ensemble_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_ensemble_iterations_session
    ON ensemble_loop_iterations(session_date, ran_at);

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
_ADDED_COLUMNS: dict[str, dict[str, str]] = {"ensemble_positions": {}}

_ENV = "ENSEMBLE_DB_PATH"

#: Days of the spot record kept; the intraday readings need today's only.
TRAIL_KEEP_DAYS = 10


def default_db_path() -> str:
    return os.path.join(str(_home.home()), "data", "ensemble", "paper_trades.db")


def resolve_path(db_path: str | None = None) -> str:
    return db_path or os.environ.get(_ENV) or default_db_path()


_store = _ledgerstore.LedgerStore("ensemble_", _SCHEMA, _ADDED_COLUMNS)

_upsert = _store.upsert
stale_writer_columns = _store.stale_writer_columns
save_position = _store.save_position
save_leg = _store.save_leg
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
    r = conn.execute("SELECT * FROM ensemble_positions WHERE position_id = ?", (position_id,)).fetchone()
    return dict(r) if r else None


def open_positions(conn, arm: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ensemble_positions WHERE status = 'open'", []
    if arm is not None:
        sql += " AND arm = ?"
        args.append(arm)
    return [dict(r) for r in conn.execute(sql + " ORDER BY expiration, arm", args)]


def positions(conn, arm: str | None = None, *, limit: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ensemble_positions", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    sql += " ORDER BY entry_session DESC, arm"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


def open_position_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM ensemble_positions WHERE status = 'open'").fetchone()[0])


def open_expirations(conn) -> dict[str, list[str]]:
    """{symbol: [expirations]} still held open: what the stream request keeps subscribed."""
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT DISTINCT symbol, expiration FROM ensemble_positions WHERE status = 'open' ORDER BY 1, 2"
    ):
        out.setdefault(r["symbol"], []).append(r["expiration"])
    return out


def legs(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM ensemble_legs WHERE position_id = ? ORDER BY id", (position_id,))
    ]


def entry_sessions(conn) -> dict[str, list[str]]:
    """{arm: [entry sessions]} of every position, any status: the engine's `entries`."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT arm, entry_session FROM ensemble_positions ORDER BY entry_session"):
        out.setdefault(r["arm"], []).append(r["entry_session"])
    return out


def open_new_position(conn, *, position_row: dict, leg_rows: list[dict], entry_row: dict) -> None:
    """The position, its legs and its session row in one transaction: an entered session with no
    position (or legs a stream request cannot find) would be a register that does not add up."""
    try:
        _store.upsert(conn, "ensemble_positions", ("position_id",), position_row, commit=False)
        for leg in leg_rows:
            _store.upsert(conn, "ensemble_legs", ("position_id", "leg_role"), leg, commit=False)
        save_entry(conn, entry_row, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_position(conn, position_id: str, row: dict) -> None:
    """The close and its legs' status in one transaction."""
    try:
        _store.upsert(
            conn, "ensemble_positions", ("position_id",), {"position_id": position_id, **row}, commit=False
        )
        conn.execute(
            "UPDATE ensemble_legs SET status = 'closed', updated_at = ? WHERE position_id = ?",
            (_store.now(), position_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- the session register
def entry(conn, arm: str, symbol: str, session: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM ensemble_entries WHERE arm = ? AND symbol = ? AND entry_session = ?",
        (arm, symbol, session),
    ).fetchone()
    return dict(r) if r else None


def save_entry(conn, row: dict, *, commit: bool = True) -> None:
    _store.upsert(conn, "ensemble_entries", ("arm", "symbol", "entry_session"), row, commit=commit)


def entries(conn, *, session: str | None = None, limit: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ensemble_entries", []
    if session is not None:
        sql += " WHERE entry_session = ?"
        args.append(session)
    sql += " ORDER BY entry_session DESC, arm"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


# --------------------------------------------------------------------------- marks
def record_mark(conn, **fields) -> None:
    """Telemetry: swallowed on failure, like every LedgerStore telemetry writer."""
    _store.record_mark(conn, **fields)


def last_mark_ts(conn, position_id: str) -> str | None:
    r = conn.execute(
        "SELECT ts FROM ensemble_marks WHERE position_id = ? ORDER BY ts DESC LIMIT 1", (position_id,)
    ).fetchone()
    return r["ts"] if r else None


def marks(conn, position_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ensemble_marks WHERE position_id = ? ORDER BY ts", (position_id,)
        )
    ]


# --------------------------------------------------------------------------- the spot record
def record_spot(conn, symbol: str, trade_date: str, minute: int, spot: float) -> None:
    """One sample per (symbol, date, minute): the last print of the minute wins. Not swallowed: the
    intraday readings depend on it, so a failure reaches the loop's own handler."""
    conn.execute(
        "INSERT INTO ensemble_trail (symbol, trade_date, minute, spot) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(symbol, trade_date, minute) DO UPDATE SET spot = excluded.spot",
        (symbol, trade_date, int(minute), float(spot)),
    )
    conn.commit()


def trail(conn, symbol: str, trade_date: str, *, through: int | None = None) -> list[tuple[int, float]]:
    sql = "SELECT minute, spot FROM ensemble_trail WHERE symbol = ? AND trade_date = ?"
    args: list = [symbol, trade_date]
    if through is not None:
        sql += " AND minute <= ?"
        args.append(int(through))
    return [(int(r["minute"]), float(r["spot"])) for r in conn.execute(sql + " ORDER BY minute", args)]


def prune_trail(conn, before_date: str) -> int:
    cur = conn.execute("DELETE FROM ensemble_trail WHERE trade_date < ?", (before_date,))
    conn.commit()
    return cur.rowcount


def measurement_breaks(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM measurement_breaks ORDER BY break_date, key")]
