"""Every fetch store opens through `storeopen.open_store`: a long lock wait, WAL, the schema, and the
row factory its readers expect. The read-only opener copes with a directory whose name would change a
`file:` URI's meaning. Synthetic stores only."""

from __future__ import annotations

import sqlite3
import sys

import pytest
from cherrypick.core.db import connect_ro

from cherrypick.backtest import addonfetch, chainfetch, flowfetch, oifetch, settings, storeopen
from cherrypick.backtest import intradayfetch as ix


def _settings(tmp_path):
    return settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})


def test_open_store_waits_long_runs_the_schema_and_is_in_wal(tmp_path):
    path = tmp_path / "s.db"
    conn = storeopen.open_store(
        path, "CREATE TABLE IF NOT EXISTS t (x);", "CREATE TABLE IF NOT EXISTS u (y);"
    )
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == storeopen.LOCK_WAIT_S * 1000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} == {
            "t",
            "u",
        }
    finally:
        conn.close()
    storeopen.open_store(path, "CREATE TABLE IF NOT EXISTS t (x);").close()  # reopening is harmless


def test_open_store_applies_the_row_factory_only_when_asked(tmp_path):
    plain = storeopen.open_store(tmp_path / "a.db", "CREATE TABLE t (x);")
    rows = storeopen.open_store(tmp_path / "b.db", "CREATE TABLE t (x);", row_factory=sqlite3.Row)
    try:
        assert plain.row_factory is None
        assert rows.row_factory is sqlite3.Row
    finally:
        plain.close()
        rows.close()


@pytest.mark.parametrize(
    "connect",
    [chainfetch.connect, ix.connect, addonfetch.connect, oifetch.connect, flowfetch.connect],
    ids=["chains", "intraday", "addon", "oi", "flow"],
)
def test_every_fetch_store_waits_the_long_lock_wait(tmp_path, connect):
    # oifetch and flowfetch used to open on sqlite's 5 s default
    conn = connect(_settings(tmp_path))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == storeopen.LOCK_WAIT_S * 1000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_oi_and_flow_stores_keep_their_named_columns(tmp_path):
    for connect in (oifetch.connect, flowfetch.connect):
        conn = connect(_settings(tmp_path))
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()


def test_the_read_only_opener_survives_a_path_that_changes_a_uri(tmp_path):
    odd = tmp_path / ("a#b%c" if sys.platform == "win32" else "a?b#c%d")  # a Windows name cannot hold ?
    odd.mkdir()
    path = odd / "s.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE t (x)")
    seed.execute("INSERT INTO t VALUES (7)")
    seed.commit()
    seed.close()
    conn = connect_ro(path, row_factory=None)
    try:
        assert conn.execute("SELECT x FROM t").fetchone() == (7,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (8)")  # read-only
    finally:
        conn.close()
