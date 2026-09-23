"""The arm-column migration's preflight, which is read-only and must stay that way.

It lives in `scripts/` (outside every package, the suite's fence for tooling) and is tested from
here because core owns `rename_column`, the thing it reports on.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "arm_column_preflight",
    Path(__file__).resolve().parents[3] / "scripts" / "arm_column_preflight.py",
)
preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preflight)


def _db(path: Path, table: str, column: str, rows: int = 2, index: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {table} (session TEXT, {column} TEXT, pnl REAL)")
    if index:
        conn.execute(f"CREATE INDEX idx_{table}_{column} ON {table}({column})")
    conn.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)", [("d", "control", 1.0)] * rows)
    conn.commit()
    conn.close()


def test_it_finds_every_spelling_including_the_live_ledgers_the_plan_missed(tmp_path):
    """The migration plan's inventory was hand-written and listed the paper ledgers only. It missed
    four LIVE ones carrying the same columns and read by the same code. Walking the directory is
    what stops a hand-kept list from doing that again, so the live files must be found AND labelled
    as live -- a migration that skipped them would leave the live read path pointing at a column
    that no longer exists."""
    (tmp_path / "bwb").mkdir()
    (tmp_path / "meic").mkdir()
    _db(tmp_path / "bwb" / "paper_trades.db", "bwb_positions", "book")
    _db(tmp_path / "bwb" / "live_trades.db", "bwb_positions", "book")
    _db(tmp_path / "meic" / "meic_trades.db", "ic_trades", "risk_profile")

    found = preflight.scan(tmp_path)
    by_kind = {r["kind"] for r in found}
    assert by_kind == {"paper", "live"}
    assert {r["from"] for r in found} == {"book", "risk_profile"}
    assert all(r["done"] is False for r in found)


def test_an_already_migrated_table_reports_done_and_is_not_pending(tmp_path):
    """The after-pass has to be able to say "nothing pending", or the run cannot be verified."""
    (tmp_path / "flies").mkdir()
    _db(tmp_path / "flies" / "paper_trades.db", "fly_positions", "arm")

    found = preflight.scan(tmp_path)
    assert [r["done"] for r in found] == [True]
    assert [r["from"] for r in found] == [None]


def test_a_half_migrated_table_is_flagged_rather_than_counted_as_done(tmp_path):
    """Both names at once means an interrupted run. Reporting it as done would hide rows split
    across two columns; reporting it as pending would invite a second rename that then refuses."""
    (tmp_path / "pmcc").mkdir()
    path = tmp_path / "pmcc" / "paper_trades.db"
    _db(path, "pmcc_positions", "book")
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE pmcc_positions ADD COLUMN arm TEXT")
    conn.commit()
    conn.close()

    row = preflight.scan(tmp_path)[0]
    assert row["half"] is True and row["done"] is False


def test_it_records_the_indexes_that_a_rebuild_would_have_dropped(tmp_path):
    (tmp_path / "curve").mkdir()
    _db(tmp_path / "curve" / "paper_trades.db", "curve_positions", "book", index=True)
    assert preflight.scan(tmp_path)[0]["indexes"], "no index recorded"


def test_the_preflight_never_writes(tmp_path):
    """It is the thing you run to decide whether to run the migration. If it could write, it would
    be part of the migration."""
    (tmp_path / "bwb").mkdir()
    path = tmp_path / "bwb" / "paper_trades.db"
    _db(path, "bwb_positions", "book")
    before = path.read_bytes()

    preflight.scan(tmp_path)

    assert path.read_bytes() == before


def test_a_file_that_is_not_a_database_is_skipped_rather_than_fatal(tmp_path):
    """`.doltcfg/privileges.db` is on this box and is not SQLite. A preflight that died on it would
    report nothing about the 41 tables that matter."""
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "privileges.db").write_text("not a database", encoding="utf-8")
    _db(tmp_path / "junk" / "paper_trades.db", "t", "book")

    assert len(preflight.scan(tmp_path)) == 1
