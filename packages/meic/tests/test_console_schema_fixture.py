"""The console tests meic's readers against a ledger schema it cannot build itself.

The console's CI job has Node and no Python, so its fixture for a meic ledger is a checked-in SQL
file. A hand-kept copy of a schema is exactly the list that drifts -- a reader passes against the
fixture and fails against the ledger -- so this test is what the fixture is: meic's own
`cmd_init_db`, run, and its `sqlite_master` written out. It fails the moment the two differ.

Regenerate after a schema change with:  REGEN_CONSOLE_FIXTURE=1 pytest tests/test_console_schema_fixture.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import db  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "server"
    / "test"
    / "fixtures"
    / "meic-ledger-schema.sql"
)

HEADER = (
    "-- meic's ledger schema, as its own cmd_init_db builds it. GENERATED -- do not edit by hand.\n"
    "-- Pinned by packages/meic/tests/test_console_schema_fixture.py; regenerate with\n"
    "-- REGEN_CONSOLE_FIXTURE=1 there after a schema change.\n"
)


def _schema(tmp_path, monkeypatch) -> str:
    path = str(tmp_path / "meic_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            " ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
        ).fetchall()
    finally:
        conn.close()
    return HEADER + "".join(f"{sql.strip()};\n" for _type, _name, sql in rows)


def test_the_console_fixture_is_the_schema_meic_actually_builds(tmp_path, monkeypatch, capsys):
    built = _schema(tmp_path, monkeypatch)
    capsys.readouterr()  # cmd_init_db prints its JSON status line
    if os.environ.get("REGEN_CONSOLE_FIXTURE"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(built, encoding="utf-8", newline="\n")
    assert FIXTURE.exists(), f"{FIXTURE} missing -- run with REGEN_CONSOLE_FIXTURE=1"
    assert FIXTURE.read_text(encoding="utf-8") == built, (
        "the console's meic fixture no longer matches meic's schema -- regenerate it "
        "(REGEN_CONSOLE_FIXTURE=1) and re-run the console tests against it"
    )
