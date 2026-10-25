"""B3: every closed record carries `r_multiple`, net over its defined max loss at entry; bwb's max
loss comes from its widths, not the overstated pre-2026-09-18 `entry_max_loss`."""

import sqlite3

from cherrypick.core import ledgers


def _bwb(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE bwb_positions (symbol TEXT, arm TEXT, gross_pnl REAL, fees REAL, entry_max_loss REAL,"
        " entry_narrow_width REAL, entry_wide_width REAL, entry_credit REAL, quantity INTEGER,"
        " closed_session TEXT, status TEXT)"
    )
    conn.executemany("INSERT INTO bwb_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return conn


def test_bwb_capital_and_r_come_from_the_widths():
    # 5/10 fly at a 0.90 credit: worst case 10 - 5 - 0.90 = 4.10 a share; the recorded 9.10 is the old,
    # overstated figure (narrow width added back).
    conn = _bwb([("SPX", "control", 100.0, 10.0, 9.10, 5.0, 10.0, 0.90, 1, "2026-09-04", "closed")])
    [r] = ledgers.READERS["bwb_132"](conn)
    assert r["capital"] == 410.0
    assert r["r_multiple"] == round(90.0 / 410.0, 4)


def test_bwb_falls_back_to_entry_max_loss_without_widths():
    conn = _bwb([("SPX", "control", -50.0, 0.0, 4.0, None, None, None, 2, "2026-09-04", "closed")])
    [r] = ledgers.READERS["bwb_132"](conn)
    assert r["capital"] == 800.0 and r["r_multiple"] == -0.0625


def test_r_is_none_without_defined_risk():
    assert ledgers.r_multiple({"net_pnl": 10.0, "capital": 1000.0}, "contango_etf") is None
    assert ledgers.r_multiple({"net_pnl": 10.0, "capital": None}, "curve_vx") is None
    assert ledgers.r_multiple({"net_pnl": None, "capital": 100.0}, "curve_vx") is None
    assert ledgers.r_multiple({"net_pnl": -100.0, "capital": 100.0}, "pmcc_99") == -1.0


def test_every_reader_carries_the_field():
    assert set(ledgers.READERS) >= ledgers.R_SCHEMAS
    conn = _bwb([])
    assert ledgers.READERS["bwb_132"](conn, "2026-01-01", None) == []
