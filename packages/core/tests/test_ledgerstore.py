"""The write-path contracts the two paper ledgers depend on.

These are not stylistic. Which writers swallow and which do not is the difference between losing a
row of telemetry and losing position state.
"""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.core import ledgerstore

SCHEMA = """
CREATE TABLE IF NOT EXISTS t_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'open',
    book        TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS t_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT,
    usable      INTEGER
);
CREATE TABLE IF NOT EXISTS measurement_breaks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    break_date  TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT, new_value TEXT, note TEXT, recorded_at REAL,
    UNIQUE(break_date, key)
);
"""
ADDED: dict[str, dict] = {"t_positions": {}, "t_marks": {}}


@pytest.fixture
def store():
    return ledgerstore.LedgerStore("t_", SCHEMA, ADDED)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


def test_the_prefix_builds_table_names(store):
    assert store.table("positions") == "t_positions"


# --------------------------------------------------------------------------- upsert


def test_upsert_inserts_then_updates_the_same_natural_key(store, conn):
    store.save_position(conn, {"position_id": "p1", "status": "open", "book": "control"})
    store.save_position(conn, {"position_id": "p1", "status": "closed", "book": "control"})

    rows = list(conn.execute("SELECT * FROM t_positions"))
    assert len(rows) == 1, "a restart mid-session must re-write, never duplicate"
    assert rows[0]["status"] == "closed"


def test_upsert_stamps_created_once_and_updated_every_time(store, conn):
    store.save_position(conn, {"position_id": "p1"})
    first = dict(conn.execute("SELECT * FROM t_positions").fetchone())
    store.save_position(conn, {"position_id": "p1", "status": "closed"})
    second = dict(conn.execute("SELECT * FROM t_positions").fetchone())

    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] is not None


# --------------------------------------------------------------------------- the swallow contract


def test_a_telemetry_write_never_raises(store, conn):
    """Telemetry may never cost a trade or a tick."""
    store.record_mark(conn, position_id="p1", no_such_column=1)  # must not raise
    assert list(conn.execute("SELECT * FROM t_marks")) == []


def test_a_telemetry_write_still_works_when_it_is_valid(store, conn):
    store.record_mark(conn, position_id="p1", usable=1)
    assert len(list(conn.execute("SELECT * FROM t_marks"))) == 1


def test_state_writes_do_NOT_swallow(store, conn):
    """The other half of the contract, and the one that matters more.

    A position or a delivered share is position STATE, not a record of one. Losing it silently
    would leave a week whose legs are settled and whose shares nobody knows are held.
    """
    with pytest.raises(sqlite3.Error):
        store.save_position(conn, {"position_id": "p1", "no_such_column": 1})


# --------------------------------------------------------------------------- measurement breaks


def test_a_measurement_break_is_recorded_once_and_re_runs_are_no_ops(store, conn):
    for _ in range(3):
        store.record_measurement_break(conn, break_date="2026-08-20", key="cadence", note="60s->15s")
    rows = list(conn.execute("SELECT * FROM measurement_breaks"))
    assert len(rows) == 1
    assert rows[0]["note"] == "60s->15s"


# --------------------------------------------------------------------------- schema guards


def test_declared_columns_are_parsed_from_the_ddl(store):
    cols = store.declared_columns("t_positions")
    assert "position_id" in cols and "status" in cols
    assert "UNIQUE" not in cols and "PRIMARY" not in cols


def test_stale_writer_columns_is_empty_when_code_and_file_agree(store, conn):
    assert store.stale_writer_columns(conn) == []


def test_stale_writer_columns_names_a_column_the_running_code_does_not_know(store, conn):
    """The flies 2026-08-05 shape: a newer checkout leaves a column an older one silently NULLs."""
    conn.execute("ALTER TABLE t_marks ADD COLUMN written_by_a_newer_checkout REAL")
    assert store.stale_writer_columns(conn) == ["t_marks.written_by_a_newer_checkout"]


def test_migrate_adds_a_declared_column_and_reports_it(conn):
    store = ledgerstore.LedgerStore("t_", SCHEMA, {"t_marks": {"later_column": "REAL"}})
    assert store.migrate(conn) == ["t_marks.later_column"]
    assert store.migrate(conn) == [], "additive migration must be idempotent"


# --------------------------------------------------------------------------- readers


def test_open_positions_filters_by_status(store, conn):
    store.save_position(conn, {"position_id": "p1", "status": "open"})
    store.save_position(conn, {"position_id": "p2", "status": "closed"})
    ids = [r["position_id"] for r in store.open_positions(conn, ("open",))]
    assert ids == ["p1"]
