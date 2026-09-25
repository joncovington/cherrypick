"""The fact pack's earnings section, under each name earnings' arm column has had.

`profile` until earnings' own rename window, `arm` after it. `_store.rows` is tolerant by design, so
a query naming a column that moved would read the section as empty without raising; the check is
that nothing is refused and the arm is in the section by name, identically, under both spellings.

The schema is earnings' own (generated from its `_conn()`, pinned by earnings'
test_console_schema_fixture.py).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from cherrypick.core import db as core_db

from cherrypick.advisor import factpack, paths
from cherrypick.advisor import store as _store

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "server"
    / "test"
    / "fixtures"
    / "earnings-ledger-schema.sql"
).read_text(encoding="utf-8")

ARM = "ctl-arm"
DAY = "2026-09-24"
TABLES = ("trades", "scan_log", "entry_reviews", "management_events")


def _seed(spelling: str) -> None:
    db = paths.module_data_dir("earnings") / "paper_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for table in TABLES:
        current = core_db.arm_column(conn, table)
        if current != spelling:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {current} TO {spelling}")
    # Closed at local noon on DAY, so the pack's localtime date() lands on DAY in any timezone.
    closed = datetime.fromisoformat(f"{DAY}T12:00:00").timestamp()
    ins = (
        f"INSERT INTO trades (order_id, strategy, symbol, expiration, {spelling}, status, entry_credit, pnl,"
        " entry_cost, exit_cost, capital_at_risk, opened_at, closed_at)"
        " VALUES (?, 'iron_fly', ?, '2026-09-25', ?, ?, 1.5, ?, 2, 2, 850, ?, ?)"
    )
    conn.execute(ins, ("ord-1", "AAPL", ARM, "closed", 40.0, closed - 86400, closed))
    conn.execute(ins, ("ord-2", "MSFT", ARM, "open", None, closed - 3600, None))
    conn.commit()
    conn.close()


def test_the_earnings_section_reads_the_arm_under_either_spelling(tmp_home):
    out = {}
    for spelling in ("profile", "arm"):
        _seed(spelling)
        _store.QUERY_ERRORS.clear()
        section = factpack._earnings(DAY)
        section.pop("advice_active", None)
        assert not _store.QUERY_ERRORS, f"queries refused under `{spelling}`: {_store.QUERY_ERRORS}"
        for key in ("open_positions", "closed_today"):
            assert ARM in repr(section[key]), f"{key} under `{spelling}` never names the arm"
        out[spelling] = repr(section)
    assert out["profile"] == out["arm"]
