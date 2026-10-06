"""The contango paper ledger: schema, additive migrations, and every writer.

One SQLite file, `~/.cherrypick/data/contango/paper_trades.db` -- the suite's filename convention,
which the read side resolves generically as `<data home>/<module>/paper_trades.db`.

**A position is one holding stint**: the shares an arm bought on one switch and sold on the next.
An arm alternates between its risk stint (SVXY) and its cash stint (SHV), so its stints tile its
whole life and their nets sum to its P&L. `contango_sessions` is the arm's daily NAV row -- the
series the module is judged on, since a stint's P&L says nothing about a drawdown inside it.
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home
from cherrypick.core import ledgerstore as _ledgerstore

_SCHEMA = """
-- One row per holding stint per arm. `position_id` = "<arm>:<symbol>:<entry_session>". Money is
-- signed cash flow: entry_value is the purchase (negative), exit_value the sale (positive), and
-- entry_value + exit_value + distributions = gross_pnl; gross_pnl - fees - slippage = net_pnl.
CREATE TABLE IF NOT EXISTS contango_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL UNIQUE,
    arm             TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    role            TEXT NOT NULL,
    shares          INTEGER NOT NULL,
    entry_session   TEXT NOT NULL,
    entry_time      TEXT,
    entry_bid       REAL,
    entry_ask       REAL,
    entry_mid       REAL,
    entry_value     REAL,
    entry_fees      REAL,
    entry_slippage  REAL,
    entry_ratio     REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    exit_session    TEXT,
    exit_time       TEXT,
    exit_bid        REAL,
    exit_ask        REAL,
    exit_mid        REAL,
    exit_value      REAL,
    exit_fees       REAL,
    exit_slippage   REAL,
    exit_ratio      REAL,
    exit_reason     TEXT,
    distributions   REAL NOT NULL DEFAULT 0,
    gross_pnl       REAL,
    fees            REAL,
    slippage        REAL,
    net_pnl         REAL,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_contango_positions_arm ON contango_positions(arm, status);
CREATE INDEX IF NOT EXISTS idx_contango_positions_session ON contango_positions(entry_session);

-- Each arm's paper account: the cash it holds beside its one stint. State, not telemetry.
CREATE TABLE IF NOT EXISTS contango_accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    arm              TEXT NOT NULL UNIQUE,
    starting_capital REAL NOT NULL,
    cash             REAL NOT NULL,
    opened_session   TEXT NOT NULL,
    created_at       TEXT,
    updated_at       TEXT
);

-- A distribution paid to a stint that held the fund at the open of its ex-date, from the
-- technicals store's dividend table. Credited when the row first becomes visible there, which can
-- be days after the ex-date; `credited_session` says when.
CREATE TABLE IF NOT EXISTS contango_distributions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id      TEXT NOT NULL,
    arm              TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    ex_date          TEXT NOT NULL,
    per_share        REAL NOT NULL,
    shares           INTEGER NOT NULL,
    amount           REAL NOT NULL,
    credited_session TEXT NOT NULL,
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(position_id, ex_date)
);

-- The daily regime read, one row per session whether or not any arm switches.
CREATE TABLE IF NOT EXISTS contango_regime (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL UNIQUE,
    tick        TEXT NOT NULL,
    ratio       REAL,
    vix         REAL,
    vix3m       REAL,
    vix_age_s   REAL,
    vix3m_age_s REAL,
    usable      INTEGER NOT NULL DEFAULT 0,
    refusal     TEXT,
    created_at  TEXT,
    updated_at  TEXT
);

-- Each arm's session: what it held going in, what its rule chose, what happened, and its NAV at
-- the decision tick. `action` is hold / switch / missed (the window closed unacted, with `refusal`).
CREATE TABLE IF NOT EXISTS contango_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT NOT NULL,
    arm            TEXT NOT NULL,
    decided_at     TEXT,
    ratio          REAL,
    state_before   TEXT,
    state_after    TEXT,
    action         TEXT,
    refusal        TEXT,
    holding_symbol TEXT,
    shares         INTEGER,
    mark           REAL,
    cash           REAL,
    nav            REAL,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(trade_date, arm)
);

-- The collapsed narrative journal (flies' shape).
CREATE TABLE IF NOT EXISTS contango_decisions (
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
CREATE INDEX IF NOT EXISTS idx_contango_decisions_date ON contango_decisions(trade_date);

-- The feed ledger: one row per decision-window tick per symbol, refusals included.
CREATE TABLE IF NOT EXISTS contango_snapshots (
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
CREATE INDEX IF NOT EXISTS idx_contango_snapshots_date ON contango_snapshots(trade_date);

-- Each fund's quote at the session's decision tick, whether or not any arm holds it (2026-10-06).
-- Buy-and-hold of the risk fund and the forward replay are both read off these, so the benchmark
-- and the expected path are priced at the same tick as the arms' own fills.
CREATE TABLE IF NOT EXISTS contango_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    bid         REAL,
    ask         REAL,
    mid         REAL,
    age_s       REAL,
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE(trade_date, symbol)
);

-- One row per in-session tick: the loop's own vital signs.
CREATE TABLE IF NOT EXISTS contango_loop_iterations (
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
CREATE INDEX IF NOT EXISTS idx_contango_iterations_session ON contango_loop_iterations(session_date, ran_at);

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
_ADDED_COLUMNS: dict[str, dict[str, str]] = {"contango_positions": {}}


def default_db_path() -> str:
    return os.path.join(str(_home.home()), "data", "contango", "paper_trades.db")


_store = _ledgerstore.LedgerStore("contango_", _SCHEMA, _ADDED_COLUMNS)

_upsert = _store.upsert
stale_writer_columns = _store.stale_writer_columns
save_position = _store.save_position
record_decision = _store.record_decision
record_snapshot = _store.record_snapshot
record_iteration = _store.record_iteration
record_measurement_break = _store.record_measurement_break


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger for writing. WAL + NORMAL, matching every other module ledger."""
    path = db_path or os.environ.get("CONTANGO_DB_PATH") or default_db_path()
    conn = _core_db.connect(path, pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(_SCHEMA)
    _store.migrate(conn)
    return conn


def connect_ro(db_path: str | None = None) -> sqlite3.Connection:
    """Open the ledger READ-ONLY, for the read side: never migrates, never creates (the curve
    lesson -- a read verb that opened the write path created empty ledgers in a scratch home)."""
    return _core_db.connect_ro(db_path or os.environ.get("CONTANGO_DB_PATH") or default_db_path())


# --------------------------------------------------------------------------- accounts
def account(conn, arm: str) -> dict | None:
    r = conn.execute("SELECT * FROM contango_accounts WHERE arm = ?", (arm,)).fetchone()
    return dict(r) if r else None


def open_account(conn, arm: str, starting_capital: float, session: str) -> dict:
    """The arm's account, created at `starting_capital` on first sight. An existing account is
    never re-capitalised by a config change: that would be a new arm, and a journaled break."""
    existing = account(conn, arm)
    if existing is not None:
        return existing
    _upsert(
        conn,
        "contango_accounts",
        ("arm",),
        {
            "arm": arm,
            "starting_capital": float(starting_capital),
            "cash": float(starting_capital),
            "opened_session": session,
        },
    )
    return account(conn, arm)


# --------------------------------------------------------------------------- positions
def open_position(conn, arm: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM contango_positions WHERE arm = ? AND status = 'open' "
        "ORDER BY entry_session DESC LIMIT 1",
        (arm,),
    ).fetchone()
    return dict(r) if r else None


def positions(conn, arm: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM contango_positions", ()
    if arm is not None:
        sql, args = sql + " WHERE arm = ?", (arm,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY entry_session, id", args)]


def open_position_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM contango_positions WHERE status = 'open'").fetchone()[0])


# --------------------------------------------------------------------------- atomic state changes
# A switch moves three rows -- the stint it closes, the stint it opens, the account's cash -- and a
# distribution moves three as well. `LedgerStore.upsert` commits per row, so a crash between two of
# them would leave cash that no stint explains (or a credit paid twice on the re-run). These write
# every row of one change and commit once.
def _upsert_nocommit(conn, table: str, keys: tuple[str, ...], row: dict) -> None:
    row = {**row, "updated_at": _store.now()}
    where = " AND ".join(f"{k} = ?" for k in keys)
    existing = conn.execute(f"SELECT id FROM {table} WHERE {where}", [row[k] for k in keys]).fetchone()
    if existing is None:
        row.setdefault("created_at", _store.now())
        cols = ", ".join(row)
        conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values())
        )
    else:
        sets = ", ".join(f"{c} = ?" for c in row if c not in keys)
        vals = [v for c, v in row.items() if c not in keys] + [row[k] for k in keys]
        conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", vals)


def apply_switch(conn, *, arm: str, closed: dict | None, opened: dict, cash: float) -> None:
    try:
        if closed is not None:
            _upsert_nocommit(conn, "contango_positions", ("position_id",), closed)
        _upsert_nocommit(conn, "contango_positions", ("position_id",), opened)
        _upsert_nocommit(conn, "contango_accounts", ("arm",), {"arm": arm, "cash": round(cash, 2)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def apply_distribution(conn, *, credit: dict, position: dict, cash: float) -> None:
    try:
        _upsert_nocommit(conn, "contango_distributions", ("position_id", "ex_date"), credit)
        _upsert_nocommit(conn, "contango_positions", ("position_id",), position)
        _upsert_nocommit(conn, "contango_accounts", ("arm",), {"arm": credit["arm"], "cash": round(cash, 2)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- distributions
def credited_pairs(conn) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT position_id, ex_date FROM contango_distributions")
    return {(r["position_id"], r["ex_date"]) for r in rows}


# --------------------------------------------------------------------------- regime / sessions
def save_regime(conn, row: dict) -> None:
    _upsert(conn, "contango_regime", ("trade_date",), row)


def regime_for(conn, trade_date: str) -> dict | None:
    r = conn.execute("SELECT * FROM contango_regime WHERE trade_date = ?", (trade_date,)).fetchone()
    return dict(r) if r else None


def save_mark(conn, row: dict) -> None:
    _upsert(conn, "contango_marks", ("trade_date", "symbol"), row)


def marks(conn, symbol: str) -> dict[str, float]:
    """{trade_date: mid} for one fund. Empty on a ledger opened read-only before the table existed
    (2026-10-06): the next loop tick creates it, and a read must not fail in the meantime."""
    try:
        rows = conn.execute(
            "SELECT trade_date, mid FROM contango_marks WHERE symbol = ? AND mid IS NOT NULL "
            "ORDER BY trade_date",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["trade_date"]: float(r["mid"]) for r in rows}


def save_session(conn, row: dict) -> None:
    _upsert(conn, "contango_sessions", ("trade_date", "arm"), row)


def session_for(conn, trade_date: str, arm: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM contango_sessions WHERE trade_date = ? AND arm = ?", (trade_date, arm)
    ).fetchone()
    return dict(r) if r else None


def sessions(conn, arm: str | None = None, limit: int | None = None) -> list[dict]:
    sql, args = "SELECT * FROM contango_sessions", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    sql += " ORDER BY trade_date DESC, arm"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]
