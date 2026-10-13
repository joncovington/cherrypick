"""How the recorder opens its own history database (`gex_history.db`), and how one tick shares a
connection across its series.

**WAL, set once per process at open.** The recorder writes every ~15 s for the whole session while
flies (`core.regime.gex_at`, every tick), the console, the overview and the advisor read the same
file. In the rollback journal a write blocks readers for its duration; in WAL it does not. The mode
is persistent in the file, so it is asked for once per process and retried on the next open if a
reader's lock refused it. Every reader already opens read-only (`core.db.connect_ro`,
better-sqlite3 `readonly`), which reads a WAL database like any other -- the stream cache has always
been one.

**One connection and one commit per tick.** The loop used to open, commit and close the file four
times a tick (spots, GEX regime, quotes, market regime). `tick` opens it once and commits once; each
series runs inside its own savepoint (`step`), so a series that raises still loses only its own
writes, never another's -- what a separate connection per series gave, without a commit each.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_wal_dbs: set[str] = set()


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the history DB for writing, in WAL (asked for once per process per file)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    key = str(db_path.resolve())
    if key not in _wal_dbs:
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode and str(mode[0]).lower() == "wal":
                _wal_dbs.add(key)
        except sqlite3.OperationalError:
            pass  # another connection held a lock; the next open asks again
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tick(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """One connection for a whole recorder tick, committed once at the end, closed always. What the
    tick's series wrote before one of them raised is still committed: each ran in its own `step`."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        try:
            if conn.in_transaction:
                conn.commit()
        finally:
            conn.close()


@contextmanager
def step(db_path: Path | str, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """The connection one series writes through. Standalone (`conn` None): its own connection,
    committed on success and discarded on a raise, which is how every series ran before `tick`.
    Inside a tick: a savepoint on the shared connection, rolled back on a raise, so the series is
    all-or-nothing exactly as it was on a connection of its own."""
    if conn is None:
        own = connect(db_path)
        try:
            yield own
            own.commit()
        finally:
            own.close()
        return
    conn.execute("SAVEPOINT recorder_step")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK TO recorder_step")
        conn.execute("RELEASE recorder_step")
        raise
    conn.execute("RELEASE recorder_step")
