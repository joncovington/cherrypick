"""`ledgers.ROW_NET`: the per-schema net rule over one raw row, for surfaces that show one trade.

MEIC's and earnings' `pnl` columns are GROSS; the trade notifier printed them under "P&L" until
2026-10-12. Its exit cards now call this table, so it must answer for every schema a reader does.
"""

import sqlite3

import pytest

from cherrypick.core import ledgers


def _row(**cols):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT " + ", ".join(f"? AS {k}" for k in cols), tuple(cols.values())).fetchone()


def test_every_closed_reader_schema_has_a_row_net_rule():
    assert set(ledgers.ROW_NET) == set(ledgers.READERS)


def test_meic_net_is_pnl_less_fees_and_earnings_less_both_cost_legs():
    assert ledgers.ROW_NET["meic_ic"](_row(pnl=100.0, fees=7.5)) == pytest.approx(92.5)
    assert ledgers.ROW_NET["earnings"](_row(pnl=50.0, entry_cost=6.0, exit_cost=4.0)) == pytest.approx(40.0)
    assert ledgers.ROW_NET["bwb_132"](_row(gross_pnl=10.0, fees=None)) == pytest.approx(10.0)


def test_an_unrecorded_gross_is_none_not_minus_the_fees():
    assert ledgers.ROW_NET["meic_ic"](_row(pnl=None, fees=3.0)) is None
    assert ledgers.ROW_NET["contango_etf"](_row(net_pnl=None)) is None


def test_the_meic_reader_agrees_with_the_row_rule():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, pnl REAL, "
        "fees REAL, exit_time TEXT)"
    )
    conn.execute("INSERT INTO ic_trades VALUES (1, 'SPX', 'control', 100.0, 7.5, '2026-10-09 16:00:00')")
    (rec,) = ledgers.READERS["meic_ic"](conn)
    row = conn.execute("SELECT * FROM ic_trades").fetchone()
    assert rec["net_pnl"] == ledgers.ROW_NET["meic_ic"](row)
