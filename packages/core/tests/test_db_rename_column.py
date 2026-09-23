"""`core.db.rename_column` — the one piece of new machinery the arm migration needs.

The interesting assertions here are not that the rename happens. They are that the things a
REBUILD would have silently dropped survive it: indexes, uniqueness, and the rows themselves.
The suite's only prior schema-change precedent rebuilds a table and restores no index, which is
exactly why this exists rather than reusing that shape.
"""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.core import db as _db


def _ledger() -> sqlite3.Connection:
    """A table shaped like the ones this migration touches: an index, a uniqueness constraint that
    is a live ON CONFLICT target, and rows."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE trades (
            scan_date TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            profile   TEXT NOT NULL DEFAULT 'default',
            pnl       REAL,
            UNIQUE(scan_date, symbol, profile)
        );
        CREATE INDEX idx_trades_profile_date ON trades(profile, scan_date);
        INSERT INTO trades VALUES ('2026-09-22', 'SPX', 'control', 10.0),
                                  ('2026-09-22', 'SPX', 'width-5',  -4.0);
        """
    )
    return conn


def test_a_rename_carries_the_index_across():
    """The whole reason this is a RENAME and not a rebuild. `_migrate_enactment` -- the suite's only
    precedent -- restores no index, and every table this migration touches has at least one."""
    conn = _ledger()
    assert _db.rename_column(conn, "trades", "profile", "arm") is True

    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", ("idx_trades_profile_date",)
    ).fetchone()
    assert idx is not None, "the index was dropped"
    assert '"arm"' in idx[0] or "arm" in idx[0], f"the index still names the old column: {idx[0]}"


def test_a_rename_carries_the_uniqueness_constraint_across():
    """earnings' `UNIQUE(scan_date, symbol, profile)` is a live ON CONFLICT upsert target. A rename
    that dropped it would not fail here -- it would start duplicating rows in production."""
    conn = _ledger()
    _db.rename_column(conn, "trades", "profile", "arm")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO trades VALUES ('2026-09-22', 'SPX', 'control', 99.0)")


def test_the_rows_and_their_values_are_untouched():
    """Row identity in this suite embeds the arm's VALUE (`position_id = f"{symbol}:{book}:..."`),
    never the column name, so a column rename must not move a single value."""
    conn = _ledger()
    _db.rename_column(conn, "trades", "profile", "arm")

    rows = sorted(conn.execute("SELECT arm, pnl FROM trades").fetchall())
    assert rows == [("control", 10.0), ("width-5", -4.0)]


def test_running_it_twice_is_a_no_op_rather_than_an_error():
    """Every module's migration runs on startup, so this is called on already-migrated files."""
    conn = _ledger()
    assert _db.rename_column(conn, "trades", "profile", "arm") is True
    assert _db.rename_column(conn, "trades", "profile", "arm") is False


def test_a_half_migrated_table_is_refused_rather_than_guessed():
    """Both columns present means something wrote a half-migrated schema. Silently picking one
    hands back a table whose rows are split across two names -- an arm reporting half its history,
    with nothing raised. Refusing is the only safe answer."""
    conn = _ledger()
    conn.execute("ALTER TABLE trades ADD COLUMN arm TEXT")

    with pytest.raises(ValueError, match="half-migrated"):
        _db.rename_column(conn, "trades", "profile", "arm")


def test_a_table_missing_both_names_is_an_error_not_a_silent_skip():
    conn = _ledger()
    with pytest.raises(ValueError, match="neither"):
        _db.rename_column(conn, "trades", "nonexistent", "also_missing")


def test_an_absent_table_is_an_error():
    conn = _ledger()
    with pytest.raises(ValueError, match="no such table"):
        _db.rename_column(conn, "not_a_table", "profile", "arm")
