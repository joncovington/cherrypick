"""A closed row the ledger left without its money reads None (n/r), never 0.

`_contango_closed` reported `gross_pnl or 0.0` and `net_pnl or 0.0`, and meic's and earnings'
readers did the same with `pnl`/`fees`/costs -- so an unrecorded result entered every total as a
flat, costless trade. Only the schemas whose ledgers can close a row without its money are covered:
meic (the live book's 2026-06-29 stop has an exit_time and NULL pnl and fees), earnings (`save-close`
writes pnl and exit_cost from whatever the caller passed) and contango. The rest write their money
in the statement that closes the row.

The downstream half: `core.metrics.calibration_reading` (calibrate, the advisor's verdicts, the
console's metrics bridge) scores only the recorded records and counts the rest as `unscored`.
"""

from __future__ import annotations

import sqlite3

from cherrypick.core import ledgers
from cherrypick.core.metrics import calibration_reading, session_nets, worst_session


def _conn(schema_sql: str, insert_sql: str, rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(schema_sql)
    conn.executemany(insert_sql, rows)
    conn.commit()
    return conn


def _contango(rows):
    return _conn(
        "CREATE TABLE contango_positions (arm TEXT, symbol TEXT, shares REAL, entry_mid REAL, "
        "gross_pnl REAL, fees REAL, slippage REAL, net_pnl REAL, exit_session TEXT, status TEXT)",
        "INSERT INTO contango_positions VALUES (?,?,?,?,?,?,?,?,?,'closed')",
        rows,
    )


def test_contango_reads_its_stored_money_and_a_missing_figure_as_none():
    out = ledgers.READERS["contango_etf"](
        _contango(
            [
                ("control", "SVXY", 100, 50.0, 120.0, 2.0, 1.5, 116.5, "2026-10-06"),
                ("control", "SVXY", 100, 50.0, None, None, None, None, "2026-10-07"),
            ]
        )
    )
    recorded, missing = out
    assert (recorded["gross_pnl"], recorded["cost"], recorded["net_pnl"]) == (120.0, 3.5, 116.5)
    assert (missing["gross_pnl"], missing["cost"], missing["net_pnl"]) == (None, None, None)


def test_meic_closed_row_without_pnl_or_fees_is_none():
    conn = _conn(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT)",
        "INSERT INTO ic_trades VALUES (?,?,?,?,?)",
        [
            ("XSP", "control", 1.44, 4.74, "2026-06-25 17:25:53"),
            ("XSP", "control", None, None, "2026-06-29 13:31 ET"),
            ("XSP", "control", 4.0, None, "2026-06-30T09:57:00"),
        ],
    )
    a, b, c = ledgers.READERS["meic_ic"](conn)
    assert a["net_pnl"] == 1.44 - 4.74
    assert (b["gross_pnl"], b["cost"], b["net_pnl"]) == (None, None, None)
    assert (c["gross_pnl"], c["cost"], c["net_pnl"]) == (4.0, None, None)


def test_earnings_closed_row_without_pnl_or_a_cost_is_none():
    conn = _conn(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL, "
        "exit_cost REAL, closed_at REAL)",
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
        [
            ("AAPL", "default", "iron_fly", 50.0, 3.0, 2.0, 1790000000.0),
            ("AAPL", "default", "iron_fly", None, 3.0, None, 1790000000.0),
        ],
    )
    ok, missing = ledgers.READERS["earnings"](conn)
    assert (ok["cost"], ok["net_pnl"]) == (5.0, 45.0)
    assert (missing["gross_pnl"], missing["cost"], missing["net_pnl"]) == (None, None, None)


def test_calibration_reading_scores_the_recorded_and_counts_the_rest():
    records = [
        {"net_pnl": 10.0, "session": "2026-10-06", "slippage": 1.0},
        {"net_pnl": -4.0, "session": "2026-10-07", "slippage": 1.0},
        {"net_pnl": None, "session": "2026-10-07", "slippage": None},
    ]
    reading = calibration_reading(records)
    assert reading["sample"] == 2
    assert reading["unscored"] == 1
    assert reading["net_pnl"] == 6.0
    assert reading["win_rate"] == 0.5
    # The n/r record neither fails the slippage-coverage check nor joins a session's net.
    assert reading["slippage_coverage"] == 2
    assert session_nets(records) == [10.0, -4.0]
    assert worst_session(records) == {"session": "2026-10-07", "net": -4.0}
