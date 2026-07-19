"""Paper database for cherrypick-flies — two tables, both append-then-update.

`fly_positions` is the row-per-structure ledger the orchestrator reads (schema tag `fly_book`).
`fly_books` is the per-session book roll-up, and it exists because a per-position ledger cannot
express the one thing that separates this strategy's honest claim from its marketing: a book whose
risk graph is green in the middle may still lose outside the funding spreads' wings. The band lives
here, next to the floor it qualifies.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fly_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT UNIQUE,
    book_id             TEXT,
    trade_date          TEXT,
    arm                 TEXT,
    entry_mode          TEXT,
    symbol              TEXT,
    kind                TEXT,
    side                TEXT,
    center              REAL,
    wing_width          REAL,
    quantity            INTEGER,
    net                 REAL,
    credit              REAL,
    debit               REAL,
    fees                REAL,
    floor_dollars       REAL,
    risk_free           INTEGER,
    entry_time          TEXT,
    entry_window        TEXT,
    center_reason       TEXT,
    completing_direction TEXT,
    completed_at        TEXT,
    underlying_at_entry REAL,
    -- Counterfactual: the LOWEST completing debit seen while this spread was open, recorded whether
    -- or not the gate fired. Without it, "never completed" is ambiguous between "the market never
    -- offered it" and "our fee_buffer was too tight" -- and those need opposite fixes.
    best_completing_debit REAL,
    best_debit_at       TEXT,
    -- Minutes from open to completion, and where spot was when it happened. Feeds the paper-vs-live
    -- gap: a completion that took three seconds of quote drift is far less likely to fill live than
    -- one that took forty minutes.
    completion_latency_min REAL,
    spot_at_completion  REAL,
    settlement_price    REAL,
    expiry_payoff       REAL,
    gross_pnl           REAL,
    pnl                 REAL,
    pinned              INTEGER,
    status              TEXT,
    exit_time           TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_fly_positions_date ON fly_positions(trade_date);
CREATE INDEX IF NOT EXISTS idx_fly_positions_book ON fly_positions(book_id);

CREATE TABLE IF NOT EXISTS fly_books (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id           TEXT UNIQUE,
    trade_date        TEXT,
    arm               TEXT,
    symbol            TEXT,
    credit_collected  REAL,
    debits_paid       REAL,
    fees              REAL,
    net_cash          REAL,
    worst             REAL,
    worst_at          REAL,
    floor_holds       INTEGER,
    band_low          REAL,
    band_high         REAL,
    unbounded_below   INTEGER,
    completion_rate   REAL,
    risk_free_rate    REAL,
    pin_rate          REAL,
    settlement_price  REAL,
    pnl               REAL,
    status            TEXT,
    created_at        TEXT,
    updated_at        TEXT
);

-- The decision journal: WHY an entry was made or refused, in a form you can query.
--
-- One row per RUN of an identical (trade_date, arm, symbol, mode, reason). A gate that blocks every
-- iteration from 09:45 to 11:20 is one row with occurrences=18, not eighteen identical rows -- so a
-- day where nothing traded reads as a handful of rows that tell the story. This is deliberately
-- unlike MEIC, which collapses its (equally rich) reasons into a free-text loop_log.reasoning blob
-- that later has to be regex-scraped and can't be aggregated at all.
CREATE TABLE IF NOT EXISTS fly_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    arm           TEXT,
    symbol        TEXT,
    mode          TEXT,     -- legged | outright | completion
    reason        TEXT,     -- the engine's reason string, plus entered / completed on the accept path
    accepted      INTEGER,  -- 1 when this run represents action taken, 0 when it is a refusal
    first_seen    TEXT,
    last_seen     TEXT,
    occurrences   INTEGER,
    center_first  REAL,
    center_last   REAL,
    position_id   TEXT,     -- set on the accept path, so a decision links to what it produced
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_fly_decisions_date ON fly_decisions(trade_date);

-- One thin row per (iteration x arm): what each arm WANTED, whether or not it acted.
--
-- Separate from the journal because collapsing destroys exactly what this is for. Arm divergence asks
-- what the arms chose ON THE SAME ITERATION, and if gex and control agree most of the time then the
-- experiment cannot separate them -- which is worth discovering in week one rather than month three.
CREATE TABLE IF NOT EXISTS fly_iterations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_ts      TEXT,
    trade_date        TEXT,
    symbol            TEXT,
    arm               TEXT,
    center            REAL,
    center_reason     TEXT,
    underlying_price  REAL,
    UNIQUE (iteration_ts, symbol, arm)
);
CREATE INDEX IF NOT EXISTS idx_fly_iterations_date ON fly_iterations(trade_date);
"""


