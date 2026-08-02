"""The order-alert inbox: a tiny WAL-mode SQLite file the alert daemon writes and the live loop reads.

Deliberately its OWN file, not a table in `live_trades.db`. That ledger's concurrency model was
tuned for exactly two short-burst writers -- the `--once` tick and the spawned `--watch-fills`
watcher, each holding its own file lock (`_once_lock_path`/`_watch_lock_path`), relying on
SQLite's default rollback journal and Python's default 5s busy-wait plus atomic claim patterns to
survive their overlap. Adding a third, *persistent* writer to that file would stack a new
contender onto exactly the arrangement that was never designed for it.

So the inbox is a separate database in the shape SQLite's WAL mode exists for: ONE writer (the
daemon) and N readers (the tick, the watcher) who never block each other and never block the
writer. `live_trades.db` keeps precisely its current two writers, untouched.

What lands here is not authoritative and is never treated as such. A row only ever answers "this
order changed -- worth asking the broker now?"; the live loop still confirms every fill through
its ordinary `_confirm_*_fill` -> `broker.status()` call. That is what keeps the daemon an
accelerator rather than something a fill depends on: if it dies, stalls, or was never started,
the loop's existing heartbeat poll still confirms everything, just later.
"""

from __future__ import annotations

import os
import sqlite3

# Long enough to outlast any writer's burst (the daemon commits one small row at a time), short
# enough that a reader never wedges a tick. WAL means readers don't block on the writer at all;
# this only covers writer-vs-writer, which should never happen given the single-writer contract.
BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_alerts (
    order_id    TEXT NOT NULL,
    status      TEXT,
    price       TEXT,
    filled      INTEGER,
    cancellable INTEGER,
    received_at TEXT NOT NULL,
    PRIMARY KEY (order_id, received_at)
);
CREATE INDEX IF NOT EXISTS idx_order_alerts_received ON order_alerts(received_at);
"""


def alerts_db_path() -> str:
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "flies", "live_alerts.db")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """WAL + an explicit busy_timeout, unlike `db.connect()` -- see the module docstring for why
    this file gets a different concurrency posture than the ledger does."""
    path = db_path or os.environ.get("FLIES_ALERTS_DB_PATH") or alerts_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    return conn


def record_alert(conn, alert: dict, received_at: str) -> None:
    """Append one alert. `alert` is the {order_id, status, price, filled, cancellable} shape
    `cherrypick.core.broker._serialize_placed_order` produces, so the daemon stores exactly what
    a `.status()` poll would have returned -- no second parsing convention to keep in sync.

    INSERT OR IGNORE on (order_id, received_at): a websocket redelivering the same alert must not
    error out the daemon's listen loop over a duplicate it can do nothing about."""
    conn.execute(
        "INSERT OR IGNORE INTO order_alerts (order_id, status, price, filled, cancellable, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(alert.get("order_id")),
            alert.get("status"),
            alert.get("price"),
            int(bool(alert.get("filled"))),
            int(bool(alert.get("cancellable"))),
            received_at,
        ),
    )
    conn.commit()


def alerts_since(conn, order_ids, since: str | None) -> list[dict]:
    """Every alert for `order_ids` received after `since` (exclusive), oldest first. `since=None`
    returns everything on file for those orders -- the natural "first look" for a freshly spawned
    watcher, which has no prior checkpoint of its own."""
    ids = [str(o) for o in order_ids if o]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    params: list = list(ids)
    clause = ""
    if since is not None:
        clause = " AND received_at > ?"
        params.append(since)
    rows = conn.execute(
        f"SELECT order_id, status, price, filled, cancellable, received_at "
        f"FROM order_alerts WHERE order_id IN ({marks}){clause} ORDER BY received_at",
        params,
    ).fetchall()
    return [
        {
            "order_id": r["order_id"],
            "status": r["status"],
            "price": r["price"],
            "filled": bool(r["filled"]),
            "cancellable": bool(r["cancellable"]),
            "received_at": r["received_at"],
        }
        for r in rows
    ]


def prune_before(conn, cutoff: str) -> int:
    """Drop alerts older than `cutoff` (an ISO timestamp). Called on daemon start: the inbox is a
    transient hand-off buffer, not a record -- the ledger is where anything durable lives."""
    cur = conn.execute("DELETE FROM order_alerts WHERE received_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount
