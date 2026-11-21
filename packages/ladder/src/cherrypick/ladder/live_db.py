"""The ladder live ledger: `~/.cherrypick/data/ladder/live_trades.db` (issue #1337, part 3).

A separate file from the paper ledger, never read by a paper surface and never resolved from
`LADDER_DB_PATH` (that override points paper somewhere else; a live loop that honoured it could be told
to write real fills into a paper file).

**Same lot table, so every reader of a ladder ledger reads this one.** `ladder_positions`,
`ladder_decisions`, `ladder_snapshots`, `ladder_loop_iterations` and `measurement_breaks` are the paper
schema (`db._SCHEMA`), and a lot is still one fund bought once and sold once. The live file adds:

- columns on `ladder_positions` for what only a broker gives: the masked account, the buy and sell
  order ids and client ids, each side's fees, and where the fees came from (`modeled` until the
  broker's own transaction rows are read, then `broker`);
- `ladder_orders`: one row per order the loop placed or found (its client id, the broker's id, side,
  fund, lot, shares, limit, state, fill and fee). It is the record the reconcile reads first, so a fill
  the loop has not yet turned into a lot is still an open order here.

A lot's `position_id` is `<arm>:<fund>:<buy session>:<n>`, the id paper uses, and it is stable: it
comes from the buy order's own client id (`ldr.b.<fund>.<yyyymmdd>.<n>...`), so the same buy is the same
lot on any run. Money is signed cash flow, whole-position dollars: `entry_value` (the purchase, −) +
`exit_value` (the sale, +) = `gross_pnl`; `gross_pnl` − `fees` − `settlement` − `slippage` = `net_pnl`.
Fills are the broker's, so `entry_value` and `exit_value` are what filled; slippage is zero (a resting
limit fills at its limit or better, and the better is already in the fill price) and settlement is zero
for shares.

An account number is never stored: `account` holds the masked form (`****1234`).
"""

from __future__ import annotations

import os
import sqlite3

from cherrypick.core import db as _core_db
from cherrypick.core import home as _home
from cherrypick.core import ledgerstore as _ledgerstore

from cherrypick.ladder import db as _paper

_ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ladder_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id           TEXT NOT NULL UNIQUE,
    order_id            TEXT,
    side                TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    lot_id              TEXT,
    shares              INTEGER NOT NULL,
    limit_price         REAL NOT NULL,
    time_in_force       TEXT,
    account             TEXT,
    state               TEXT NOT NULL DEFAULT 'working',
    final               INTEGER NOT NULL DEFAULT 0,
    placed_at           TEXT,
    placed_session      TEXT,
    adopted             INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    ended_session       TEXT,
    filled_shares       INTEGER,
    fill_price          REAL,
    filled_at           TEXT,
    fill_source         TEXT,
    fees                REAL,
    fees_source         TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ladder_orders_open ON ladder_orders(final, symbol);

-- One row per split applied to a lot: what makes a restatement happen once.
CREATE TABLE IF NOT EXISTS ladder_lot_splits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    ex_date       TEXT NOT NULL,
    to_shares     REAL NOT NULL,
    for_shares    REAL NOT NULL,
    shares_before INTEGER NOT NULL,
    shares_after  INTEGER NOT NULL,
    price_before  REAL NOT NULL,
    price_after   REAL NOT NULL,
    applied_at    TEXT,
    UNIQUE (position_id, ex_date)
);
"""

LIVE_SCHEMA = _paper._SCHEMA + _ORDERS_SCHEMA

# Added to `ladder_positions` in the live file only. (`CREATE TABLE IF NOT EXISTS` in the shared
# schema does not carry them, so a live file gets them by the same additive migration paper uses.)
LIVE_COLUMNS: dict[str, str] = {
    "account": "TEXT",
    "buy_order_id": "TEXT",
    "buy_client_id": "TEXT",
    "buy_fees": "REAL",
    "sell_order_id": "TEXT",
    "sell_client_id": "TEXT",
    "sell_fees": "REAL",
    "fees_source": "TEXT",
}
_ADDED_COLUMNS: dict[str, dict[str, str]] = {"ladder_positions": LIVE_COLUMNS}

_store = _ledgerstore.LedgerStore("ladder_", LIVE_SCHEMA, _ADDED_COLUMNS)

save_position = _store.save_position
record_decision = _store.record_decision
record_iteration = _store.record_iteration
record_snapshot = _store.record_snapshot
record_measurement_break = _store.record_measurement_break

# The broker-final states: after one of these an order never changes.
FINAL_STATES = frozenset({"filled", "cancelled", "rejected", "expired", "removed", "partially removed"})


def live_db_path() -> str:
    return os.path.join(str(_home.home()), "data", "ladder", "live_trades.db")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the live ledger for writing, creating and migrating it. WAL + NORMAL, as every ledger."""
    conn = _core_db.connect(db_path or live_db_path(), pragmas=("journal_mode=WAL", "synchronous=NORMAL"))
    conn.executescript(LIVE_SCHEMA)
    _store.migrate(conn)
    return conn


