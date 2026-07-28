"""The data-epoch marker: a correctness fix that restates what paper history means
declares an epoch date, report says how much history predates it, and calibrate
refuses to let pre-epoch sessions support a promotion reading."""

import sqlite3

import pytest

from cherrypick.orchestrator import calibrate, report
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

_LADDER = ["conservative", "moderate", "aggressive"]


def _meic_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "pnl REAL, fees REAL, exit_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _cfg(tmp_path, epoch=None):
    (tmp_path / "meic").mkdir(exist_ok=True)
    cfg = {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {"paper_db": "p.db", "trade_schema": "meic_ic"},
                "calibration": {"ladder": _LADDER},
            }
        }
    }
    if epoch:
        cfg["data_epoch"] = epoch
    return cfg


def _rows(tmp_path, pre=20, post=3):
    rows = [("SPX", "conservative", 20.0, 5.0, f"2026-06-{i + 1:02d}T15:45") for i in range(pre)]
    rows += [("SPX", "conservative", 20.0, 5.0, f"2026-07-{i + 1:02d}T15:45") for i in range(post)]
    _meic_db(tmp_path / "meic" / "p.db", rows)


def test_config_accessor_requires_a_date():
    assert cfgmod.data_epoch({}) is None
    assert cfgmod.data_epoch({"data_epoch": {"note": "no date"}}) is None
    e = cfgmod.data_epoch({"data_epoch": {"date": "2026-07-01", "note": "phase-0"}})
    assert e == {"date": "2026-07-01", "note": "phase-0"}


def test_calibrate_excludes_pre_epoch_sessions_from_promotion(tmp_path):
    """20 winning pre-epoch sessions would graduate the rung; with the epoch declared,
    only the 3 post-epoch sessions count and the recommendation holds."""
    cfg = _cfg(tmp_path, epoch={"date": "2026-07-01", "note": "phase-0 restatement"})
    _rows(tmp_path)
    out = calibrate.run(cfg)
    prof = out["modules"]["meic"]["profiles"]["conservative"]
    assert out["data_epoch"]["date"] == "2026-07-01"
    assert prof["reading"]["sample"] == 3
    assert prof["reading"]["days"] == 3
    assert prof["recommendation"]["eligible"] is False


def test_calibrate_without_epoch_keeps_full_history(tmp_path):
    cfg = _cfg(tmp_path)
    _rows(tmp_path)
    out = calibrate.run(cfg)
    assert out["data_epoch"] is None
    assert out["modules"]["meic"]["profiles"]["conservative"]["reading"]["sample"] == 23


def test_report_counts_pre_epoch_trades_without_rewriting_totals(tmp_path):
    """The report stays descriptive: totals cover everything, but the module block says
    how many rows predate the epoch (what a promotion reading excludes)."""
    cfg = _cfg(tmp_path, epoch={"date": "2026-07-01"})
    _rows(tmp_path)
    out = report.run(cfg)
    meic = out["modules"]["meic"]
    assert out["data_epoch"]["date"] == "2026-07-01"
    assert meic["trades"] == 23          # history is never rewritten
    assert meic["pre_epoch_trades"] == 20


def test_report_without_epoch_has_no_marker(tmp_path):
    cfg = _cfg(tmp_path)
    _rows(tmp_path)
    out = report.run(cfg)
    assert out["data_epoch"] is None
    assert "pre_epoch_trades" not in out["modules"]["meic"]
