"""`ledgers.READERS[...]`'s `outcome` field (2026-10-26, the backtest run cards' exit breakdown): how
a trade ended, in the module's own words. MEIC's recorded `exit_reason`; flies' `fly_outcome`, the
rule flies.analytics scores its closes with. None on a ledger from before the columns."""

from __future__ import annotations

import sqlite3

from cherrypick.core import ledgers


def _conn(schema_sql: str, insert_sql: str, rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(schema_sql)
    conn.executemany(insert_sql, rows)
    conn.commit()
    return conn


def test_meic_reads_its_recorded_exit_reason_and_an_older_ledger_reads_none():
    conn = _conn(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT, "
        "exit_reason TEXT)",
        "INSERT INTO ic_trades VALUES (?,?,?,?,?,?)",
        [("SPX", "control", 50.0, 2.0, "2026-01-02T16:00", "per_side_stop")],
    )
    assert [r["outcome"] for r in ledgers.READERS["meic_ic"](conn)] == ["per_side_stop"]
    old = _conn(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT)",
        "INSERT INTO ic_trades VALUES (?,?,?,?,?)",
        [("SPX", "control", 50.0, 2.0, "2026-01-02T16:00")],
    )
    assert [r["outcome"] for r in ledgers.READERS["meic_ic"](old)] == [None]


def test_flies_reads_completed_stranded_or_expired_otm():
    conn = _conn(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, fees REAL, "
        "trade_date TEXT, status TEXT, kind TEXT, completed_at TEXT, expiry_payoff REAL)",
        "INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("SPX", "control", "legged", 90.0, 4.0, "2026-01-02", "settled", "fly", None, 0.0),
            ("SPX", "control", "legged", 60.0, 4.0, "2026-01-02", "settled", "vertical", "10:31", 0.0),
            ("SPX", "control", "legged", -300.0, 2.0, "2026-01-02", "settled", "vertical", None, -350.0),
            ("SPX", "control", "legged", 40.0, 2.0, "2026-01-02", "settled", "vertical", None, 0.0),
        ],
    )
    assert [r["outcome"] for r in ledgers.READERS["fly_book"](conn)] == [
        ledgers.FLY_COMPLETED,
        ledgers.FLY_COMPLETED,
        ledgers.FLY_STRANDED,
        ledgers.FLY_EXPIRED_OTM,
    ]


def test_a_flies_ledger_without_the_outcome_columns_reads_none():
    conn = _conn(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, fees REAL, "
        "trade_date TEXT, status TEXT, kind TEXT)",
        "INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?,?)",
        [("SPX", "control", "legged", 90.0, 4.0, "2026-01-02", "settled", "fly")],
    )
    assert [r["outcome"] for r in ledgers.READERS["fly_book"](conn)] == [None]
