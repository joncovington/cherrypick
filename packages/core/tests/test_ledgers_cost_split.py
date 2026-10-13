"""Every closed record splits its total cost into fees, settlement and slippage-with-basis, and the
parts add back to the net: gross - fees - settlement - (slippage if charged) == net."""

import sqlite3

import pytest

from cherrypick.core import ledgers


def _identity(rec) -> float:
    charged = rec["slippage"] if rec["slippage_basis"] == ledgers.SLIPPAGE_COST and rec["slippage"] else 0.0
    return rec["gross_pnl"] - rec["fees"] - (rec["settlement"] or 0.0) - charged


def test_every_schema_declares_a_slippage_basis():
    assert set(ledgers.SLIPPAGE_BASIS) == set(ledgers.READERS)


def test_meic_slippage_is_inside_gross_and_never_taken_out_of_fees():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL,"
        " exit_time TEXT, slippage_dollars REAL, settlement_fees REAL, wing_width REAL, net_credit REAL,"
        " quantity INTEGER)"
    )
    conn.execute(
        "INSERT INTO ic_trades VALUES (1, 'SPX', 'c', 100.0, 12.0, '2026-10-09 16:00', 8.0, 5.0, 5, 1.2, 1)"
    )
    (rec,) = ledgers.READERS["meic_ic"](conn)
    assert (rec["fees"], rec["settlement"], rec["slippage"]) == (7.0, 5.0, 8.0)
    assert rec["slippage_basis"] == "in_gross" and rec["entry_credit"] == pytest.approx(120.0)
    assert _identity(rec) == pytest.approx(rec["net_pnl"])


def test_an_unrecorded_cost_leaves_fees_unrecorded_never_zero():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL,"
        " exit_time TEXT)"
    )
    conn.execute("INSERT INTO ic_trades VALUES (1, 'SPX', 'c', NULL, NULL, '2026-06-29 16:00')")
    (rec,) = ledgers.READERS["meic_ic"](conn)
    assert rec["cost"] is None and rec["fees"] is None and rec["net_pnl"] is None


def test_a_charged_slippage_and_settlement_each_come_out_of_the_total_once():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE curve_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_slippage REAL,"
        " exit_slippage REAL, entry_max_loss REAL, entry_credit REAL, quantity INTEGER, closed_session TEXT,"
        " status TEXT, settlement_fees REAL)"
    )
    conn.execute(
        "INSERT INTO curve_positions VALUES ('VXX', 'control', 50.0, 20.0, 4.0, 6.0, 1.7, 0.3, 1,"
        " '2026-10-09', 'closed', 3.0)"
    )
    (rec,) = ledgers.READERS["curve_vx"](conn)
    assert (rec["fees"], rec["settlement"], rec["slippage"]) == (7.0, 3.0, 10.0)
    assert rec["cost"] == 20.0 and _identity(rec) == pytest.approx(rec["net_pnl"])


def test_an_unrecorded_settlement_stays_inside_fees_and_reads_none():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE dc_positions (symbol TEXT, arm TEXT, structure TEXT, gross_pnl REAL, fees REAL,"
        " entry_slippage REAL, exit_slippage REAL, entry_debit REAL, quantity INTEGER, closed_session TEXT,"
        " status TEXT)"
    )
    conn.execute(
        "INSERT INTO dc_positions VALUES ('SPY', 'control', 'dc_4_7', 30.0, 9.0, 1.0, 1.0, 2.0, 1,"
        " '2026-10-09', 'closed')"
    )
    (rec,) = ledgers.READERS["dc_week"](conn)
    assert rec["settlement"] is None and rec["fees"] == 7.0
    assert _identity(rec) == pytest.approx(rec["net_pnl"])
