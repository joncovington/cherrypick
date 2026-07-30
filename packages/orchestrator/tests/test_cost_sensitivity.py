"""The cost-sensitivity column: slippage is linear in the modeled fraction, so every
reading carries net restated at a doubled fraction (net - recorded slippage), with a
coverage count so pre-instrumentation rows read as unknown, never as zero-slippage."""

import sqlite3

import pytest

from cherrypick.orchestrator import calibrate, report

pytestmark = pytest.mark.unit


def _meic_db(path, rows, with_slippage=True):
    conn = sqlite3.connect(path)
    slip_col = ", slippage_dollars REAL DEFAULT 0" if with_slippage else ""
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        f"pnl REAL, fees REAL, exit_time TEXT{slip_col})"
    )
    cols = "symbol, risk_profile, pnl, fees, exit_time" + (", slippage_dollars" if with_slippage else "")
    ph = ", ".join("?" * (6 if with_slippage else 5))
    conn.executemany(f"INSERT INTO ic_trades ({cols}) VALUES ({ph})", rows)
    conn.commit()
    conn.close()


def _cfg(tmp_path):
    (tmp_path / "meic").mkdir(exist_ok=True)
    return {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {"paper_db": "p.db", "trade_schema": "meic_ic"},
                "calibration": {"ladder": ["conservative"]},
            }
        }
    }


def test_report_carries_stressed_net_and_coverage(tmp_path):
    cfg = _cfg(tmp_path)
    _meic_db(
        tmp_path / "meic" / "p.db",
        [
            # net +15 with $6 slippage: survives 2x. net +4 with $5: flips negative at 2x.
            ("SPX", "conservative", 20.0, 5.0, "2026-07-21T15:45", 6.0),
            ("SPX", "conservative", 9.0, 5.0, "2026-07-22T15:45", 5.0),
        ],
    )
    meic = report.run(cfg)["modules"]["meic"]
    assert meic["net_pnl"] == 19.0
    assert meic["slippage"] == 11.0
    assert meic["net_pnl_2x_slippage"] == 8.0
    assert meic["slippage_coverage"] == 2


def test_report_degrades_on_pre_instrumentation_db(tmp_path):
    """A DB without the column must not fail the reader — and must not claim the stress
    was survived: slippage totals 0 with coverage 0, which consumers read as unknown."""
    cfg = _cfg(tmp_path)
    _meic_db(
        tmp_path / "meic" / "p.db",
        [
            ("SPX", "conservative", 20.0, 5.0, "2026-07-21T15:45"),
        ],
        with_slippage=False,
    )
    meic = report.run(cfg)["modules"]["meic"]
    assert meic["net_pnl"] == 15.0
    assert meic["slippage"] == 0.0
    assert meic["slippage_coverage"] == 0


def test_calibrate_reading_carries_stressed_net(tmp_path):
    cfg = _cfg(tmp_path)
    _meic_db(
        tmp_path / "meic" / "p.db",
        [
            ("SPX", "conservative", 20.0, 5.0, "2026-07-21T15:45", 6.0),
            ("SPX", "conservative", 9.0, 5.0, "2026-07-22T15:45", 5.0),
        ],
    )
    reading = calibrate.run(cfg)["modules"]["meic"]["profiles"]["conservative"]["reading"]
    assert reading["net_pnl"] == 19.0
    assert reading["net_pnl_2x_slippage"] == 8.0
    assert reading["slippage_coverage"] == 2