def connect_ro(db_path: str | None = None) -> sqlite3.Connection:
    return _core_db.connect_ro(db_path or live_db_path())


# --------------------------------------------------------------------------- orders
def save_order(conn, row: dict) -> None:
    _store.upsert(conn, "ladder_orders", ("client_id",), row)


def order(conn, client_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM ladder_orders WHERE client_id = ?", (client_id,)).fetchone()
    return dict(r) if r else None


def order_by_broker_id(conn, order_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM ladder_orders WHERE order_id = ?", (str(order_id),)).fetchone()
    return dict(r) if r else None


def open_orders(conn) -> list[dict]:
    """Orders the broker has not yet ended, oldest first."""
    return [dict(r) for r in conn.execute("SELECT * FROM ladder_orders WHERE final = 0 ORDER BY id")]


def orders(conn, *, side: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ladder_orders", []
    if side:
        sql += " WHERE side = ?"
        args.append(side)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def ended_unfilled(conn, session: str) -> list[str]:
    """Client ids the broker ended without a fill on `session`: what the planner will not re-place."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT client_id FROM ladder_orders WHERE final = 1 AND ended_session = ? "
            "AND COALESCE(filled_shares, 0) = 0",
            (session,),
        )
    ]


def known_order_ids(conn) -> list[str]:
    """Every broker order id this ledger has recorded: what the orphan sweep compares against."""
    return [str(r[0]) for r in conn.execute("SELECT order_id FROM ladder_orders WHERE order_id IS NOT NULL")]


# --------------------------------------------------------------------------- lots
def open_lots(conn, arm: str | None = None, symbol: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM ladder_positions WHERE status = 'open'", []
    if arm is not None:
        sql += " AND arm = ?"
        args.append(arm)
    if symbol is not None:
        sql += " AND symbol = ?"
        args.append(symbol)
    return [dict(r) for r in conn.execute(sql + " ORDER BY buy_time, id", args)]


def lot_splits(conn) -> set[tuple[str, str]]:
    """(position id, ex date) of every split already applied to a lot."""
    return {(r[0], r[1]) for r in conn.execute("SELECT position_id, ex_date FROM ladder_lot_splits")}


def apply_lot_split(conn, position_id: str, update: dict, record: dict) -> bool:
    """Restate one lot and record that it was, in one transaction: either both land or neither does.
    False, with nothing written, when the lot has already had this split."""
    if conn.execute(
        "SELECT 1 FROM ladder_lot_splits WHERE position_id = ? AND ex_date = ?",
        (position_id, record["ex_date"]),
    ).fetchone():
        return False
    try:
        _store.upsert(
            conn, "ladder_positions", ("position_id",), {"position_id": position_id, **update}, commit=False
        )
        cols = ", ".join(["position_id", *record])
        marks = ", ".join("?" for _ in range(len(record) + 1))
        conn.execute(
            f"INSERT INTO ladder_lot_splits ({cols}) VALUES ({marks})", [position_id, *record.values()]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def last_dry_run(conn, arm: str, symbol: str, reason: str) -> dict | None:
    """The newest dry-run journal row for this intended order (any day), or None."""
    r = conn.execute(
        "SELECT * FROM ladder_decisions WHERE mode = 'dry_run' AND arm = ? AND symbol = ? AND reason = ? "
        "ORDER BY id DESC LIMIT 1",
        (arm, symbol, reason),
    ).fetchone()
    return dict(r) if r else None


def lots_of(conn, arm: str, symbol: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ladder_positions WHERE arm = ? AND symbol = ? ORDER BY buy_time, id", (arm, symbol)
        )
    ]


def lot(conn, position_id: str) -> dict | None:
    return _paper.position(conn, position_id)


def bought_in_session(conn, arm: str, symbol: str, session: str) -> int:
    return _paper.bought_in_session(conn, arm, symbol, session)


def unsettled_proceeds(conn, session: str) -> float:
    """The live lots' sale proceeds that have not settled by `session`: every lot sold in it (stock
    settles the next business day). Every arm, since the account's cash is one pool."""
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity * sell_price), 0) FROM ladder_positions "
        "WHERE status = 'closed' AND sell_price IS NOT NULL AND sell_session >= ?",
        (session,),
    ).fetchone()
    return float(row[0] or 0.0)
