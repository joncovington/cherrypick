"""The package's one store, `fundamentals.db`: every landing opens it here, with its own tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import paths


def connect(path: Path | None = None, schema: str = "") -> sqlite3.Connection:
    """The store (created on first use) with `schema`'s tables. A research store, rebuildable from its
    sources: durability per transaction is not worth its cost here."""
    path = path or paths.db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if schema:
        conn.executescript(schema)
    return conn
