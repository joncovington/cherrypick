"""The suite tests earnings' readers against a ledger schema it cannot build itself.

The console's CI job has no Python, and the orchestrator/advisor tests have no earnings install, so
their fixture for an earnings paper ledger is a checked-in SQL file. A hand-kept copy of a schema is
exactly the list that drifts, so this test is what the fixture is: earnings' own `_conn()` -- the
DDL plus every additive migration, as every command runs it -- and its `sqlite_master` written out.
It fails the moment the two differ.

Regenerate after a schema change with:  REGEN_CONSOLE_FIXTURE=1 pytest tests/test_console_schema_fixture.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cherrypick.earnings import db_paper

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "server"
    / "test"
    / "fixtures"
    / "earnings-ledger-schema.sql"
)

HEADER = (
    "-- earnings' paper ledger schema, as its own _conn() builds it. GENERATED -- do not edit by hand.\n"
    "-- Pinned by packages/earnings/tests/test_console_schema_fixture.py; regenerate with\n"
    "-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.\n"
)


def _schema(tmp_path, monkeypatch) -> str:
    path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    db_paper._conn().close()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            " ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
        ).fetchall()
    finally:
        conn.close()
    return HEADER + "".join(f"{sql.strip()};\n" for _type, _name, sql in rows)


def test_the_fixture_is_the_schema_earnings_actually_builds(tmp_path, monkeypatch):
    built = _schema(tmp_path, monkeypatch)
    if os.environ.get("REGEN_CONSOLE_FIXTURE"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(built, encoding="utf-8", newline="\n")
    assert FIXTURE.exists(), f"{FIXTURE} missing -- run with REGEN_CONSOLE_FIXTURE=1"
    assert FIXTURE.read_text(encoding="utf-8") == built, (
        "the earnings fixture no longer matches earnings' schema -- regenerate it "
        "(REGEN_CONSOLE_FIXTURE=1) and re-run the console, orchestrator and advisor tests against it"
    )
