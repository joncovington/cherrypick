"""How a fetch store is opened for writing: one place for the lock wait, the WAL switch and the schema.

Every fetch (chains, add-on weeklies, intraday chains, open interest, flow) and the recorder's vendor
store write through a connection opened here, so none of them can be left on sqlite's 5 s default wait
or on the rollback journal while a reader holds the file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: How long a writer waits for the file to be free (seconds). Another study reading the store, or a
#: second fetch writing it, holds the lock for a moment; sqlite's 5 s default ended a 1,500-path run
#: mid-write (2026-10-23).
LOCK_WAIT_S = 300

#: How long `prefer_wal` waits for the file to be free before leaving its journal mode as it is.
WAL_SWITCH_WAIT_MS = 1000


def prefer_wal(conn: sqlite3.Connection, wait_ms: int = WAL_SWITCH_WAIT_MS) -> str:
    """Put a fetch store in WAL, where a reader and the writer stop blocking each other, and return
    the mode it is in. Without it, the inventory job's full count of a large store held the file
    for longer than a writer's 300 s wait, and the fetch died on its next write (the full set's
    near-spx-2, 2026-11-21). The switch needs the file to itself for a moment, so a busy file keeps
    its mode for this run after `wait_ms`, rather than holding the fetch back; the mode is stored in
    the file, so the first connect that finds it quiet switches it for good."""
    (busy,) = conn.execute("PRAGMA busy_timeout").fetchone()
    conn.execute(f"PRAGMA busy_timeout = {int(wait_ms)}")
    try:
        return str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0])
    except sqlite3.OperationalError:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    finally:
        conn.execute(f"PRAGMA busy_timeout = {int(busy)}")


def open_store(path: Path, *schema: str, row_factory: type | None = None) -> sqlite3.Connection:
    """Open (creating) the store at `path` for writing: a long lock wait, WAL when the file is quiet,
    then each `schema` script run (all are `IF NOT EXISTS`, so re-opening is harmless). The caller
    creates the marked vendor directory first."""
    conn = sqlite3.connect(path, timeout=LOCK_WAIT_S)
    if row_factory is not None:
        conn.row_factory = row_factory
    prefer_wal(conn)
    for script in schema:
        conn.executescript(script)
    return conn