def default_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "flies", "paper_trades.db")


# Columns added to fly_positions after the first release. CREATE TABLE IF NOT EXISTS silently does
# nothing on an existing database, so a plain schema edit would leave older paper DBs missing these
# and every write against them would fail at runtime rather than at startup.
_ADDED_POSITION_COLUMNS = {
    "best_completing_debit": "REAL",
    "best_debit_at": "TEXT",
    "completion_latency_min": "REAL",
    "spot_at_completion": "REAL",
}


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any columns missing from an older paper DB. Returns what it added (for tests and logs)."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(fly_positions)")}
    added = []
    for column, sql_type in _ADDED_POSITION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE fly_positions ADD COLUMN {column} {sql_type}")
            added.append(column)
    if added:
        conn.commit()
    return added


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("FLIES_DB_PATH") or default_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _upsert(conn, table: str, key: str, row: dict) -> None:
    """Insert `row`, or update the existing row with the same natural key. Keeps the loop idempotent:
    a restart mid-session re-writes the same position rather than duplicating it."""
    row = {**row, "updated_at": _now()}
    existing = conn.execute(f"SELECT id FROM {table} WHERE {key} = ?", (row[key],)).fetchone()
    if existing is None:
        row.setdefault("created_at", _now())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
    else:
        sets = ", ".join(f"{c} = ?" for c in row if c != key)
        vals = [v for c, v in row.items() if c != key] + [row[key]]
        conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", vals)
    conn.commit()


def save_position(conn, row: dict) -> None:
    _upsert(conn, "fly_positions", "position_id", row)


def save_book(conn, row: dict) -> None:
    _upsert(conn, "fly_books", "book_id", row)


def record_decision(conn, *, trade_date: str, arm: str, symbol: str, mode: str, reason: str,
                    accepted: bool = False, center: float | None = None,
                    position_id: str | None = None, detail: str | None = None,
                    when: str | None = None) -> None:
    """Append to the decision journal, extending the current run when the reason is unchanged.

    "Current run" means the most recent row for this (trade_date, arm, symbol, mode) — so an unchanged
    reason bumps `occurrences` and `last_seen`, and a changed one opens a new row. Accepted decisions
    never extend a run: an entry is a distinct event even when two happen back to back, and collapsing
    them would lose the count of trades actually taken.
    """
    now = when or _now()
    latest = conn.execute(
        "SELECT * FROM fly_decisions WHERE trade_date = ? AND arm = ? AND symbol = ? AND mode = ? "
        "ORDER BY id DESC LIMIT 1",
        (trade_date, arm, symbol, mode),
    ).fetchone()

    if latest is not None and latest["reason"] == reason and not accepted and not latest["accepted"]:
        conn.execute(
            "UPDATE fly_decisions SET last_seen = ?, occurrences = occurrences + 1, center_last = ? "
            "WHERE id = ?",
            (now, center if center is not None else latest["center_last"], latest["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, "
            "last_seen, occurrences, center_first, center_last, position_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (trade_date, arm, symbol, mode, reason, int(accepted), now, now, center, center,
             position_id, detail),
        )
    conn.commit()


def record_iteration(conn, *, iteration_ts: str, trade_date: str, symbol: str, arm: str,
                     center: float | None, center_reason: str | None,
                     underlying_price: float | None) -> None:
    """Record what one arm wanted on one iteration. Idempotent on (iteration_ts, symbol, arm) so a
    re-run of the same snapshot doesn't inflate the divergence denominator."""
    conn.execute(
        "INSERT OR REPLACE INTO fly_iterations (iteration_ts, trade_date, symbol, arm, center, "
        "center_reason, underlying_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (iteration_ts, trade_date, symbol, arm, center, center_reason, underlying_price),
    )
    conn.commit()


def open_positions(conn, book_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE book_id = ? AND status = 'open'", (book_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def book_positions(conn, book_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fly_positions WHERE book_id = ?", (book_id,)).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect the cherrypick-flies paper database.")
    ap.add_argument("command", choices=["positions", "books"])
    ap.add_argument("--db")
    ap.add_argument("--date")
    args = ap.parse_args()

    conn = connect(args.db)
    table = "fly_positions" if args.command == "positions" else "fly_books"
    if args.date:
        rows = conn.execute(f"SELECT * FROM {table} WHERE trade_date = ?", (args.date,)).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 50").fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
