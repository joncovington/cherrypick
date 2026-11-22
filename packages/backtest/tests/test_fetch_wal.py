"""The fetch stores open in WAL, so a long read (the inventory job's count of a large store) cannot hold
a fetch's write past its wait; and a store busy at connect keeps its mode rather than holding the
fetch back. Synthetic stores only."""

from __future__ import annotations

import sqlite3
import time

import pytest

from cherrypick.backtest import addonfetch, chainfetch, settings
from cherrypick.backtest import intradayfetch as ix


def _settings(tmp_path):
    return settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})


STORES = {
    "chains": (chainfetch.connect, chainfetch.db_path),
    "intraday": (ix.connect, ix.db_path),
    "addon": (addonfetch.connect, addonfetch.db_path),
}


@pytest.mark.parametrize("name", sorted(STORES))
def test_each_fetch_store_opens_in_wal(tmp_path, name):
    connect, _ = STORES[name]
    conn = connect(_settings(tmp_path))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_a_reader_mid_count_does_not_block_the_fetch_write(tmp_path):
    # The near-spx-2 failure: the inventory job's full count held the store, and the fetch's next
    # write waited out its timeout. In WAL the write lands while the read is still open.
    s = _settings(tmp_path)
    ix.connect(s).close()
    reader = sqlite3.connect(ix.db_path(s))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM intraday_chain_requests").fetchone()
    writer = ix.connect(s)
    writer.execute("PRAGMA busy_timeout = 200")
    try:
        with writer:
            ix._record(
                writer, ("SPXW", "2024-06-21", "2024-06-24", "09:30", "16:00", "1m"), "refused", 0, 0, "x"
            )
    finally:
        writer.close()
        reader.close()


def test_a_busy_store_keeps_its_mode_quickly_and_switches_once_quiet(tmp_path):
    s = _settings(tmp_path)
    path = ix.db_path(s)
    path.parent.mkdir(parents=True)
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE t (x)")
    seed.commit()
    seed.close()
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM t").fetchone()
    conn = sqlite3.connect(path, timeout=300)
    started = time.monotonic()
    assert chainfetch.prefer_wal(conn, wait_ms=100) == "delete"
    assert time.monotonic() - started < 5  # not the connection's 300 s
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 300_000  # its own wait is restored
    conn.close()
    reader.close()
    quiet = sqlite3.connect(path)
    assert chainfetch.prefer_wal(quiet) == "wal"
    quiet.close()
