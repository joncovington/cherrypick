"""The earnings reader honours start/end on the LOCAL session date.

Until 2026-10-17 `_earnings_closed` took start and end and applied neither: its comment said run()
would, and only the orchestrator's run() did. The metrics, analysis and compare bridges therefore
read earnings' whole history whatever window they asked for -- the console's P&L calendar showed
July earnings sessions under a window that opened on 2026-08-21.
"""

import sqlite3
from datetime import datetime

from cherrypick.core import ledgers


def _conn(closes):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL, "
        "exit_cost REAL, closed_at REAL)"
    )
    conn.executemany(
        "INSERT INTO trades VALUES ('AAPL', 'default', 'iron_fly', 10.0, 1.0, 1.0, ?)",
        [(c,) for c in closes],
    )
    return conn


def _local(y, m, d, hh=10):
    return datetime(y, m, d, hh).timestamp()  # naive: the machine's local time, as the module stamps


def test_start_and_end_bound_the_local_session():
    conn = _conn([_local(2026, 7, 16), _local(2026, 8, 21), _local(2026, 9, 1), _local(2026, 10, 2)])
    out = ledgers.READERS["earnings"](conn, "2026-08-21", "2026-09-30")
    assert sorted(r["session"] for r in out) == ["2026-08-21", "2026-09-01"]


def test_an_evening_close_stays_on_its_own_local_day():
    # 20:30 local is the next day in UTC for any zone behind it; the bound must read the local day.
    conn = _conn([_local(2026, 8, 20, 20)])
    assert ledgers.READERS["earnings"](conn, "2026-08-21", None) == []
    assert [r["session"] for r in ledgers.READERS["earnings"](conn, "2026-08-20", "2026-08-20")] == [
        "2026-08-20"
    ]


def test_no_bounds_reads_everything():
    conn = _conn([_local(2026, 7, 16), _local(2026, 10, 2)])
    assert len(ledgers.READERS["earnings"](conn)) == 2
