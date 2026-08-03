"""Scout's own SQLite cache — ``~/.cherrypick/data/scout/cache.db``. Never a cache another module
owns (the streamer's ``stream_cache.db``, a Dolt database) — this file exists purely to memoize scout's
own broker/Dolt reads under a TTL.

``get_or_fetch`` is the one primitive every service builds on: read-through with a TTL, and on a
fetch failure it serves the last-known payload with ``stale=True`` rather than raising — a rate-limit
hiccup should degrade the page, not blank it. Every API payload built on top of this carries
``as_of``/``stale`` so the UI can label freshness honestly instead of asserting a number is live when
it is an hour old.

Clock is injectable (``now=``) so tests can move time without sleeping.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_cache (
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (bucket, key)
);

CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    ts INTEGER NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    v REAL,
    PRIMARY KEY (symbol, period, ts)
);

CREATE TABLE IF NOT EXISTS candle_meta (
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    last_backfill REAL,
    PRIMARY KEY (symbol, period)
);

CREATE TABLE IF NOT EXISTS symbol_meta (
    symbol TEXT PRIMARY KEY,
    sector TEXT,
    industry TEXT,
    source TEXT,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS staged_orders (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    legs_json TEXT NOT NULL,
    credit REAL,
    max_risk REAL,
    dry_run_json TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'staged'
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the cache DB in WAL mode with the full schema present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _read(conn: sqlite3.Connection, bucket: str, key: str) -> tuple[Any, float] | None:
    row = conn.execute(
        "SELECT payload, fetched_at FROM kv_cache WHERE bucket = ? AND key = ?", (bucket, key)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0]), float(row[1])


def _write(conn: sqlite3.Connection, bucket: str, key: str, payload: Any, fetched_at: float) -> None:
    conn.execute(
        "INSERT INTO kv_cache (bucket, key, payload, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(bucket, key) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
        (bucket, key, json.dumps(payload), fetched_at),
    )
    conn.commit()


def get_or_fetch(
    conn: sqlite3.Connection,
    bucket: str,
    key: str,
    ttl: float,
    fetch_fn: Callable[[], Any],
    *,
    force: bool = False,
    refresh_floor_seconds: float = 60.0,
    now: float | None = None,
) -> tuple[Any, float, bool]:
    """Read-through cache with a TTL.

    Returns ``(payload, fetched_at, stale)``. ``force=True`` (the API's ``?fresh=1``) bypasses the TTL
    but is itself floored to ``refresh_floor_seconds`` since the last fetch, so a refresh button can't
    be used to hammer the broker. On a fetch failure, the last cached payload is returned with
    ``stale=True`` if one exists; otherwise the exception propagates (nothing to serve).
    """
    now = time.time() if now is None else now
    cached = _read(conn, bucket, key)
    if cached is not None:
        payload, fetched_at = cached
        age = now - fetched_at
        if not force and age < ttl:
            return payload, fetched_at, False
        if force and age < refresh_floor_seconds:
            return payload, fetched_at, False

    try:
        fresh_payload = fetch_fn()
    except Exception:
        if cached is not None:
            payload, fetched_at = cached
            return payload, fetched_at, True
        raise

    _write(conn, bucket, key, fresh_payload, now)
    return fresh_payload, now, False
