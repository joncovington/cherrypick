"""The fact pack's meic section, under each name meic's arm column has had.

meic's column is `risk_profile` until its own rename window and `arm` after it; the pack is built
four times a trading day, on whichever side of that window the ledger is. `_store.rows` is tolerant
by design -- a query it cannot run yields no rows -- so a pack that asked for a column that moved
would read every meic arm as absent without raising. That is the failure checked for here: the arm
must be in the section by name, identically, under both spellings.

The schema is meic's own (generated from its `cmd_init_db`, pinned by meic's
test_console_schema_fixture.py), not `fakes.MEIC_DDL`.
"""

import sqlite3
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
    / "meic-ledger-schema.sql"
).read_text(encoding="utf-8")

ARM = "ctl-arm"
DAY = "2026-09-24"


def _seed(spelling: str) -> None:
    db = paths.module_data_dir("meic") / "paper_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for table in ("ic_trades", "entry_attempts"):
        current = core_db.arm_column(conn, table)
        if current != spelling:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {current} TO {spelling}")
    now = f"{DAY} 10:00:00-04:00"
    conn.execute(
        f"INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, {spelling}, net_credit, pnl, fees,"
        " put_max_cost, exit_time, created_at, updated_at)"
        " VALUES (?, 'SPX', 'ic-1', 'stopped', ?, 1.2, -80, 4.5, 2.4, ?, ?, ?)",
        (DAY, ARM, f"{DAY} 11:00:00-04:00", now, now),
    )
    conn.execute(
        f"INSERT INTO entry_attempts (ts, trade_date, {spelling}, symbol, outcome, block_detail)"
        " VALUES (?, ?, ?, 'SPX', 'cadence_blocked', 'too soon')",
        (f"{DAY}T10:00:00", DAY, ARM),
    )
    conn.commit()
    conn.close()


def test_the_meic_section_reads_the_arm_under_either_spelling(tmp_home):
    out = {}
    for spelling in ("risk_profile", "arm"):
        _seed(spelling)
        _store.QUERY_ERRORS.clear()
        section = factpack._meic(DAY)
        section.pop("advice_active", None)
        assert not _store.QUERY_ERRORS, f"queries refused under `{spelling}`: {_store.QUERY_ERRORS}"
        out[spelling] = repr(section)
        # Every per-arm block, not just one: each of the five queries names its own column.
        for key in ("entry_attempts", "top_block_details", "book_by_arm", "closed_with_stop_instrumentation"):
            assert ARM in repr(section[key]), f"{key} under `{spelling}` never names the arm"
        assert section["control_fired"]["fills_by_arm"] == {ARM: 1}
    assert out["risk_profile"] == out["arm"]
