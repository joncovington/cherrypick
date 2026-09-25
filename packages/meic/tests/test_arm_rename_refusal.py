"""The code names meic's arm column `arm`; a ledger that still says `risk_profile` is refused.

Refused rather than migrated, and refused before anything else touches the schema. The paper loop
runs `init_db` on every iteration, and `_migrate` ADDs any `ic_trades` column it finds missing -- so
without this, new code starting against an un-renamed ledger would add `arm` beside `risk_profile`
within seconds: history in one column, every new row in the other, both readable, nothing raised.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import db  # noqa: E402


def _pre_rename_ledger(tmp_path, monkeypatch) -> str:
    """A ledger exactly as it stood before 2026-09-24: today's schema with the old column name."""
    path = str(tmp_path / "meic_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    conn = sqlite3.connect(path)
    for table in ("ic_trades", "entry_attempts"):
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN arm TO risk_profile")
    conn.commit()
    conn.close()
    return path


def _columns(path: str, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_init_db_refuses_a_ledger_that_still_says_risk_profile(tmp_path, monkeypatch, capsys):
    path = _pre_rename_ledger(tmp_path, monkeypatch)
    capsys.readouterr()
    with pytest.raises(db.PreRenameLedger, match="arm_column_migrate"):
        db.cmd_init_db(None)
    for table in ("ic_trades", "entry_attempts"):
        cols = _columns(path, table)
        assert "risk_profile" in cols and "arm" not in cols, f"{table} was touched: {sorted(cols)}"


def test_migrate_refuses_on_its_own_too(tmp_path, monkeypatch):
    # _migrate is reachable without cmd_init_db; it must not be the path that splits the column.
    path = _pre_rename_ledger(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    try:
        with pytest.raises(db.PreRenameLedger):
            db._migrate(conn)
    finally:
        conn.close()
    assert "arm" not in _columns(path, "ic_trades")


def test_a_renamed_ledger_initialises_normally(tmp_path, monkeypatch, capsys):
    path = _pre_rename_ledger(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    for table in ("ic_trades", "entry_attempts"):
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN risk_profile TO arm")
    conn.commit()
    conn.close()
    db.cmd_init_db(None)  # the migration script's result: must pass straight through
    assert "arm" in _columns(path, "ic_trades") and "risk_profile" not in _columns(path, "ic_trades")
