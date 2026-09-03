"""`ledgers.READERS[...]`'s new `max_profit` field (console Phase 3a, for `core.metrics.capture_rate`):
the structure's own defined ceiling at expiry, additive to the existing closed-trade record shape.

Only ever a number for meic_ic and curve_vx -- plain credit structures whose ceiling IS the credit
received, using columns each reader's `capital` computation already trusts. Every other schema
stays `None` (a debit structure whose profit depends on where the underlying settles, or a
structure whose ceiling needs strike geometry the per-trade row does not carry) -- asserted here so
a future change cannot silently start guessing at one of those.
"""

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


# --------------------------------------------------------------------------- meic_ic (computed)


def _meic_conn(rows):
    return _conn(
        "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, "
        "exit_time TEXT, slippage_dollars REAL, wing_width REAL, net_credit REAL, "
        "quantity INTEGER, dollar_multiplier REAL)",
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, "
        "wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def test_meic_max_profit_is_net_credit_times_multiplier_times_quantity():
    conn = _meic_conn(
        [("SPX", "control", 120.0, 5.0, "2026-08-20T15:45:00", None, 10.0, 3.0, 2, 100.0)]
    )
    out = ledgers.READERS["meic_ic"](conn)
    assert out[0]["max_profit"] == 3.0 * 100.0 * 2  # 600.0
    # capital and max_profit come from the same wing_width/net_credit/quantity/multiplier row,
    # and their sum should reconstruct the wing's dollar width x multiplier x quantity.
    assert out[0]["capital"] + out[0]["max_profit"] == (10.0 - 3.0 + 3.0) * 100.0 * 2


def test_meic_max_profit_is_none_without_the_credit_columns():
    conn = _meic_conn([("SPX", "control", 120.0, 5.0, "2026-08-20T15:45:00", None, None, None, None, None)])
    out = ledgers.READERS["meic_ic"](conn)
    assert out[0]["max_profit"] is None


# --------------------------------------------------------------------------- curve_vx (computed)


def _curve_conn(rows):
    return _conn(
        "CREATE TABLE curve_positions (symbol TEXT, book TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, exit_slippage REAL, entry_max_loss REAL, entry_credit REAL, "
        "quantity INTEGER, closed_session TEXT, status TEXT)",
        "INSERT INTO curve_positions (symbol, book, gross_pnl, fees, entry_slippage, "
        "exit_slippage, entry_max_loss, entry_credit, quantity, closed_session, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'closed')",
        rows,
    )


def test_curve_max_profit_is_entry_credit_times_100_times_quantity():
    conn = _curve_conn([("VXX", "control", 40.0, 2.0, None, None, 1.5, 0.5, 3, "2026-08-20")])
    out = ledgers.READERS["curve_vx"](conn)
    assert out[0]["max_profit"] == 0.5 * 100 * 3  # 150.0


def test_curve_max_profit_is_none_without_entry_credit():
    conn = _curve_conn([("VXX", "control", 40.0, 2.0, None, None, 1.5, None, 3, "2026-08-20")])
    out = ledgers.READERS["curve_vx"](conn)
    assert out[0]["max_profit"] is None


# --------------------------------------------------------------------------- every other schema: always None


def test_earnings_max_profit_is_always_none():
    conn = _conn(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL, "
        "exit_cost REAL, closed_at REAL, capital_at_risk REAL)",
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)",
        [("AAPL", "core", "put_credit_spread", 50.0, 2.0, 2.0, 1755000000.0, 400.0)],
    )
    out = ledgers.READERS["earnings"](conn)
    assert out[0]["max_profit"] is None


def test_flies_max_profit_is_always_none():
    conn = _conn(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, "
        "fees REAL, trade_date TEXT, status TEXT)",
        "INSERT INTO fly_positions VALUES (?,?,?,?,?,?,'settled')",
        [("SPX", "control", "outright", 25.0, 1.0, "2026-08-20")],
    )
    out = ledgers.READERS["fly_book"](conn)
    assert out[0]["max_profit"] is None


def test_calendars_max_profit_is_always_none():
    conn = _conn(
        "CREATE TABLE dc_positions (symbol TEXT, book TEXT, structure TEXT, gross_pnl REAL, "
        "fees REAL, entry_slippage REAL, exit_slippage REAL, entry_debit REAL, quantity INTEGER, "
        "closed_session TEXT, status TEXT)",
        "INSERT INTO dc_positions VALUES (?,?,?,?,?,?,?,?,?,?,'closed')",
        [("SPY", "control", "dc_4_7", 30.0, 2.0, None, None, 1.2, 1, "2026-08-20")],
    )
    out = ledgers.READERS["dc_week"](conn)
    assert out[0]["max_profit"] is None


def test_pmcc_max_profit_is_always_none():
    conn = _conn(
        "CREATE TABLE pmcc_positions (symbol TEXT, book TEXT, gross_pnl REAL, fees REAL, "
        "entry_slippage REAL, exit_slippage REAL, net_debit REAL, quantity INTEGER, "
        "closed_session TEXT, status TEXT)",
        "INSERT INTO pmcc_positions VALUES (?,?,?,?,?,?,?,?,?,'closed')",
        [("TQQQ", "control", 60.0, 3.0, None, None, 20.0, 1, "2026-08-20")],
    )
    out = ledgers.READERS["pmcc_99"](conn)
    assert out[0]["max_profit"] is None


def test_bwb_max_profit_is_always_none():
    conn = _conn(
        "CREATE TABLE bwb_positions (symbol TEXT, book TEXT, gross_pnl REAL, fees REAL, "
        "entry_max_loss REAL, quantity INTEGER, closed_session TEXT, status TEXT)",
        "INSERT INTO bwb_positions VALUES (?,?,?,?,?,?,?,'closed')",
        [("SPX", "control", 45.0, 2.0, 3.0, 1, "2026-08-20")],
    )
    out = ledgers.READERS["bwb_132"](conn)
    assert out[0]["max_profit"] is None
