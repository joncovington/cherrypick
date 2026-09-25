"""Every Python reader of an earnings ledger outside earnings, under each name its arm column has had.

`profile` until earnings' own rename window, `arm` after it. The same guard as
test_meic_arm_column.py, for the same reason: each reader must return the arm BY NAME under both
spellings, and the same thing under both -- an empty read is the failure, not a pass.

The schema is earnings' own, generated from its `_conn()` and pinned by earnings'
test_console_schema_fixture.py.
"""

import sqlite3
from pathlib import Path

import pytest
from cherrypick.core import db as core_db
from cherrypick.core import ledgers

from cherrypick.orchestrator import reconcile
from cherrypick.orchestrator import trade_notifier as tn

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "console"
    / "server"
    / "test"
    / "fixtures"
    / "earnings-ledger-schema.sql"
).read_text(encoding="utf-8")

ARM = "ctl-arm"
TABLES = ("trades", "scan_log", "entry_reviews", "management_events")
OPENED = 1790200800.0  # 2026-09-23 19:00 UTC


def _ledger(tmp_path: Path, spelling: str) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / f"earnings-{spelling}.db")
    conn.executescript(SCHEMA)
    for table in TABLES:
        current = core_db.arm_column(conn, table)
        if current != spelling:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {current} TO {spelling}")
    ins = (
        f"INSERT INTO trades (order_id, strategy, symbol, expiration, {spelling}, status, short_strike,"
        " entry_credit, pnl, entry_cost, exit_cost, quantity, capital_at_risk, opened_at, closed_at)"
        " VALUES (?, 'iron_fly', ?, '2026-09-25', ?, ?, 230, 1.5, ?, 2, 2, 1, 850, ?, ?)"
    )
    conn.execute(ins, ("ord-1", "AAPL", ARM, "closed", 40.0, OPENED, OPENED + 64800))
    conn.execute(ins, ("ord-2", "MSFT", ARM, "open", None, OPENED, None))
    conn.execute(
        f"INSERT INTO entry_reviews (scan_date, symbol, selected, reason, {spelling})"
        " VALUES ('2026-09-23', 'AAPL', 1, 'cleared', ?)",
        (ARM,),
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


READERS = {
    "ledgers.earnings_closed": lambda c: ledgers.READERS["earnings"](c),
    "ledgers.earnings_open": lambda c: ledgers.OPEN_READERS["earnings"](c),
    "reconcile.earnings_open": lambda c: reconcile._earnings_open(c),
    "notifier.entries": lambda c: (
        [tn._fmt_earnings_entry(r) for r in tn._earnings_new_entries(c, set())]
        + [tn._embed_earnings_entry(r) for r in tn._earnings_new_entries(c, set())]
    ),
    "notifier.exits": lambda c: (
        [tn._fmt_earnings_exit(r) for r in tn._earnings_new_exits(c, set())]
        + [tn._embed_earnings_exit(r) for r in tn._earnings_new_exits(c, set())]
    ),
    "notifier.reviews": lambda c: [repr(dict(r)) for r in tn._earnings_new_reviews(c, set())],
}


@pytest.mark.parametrize("name", sorted(READERS))
def test_every_reader_reads_the_arm_under_either_spelling(tmp_path, name):
    out = []
    for spelling in ("profile", "arm"):
        conn = _ledger(tmp_path, spelling)
        try:
            out.append(repr(READERS[name](conn)))
        finally:
            conn.close()
    for spelling, text in zip(("profile", "arm"), out, strict=True):
        assert ARM in text, f"{name} under `{spelling}` never names the arm -- read as empty"
    assert out[0] == out[1], f"{name} reads differently under `arm` than under `profile`"


def test_the_review_formatters_render_the_arm_under_either_spelling(tmp_path):
    # The review path is the one the reviews reader above feeds; pin its rendered output too.
    for spelling in ("profile", "arm"):
        conn = _ledger(tmp_path, spelling)
        try:
            (row,) = tn._earnings_new_reviews(conn, set())
            assert tn._arm(row) == ARM
        finally:
            conn.close()
