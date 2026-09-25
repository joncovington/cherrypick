"""The code names earnings' arm column `arm`; a ledger that still says `profile` is refused.

Every earnings command opens its ledger through `_conn()`, which runs the DDL and the additive
migrations -- and the migration list now names `arm`. Against a ledger still saying `profile` it
would ADD `arm` (NOT NULL DEFAULT 'default') beside it: history under one name, every new row under
the other, and `entry_reviews`' upsert keyed on the new one while the old rows sit under the old.
So `_conn()` refuses first, for the paper ledger and the live one alike.
"""

from __future__ import annotations

import sqlite3

import pytest
from cherrypick.core import db as core_db

from cherrypick.earnings import db, db_paper

TABLES = ("trades", "scan_log", "entry_reviews", "management_events")


def _columns(path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _pre_rename(module, tmp_path, monkeypatch):
    """Today's schema with the arm column put back to its pre-2026-09-24 name."""
    path = tmp_path / f"{module.__name__.rsplit('.', 1)[-1]}.db"
    monkeypatch.setattr(module, "DB_PATH", path)
    module._conn().close()
    conn = sqlite3.connect(path)
    for table in TABLES:
        if "arm" in _columns(path, table):
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN arm TO profile")
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("module", [db_paper, db], ids=["paper", "live"])
def test_a_ledger_that_still_says_profile_is_refused_and_left_untouched(module, tmp_path, monkeypatch):
    path = _pre_rename(module, tmp_path, monkeypatch)
    with pytest.raises(core_db.PreRenameLedger, match="arm_column_migrate.py --only profile"):
        module._conn()
    for table in ("trades", "scan_log"):
        cols = _columns(path, table)
        assert "profile" in cols and "arm" not in cols, f"{table} was touched: {sorted(cols)}"


@pytest.mark.parametrize("module", [db_paper, db], ids=["paper", "live"])
def test_a_renamed_ledger_opens_normally(module, tmp_path, monkeypatch):
    path = _pre_rename(module, tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    for table in TABLES:
        if "profile" in _columns(path, table):
            core_db.rename_column(conn, table, "profile", "arm")  # what the migration script does
    conn.close()
    module._conn().close()
    for table in ("trades", "scan_log"):
        cols = _columns(path, table)
        assert "arm" in cols and "profile" not in cols


def test_a_save_spec_still_sending_profile_is_filed_under_that_arm(tmp_path, monkeypatch):
    # A caller that predates the rename must not have its row silently filed under `default`.
    assert db_paper._arm_of({"profile": "strat_test"}) == "strat_test"
    assert db_paper._arm_of({"arm": "a", "profile": "b"}) == "a"
    assert db_paper._arm_of({}) == "default"
    assert db._arm_of({"profile": "strat_test"}) == "strat_test"
