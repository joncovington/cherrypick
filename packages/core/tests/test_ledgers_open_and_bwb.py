"""bwb's closed records carry their charged slippage, and no open reader counts a cancelled row.

Until 2026-10-12 `_bwb_closed` reported `slippage: None` for every row although the paper ledger
records `entry_slippage` on all of them (the console's bwbTradeCash reads it), so calibration's
`net_pnl_2x_slippage` read bwb as cost-insensitive; and the bwb/calendars/pmcc/curve open readers
said `status != 'closed'`, counting a live entry that never filled (`cancelled`) as carried capital.
"""

import sqlite3

import pytest

from cherrypick.core import ledgers


def _bwb(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE bwb_positions (position_id TEXT, symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, "
        "entry_max_loss REAL, quantity INTEGER, closed_session TEXT, entry_session TEXT, status TEXT, "
        "entry_slippage REAL, addon_slippage REAL, fees_source TEXT)"
    )
    conn.executemany(
        "INSERT INTO bwb_positions VALUES (?, 'SPX', 'control', 100, 20, 5, 1, '2026-10-09', '2026-10-02', "
        "?, ?, ?, ?)",
        rows,
    )
    return conn


def test_a_paper_bwb_row_reports_entry_plus_addon_slippage():
    conn = _bwb([("a", "closed", 3.0, 1.5, None), ("b", "closed", 2.0, None, None)])
    slips = sorted(r["slippage"] for r in ledgers.READERS["bwb_132"](conn))
    assert slips == [pytest.approx(2.0), pytest.approx(4.5)]


def test_a_live_bwb_row_charges_no_slippage_and_an_old_row_is_unknown():
    conn = _bwb([("live", "closed", 3.0, None, "reconciled"), ("old", "closed", None, None, None)])
    assert [r["slippage"] for r in ledgers.READERS["bwb_132"](conn)] == [None, None]


def test_a_ledger_without_the_slippage_columns_still_reads():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL, "
        "quantity INTEGER, closed_session TEXT, status TEXT)"
    )
    conn.execute("INSERT INTO bwb_positions VALUES ('SPX', 'control', 1, 0, 5, 1, '2026-10-09', 'closed')")
    (rec,) = ledgers.READERS["bwb_132"](conn)
    assert rec["slippage"] is None


@pytest.mark.parametrize("registry", ["OPEN_READERS", "HELD_READERS"])
def test_a_cancelled_bwb_entry_is_not_on_the_book(registry):
    conn = _bwb([("open", "open", 1.0, None, None), ("never", "cancelled", None, None, "modeled")])
    held = getattr(ledgers, registry)["bwb_132"](conn)
    assert len(held) == 1 and held[0]["capital"] == pytest.approx(500.0)


@pytest.mark.parametrize(
    "table, schema, cols",
    [
        ("dc_positions", "dc_week", "structure TEXT, entry_debit REAL"),
        ("pmcc_positions", "pmcc", "net_debit REAL"),
        ("curve_positions", "curve_vx", "entry_max_loss REAL"),
    ],
)
def test_the_other_open_readers_exclude_cancelled_too(table, schema, cols):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"CREATE TABLE {table} (symbol TEXT, arm TEXT, quantity INTEGER, entry_session TEXT, "
        f"status TEXT, {cols})"
    )
    for status in ("open", "short_settled", "closed", "cancelled"):
        conn.execute(
            f"INSERT INTO {table} (symbol, arm, quantity, entry_session, status) VALUES ('X', 'c', 1, '', ?)",
            (status,),
        )
    assert len(ledgers.OPEN_READERS[schema](conn)) == 2


def test_held_readers_answer_the_0dte_schemas_intraday():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, status TEXT, "
        "entry_time TEXT, exit_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, status, entry_time, exit_time) VALUES (?, 'c', ?, "
        "'2026-10-12 10:00:00-04:00', ?)",
        [("SPX", "open", None), ("SPX", "cancelled", None), ("XSP", "expired", "2026-10-12 16:00:00-04:00")],
    )
    assert ledgers.OPEN_READERS["meic_ic"](conn) == []  # the overnight view, empty by construction
    held = ledgers.HELD_READERS["meic_ic"](conn)
    assert [(r["symbol"], r["arm"], r["session"]) for r in held] == [("SPX", "c", "2026-10-12")]
    assert set(ledgers.HELD_READERS) == set(ledgers.OPEN_READERS)
