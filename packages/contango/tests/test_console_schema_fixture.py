"""The console tests contango's readers against a ledger schema it cannot build itself.

The console's CI job has Node and no Python, so its fixture for a contango ledger is a checked-in
SQL file. A hand-kept copy of a schema is exactly the list that drifts -- a reader passes against
the fixture and fails against the ledger -- so this test is what the fixture is: contango's own
`db.connect`, run, and its `sqlite_master` written out. It fails the moment the two differ.

Regenerate after a schema change with:  REGEN_CONSOLE_FIXTURE=1 pytest tests/test_console_schema_fixture.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cherrypick.contango import db

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "server"
    / "test"
    / "fixtures"
    / "contango-ledger-schema.sql"
)

HEADER = (
    "-- contango's ledger schema, as its own db.connect builds it. GENERATED -- do not edit by hand.\n"
    "-- Pinned by packages/contango/tests/test_console_schema_fixture.py; regenerate with\n"
    "-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.\n"
)


def _schema(tmp_path) -> str:
    path = str(tmp_path / "paper_trades.db")
    db.connect(path).close()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            " ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
        ).fetchall()
    finally:
        conn.close()
    return HEADER + "".join(f"{sql.strip()};\n" for _type, _name, sql in rows)


def test_the_console_fixture_is_the_schema_contango_actually_builds(tmp_path):
    built = _schema(tmp_path)
    if os.environ.get("REGEN_CONSOLE_FIXTURE"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(built, encoding="utf-8", newline="\n")
    assert FIXTURE.exists(), f"{FIXTURE} missing -- run with REGEN_CONSOLE_FIXTURE=1"
    assert FIXTURE.read_text(encoding="utf-8") == built, (
        "the console's contango fixture no longer matches contango's schema -- regenerate it "
        "(REGEN_CONSOLE_FIXTURE=1) and re-run the console tests against it"
    )
