"""Every schema's ledger record carries the trade fields (entry/exit time and spot), 2026-10-17.

They are what core.analysis needs for an hour-of-entry breakdown and, next, a regime read at entry.
A ledger older than a column reads None for it, never zero; an early close has no settlement spot,
so its exit spot is None rather than a guess.
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from cherrypick.core import ledgers

FIELDS = ("entry_time", "exit_time", "entry_spot", "exit_spot")


def _conn(sql, row):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(sql)
    conn.execute(f"INSERT INTO {sql.split()[2]} VALUES ({','.join('?' * len(row))})", row)
    return conn


MULTI_DAY = {
    # schema: (create, row) -- entry 10:00, closed 15:55, settled at 101.5 where it settled.
    "bwb_132": (
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, "
        "entry_max_loss REAL, "
        "quantity INTEGER, closed_session TEXT, status TEXT, entry_time TEXT, closed_at TEXT, "
        "entry_spot REAL, settlement_spot REAL)",
        ("SPX", "control", 10.0, 1.0, 500.0, 1, "2026-10-16", "closed", "2026-10-09T10:00:06-04:00",
         "2026-10-16T16:20:03-04:00", 7700.0, 7659.44),
    ),
    "dc_week": (
        "CREATE TABLE dc_positions (symbol TEXT, arm TEXT, structure TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, exit_slippage REAL, entry_debit REAL, quantity INTEGER, closed_session TEXT, "
        "status TEXT, entry_time TEXT, closed_at TEXT, entry_spot REAL, settlement_spot REAL)",
        ("SPY", "control", "dc_4_7", 10.0, 1.0, None, None, 1.2, 1, "2026-10-16", "closed",
         "2026-10-13T15:50:25-04:00", "2026-10-16T12:00:27-04:00", 762.85, None),
    ),
    "pmcc": (
        "CREATE TABLE pmcc_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_slippage REAL, "
        "exit_slippage REAL, net_debit REAL, quantity INTEGER, closed_session TEXT, status TEXT, "
        "entry_time TEXT, closed_at TEXT, entry_spot REAL, settlement_spot REAL)",
        ("XSP", "control", 10.0, 1.0, None, None, 20.0, 1, "2026-10-16", "closed",
         "2026-10-06T10:00:06-04:00", "2026-10-16T09:40:58-04:00", 541.9, None),
    ),
    "curve_vx": (
        "CREATE TABLE curve_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, "
        "exit_slippage REAL, entry_max_loss REAL, entry_credit REAL, quantity INTEGER, closed_session TEXT, "
        "status TEXT, entry_time TEXT, closed_at TEXT, entry_spot REAL, settlement_spot REAL)",
        ("VXX", "control", 10.0, 1.0, None, None, 1.5, 0.5, 1, "2026-10-07", "closed",
         "2026-09-29T10:00:57-04:00", "2026-10-07T15:55:37-04:00", 16.6, None),
    ),
    "contango_etf": (
        "CREATE TABLE contango_positions (arm TEXT, symbol TEXT, shares REAL, entry_mid REAL, "
        "gross_pnl REAL, "
        "fees REAL, slippage REAL, net_pnl REAL, exit_session TEXT, status TEXT, entry_time TEXT, "
        "exit_time TEXT, exit_mid REAL)",
        ("control", "SVXY", 100.0, 61.0, 88.0, 1.0, 0.5, 86.5, "2026-10-16", "closed",
         "2026-10-06T15:50:50-04:00", "2026-10-16T15:50:50-04:00", 61.88),
    ),
}  # fmt: skip


def test_every_multi_day_schema_carries_its_trade_fields():
    for schema, (create, row) in MULTI_DAY.items():
        [r] = ledgers.READERS[schema](_conn(create, row))
        assert r["entry_time"] and r["exit_time"], schema
        assert r["entry_spot"] is not None, schema


def test_a_settled_position_has_an_exit_spot_and_an_early_close_does_not():
    [settled] = ledgers.READERS["bwb_132"](_conn(*MULTI_DAY["bwb_132"]))
    assert settled["exit_spot"] == 7659.44
    [early] = ledgers.READERS["dc_week"](_conn(*MULTI_DAY["dc_week"]))
    assert early["exit_spot"] is None, "an early close has no settlement print: unknown, never a guess"


def test_contango_reads_the_held_funds_own_mid_as_its_spot():
    [r] = ledgers.READERS["contango_etf"](_conn(*MULTI_DAY["contango_etf"]))
    assert (r["entry_spot"], r["exit_spot"]) == (61.0, 61.88)


def test_a_ledger_older_than_the_columns_reads_none():
    conn = _conn(
        "CREATE TABLE curve_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, "
        "exit_slippage REAL, entry_max_loss REAL, entry_credit REAL, quantity INTEGER, closed_session TEXT, "
        "status TEXT)",
        ("VXX", "control", 10.0, 1.0, None, None, 1.5, 0.5, 1, "2026-10-07", "closed"),
    )
    [r] = ledgers.READERS["curve_vx"](conn)
    assert all(r[f] is None for f in FIELDS)


def test_earnings_reads_its_epoch_stamps_in_et_and_records_no_spot():
    # ET whatever the machine's zone: the hour buckets read every module's stamp as ET.
    et = ZoneInfo("America/New_York")
    opened = datetime(2026, 10, 14, 15, 30, tzinfo=et).timestamp()
    closed = datetime(2026, 10, 15, 9, 45, tzinfo=et).timestamp()
    conn = _conn(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL, "
        "exit_cost REAL, closed_at REAL, opened_at REAL)",
        ("AAPL", "default", "iron_fly", 10.0, 1.0, 1.0, closed, opened),
    )
    [r] = ledgers.READERS["earnings"](conn)
    assert r["entry_time"].startswith("2026-10-14T15:30") and r["exit_time"].startswith("2026-10-15T09:45")
    assert r["entry_spot"] is None and r["exit_spot"] is None
