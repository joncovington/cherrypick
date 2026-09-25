"""Every Python reader of a meic ledger outside meic, under each name its arm column has had.

meic's column is `risk_profile` until its own rename window and `arm` after it. These readers run
across that window -- the notifier every minute, reconcile and the report on a schedule -- and on
the live ledger as well as the paper one, which need not be on the same side of it.

The defect this guards shipped with the 2026-09-23 `book` rename: the notifier's formatters still
read the old column, raised on the first event, and every later pass re-sent the day's flies and
earnings events -- 7,726 notifications for 553 events. So each reader must return the arm BY NAME
under both spellings, and the same thing under both.

The schema is meic's own, generated from its `cmd_init_db` and pinned by meic's
test_console_schema_fixture.py -- not a hand-kept copy that could drift from the real ledger.
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
    / "meic-ledger-schema.sql"
).read_text(encoding="utf-8")

ARM = "ctl-arm"
DAY = "2026-09-24"


def _ledger(tmp_path: Path, spelling: str) -> sqlite3.Connection:
    path = tmp_path / f"meic-{spelling}.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for table in ("ic_trades", "entry_attempts"):
        current = core_db.arm_column(conn, table)
        if current != spelling:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {current} TO {spelling}")
    now = f"{DAY} 10:00:00-04:00"
    ins = (
        f"INSERT INTO ic_trades (trade_date, symbol, ic_order_id, status, {spelling}, put_strike,"
        " call_strike, wing_width, net_credit, quantity, pnl, fees, entry_time, exit_time, exit_reason,"
        " put_stop_cost, created_at, updated_at)"
        " VALUES (?, 'SPX', ?, ?, ?, 5950, 6050, 5, 1.2, 1, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    conn.execute(
        ins, (DAY, "ic-1", "stopped", ARM, -80.0, 4.5, now, f"{DAY} 11:00:00-04:00", "stop", 2.4, now, now)
    )
    conn.execute(ins, (DAY, "ic-2", "open", ARM, None, 2.25, now, None, None, None, now, now))
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


READERS = {
    "ledgers.meic_closed": lambda c: ledgers.READERS["meic_ic"](c),
    "reconcile.meic_open": lambda c: reconcile._meic_open(c),
    "notifier.entries": lambda c: (
        [tn._fmt_meic_entry(r) for r in tn._meic_new_entries(c, 0)]
        + [tn._embed_meic_entry(r) for r in tn._meic_new_entries(c, 0)]
    ),
    "notifier.exits": lambda c: (
        [tn._fmt_meic_exit(r) for r in tn._meic_new_exits(c, set())]
        + [tn._embed_meic_exit(r) for r in tn._meic_new_exits(c, set())]
    ),
    "notifier.stops": lambda c: (
        [tn._fmt_meic_stop(r, "put") for r in tn._meic_new_stops(c)]
        + [tn._embed_meic_stop(r, "put") for r in tn._meic_new_stops(c)]
    ),
    # No arm name in a day total -- the count is the evidence the prefix filter found the column.
    "notifier.day_totals": lambda c: {"n": tn._meic_day_totals(c, "SPX", DAY, ("ctl",))[0], "arm": ARM},
}


@pytest.mark.parametrize("name", sorted(READERS))
def test_every_reader_reads_the_arm_under_either_spelling(tmp_path, name):
    out = []
    for spelling in ("risk_profile", "arm"):
        conn = _ledger(tmp_path, spelling)
        try:
            out.append(repr(READERS[name](conn)))
        finally:
            conn.close()
    for spelling, text in zip(("risk_profile", "arm"), out, strict=True):
        assert ARM in text, f"{name} under `{spelling}` never names the arm -- read as empty"
    assert out[0] == out[1], f"{name} reads differently under `arm` than under `risk_profile`"


def test_the_day_total_actually_counts_the_arm(tmp_path):
    # The generic check above cannot see the day total's number; pin it on both spellings.
    for spelling in ("risk_profile", "arm"):
        conn = _ledger(tmp_path, spelling)
        try:
            assert tn._meic_day_totals(conn, "SPX", DAY, ("ctl",)) == (1, pytest.approx(-84.5))
        finally:
            conn.close()


def test_a_ledger_with_no_arm_column_is_a_sqlite_error_every_reader_already_handles(tmp_path):
    # Readers' existing `except sqlite3.Error` (an uninitialised ledger) must keep catching this.
    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.execute("CREATE TABLE ic_trades (id INTEGER, symbol TEXT)")
    with pytest.raises(sqlite3.Error):
        core_db.arm_column(conn, "ic_trades")
    conn.close()
