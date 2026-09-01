"""Tests for the profiles calibration + champion/challenger advisor surface (orchestrator.calibrate).

Unit lane: builds temp paper DBs with multi-session closed trades, asserts per-profile readings
(sample/win_rate/days) and the advisory champion/challenger verdict (champion change/retain,
deliberate-only, readings-only mode), and that a missing DB is reported rather than fatal.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import calibrate

pytestmark = pytest.mark.unit


def _meic_db(path, rows):
    """rows: (symbol, risk_profile, pnl, fees, exit_time)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "pnl REAL, fees REAL, exit_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time) VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


def _cfg(tmp_path, meic_cal=None, meic_dir="meic"):
    (tmp_path / meic_dir).mkdir(exist_ok=True)
    meic = {
        "enabled": True,
        "path": str(tmp_path / meic_dir),
        "paper": {"paper_db": "p.db", "trade_schema": "meic_ic"},
    }
    if meic_cal is not None:
        meic["calibration"] = meic_cal
    return {"modules": {"meic": meic}}


_CHAMPION = "conservative"


def _winning_rows(profile, n, base_day=1, pnl=20.0, fees=5.0):
    # n net-positive closed trades (net = pnl - fees), each on a distinct session day.
    return [(profile, pnl, fees, f"2026-06-{base_day + i:02d}T15:45") for i in range(n)]


def _rows_to_db(tmp_path, triples, meic_dir="meic"):
    (tmp_path / meic_dir).mkdir(exist_ok=True)
    _meic_db(tmp_path / meic_dir / "p.db", [("SPX", p, pnl, fees, t) for (p, pnl, fees, t) in triples])


def test_reading_counts_sample_winrate_and_days():
    recs = [
        {"profile": "c", "net_pnl": 10.0, "session": "2026-06-01"},
        {"profile": "c", "net_pnl": -5.0, "session": "2026-06-01"},  # same day
        {"profile": "c", "net_pnl": 8.0, "session": "2026-06-02"},
    ]
    r = calibrate._reading(recs)
    assert r["sample"] == 3
    assert r["win_rate"] == round(2 / 3, 4)
    assert r["days"] == 2  # two distinct sessions
    assert r["net_pnl"] == 13.0


def test_every_tag_gets_a_reading_and_a_qualification(tmp_path):
    # Readings and qualification checks, never a recommendation — the champion/challenger
    # comparison was retired 2026-08-20 and judging arms belongs to packages/advisor.
    _rows_to_db(tmp_path, _winning_rows("experimental", 20))
    cfg = _cfg(tmp_path, {"rule": {"min_days": 14}})
    out = calibrate.run(cfg)["modules"]["meic"]
    assert "champion" not in out
    assert "recommendation" not in out
    prof = out["profiles"]["experimental"]
    assert prof["role"] is None
    assert prof["reading"]["sample"] == 20
    assert prof["qualified"] is True
    assert "recommendation" not in prof
    assert "beats_champion" not in prof


def test_a_module_with_no_calibration_block_still_reads(tmp_path):
    _rows_to_db(tmp_path, _winning_rows("conservative", 3))
    cfg = _cfg(tmp_path, meic_cal=None)  # no calibration block at all
    m = calibrate.run(cfg)["modules"]["meic"]
    assert m["ok"] is True
    assert "champion" not in m and "recommendation" not in m
    assert m["profiles"]["conservative"]["role"] is None


def test_missing_db_reported_not_fatal(tmp_path):
    cfg = _cfg(tmp_path, {"champion": _CHAMPION})  # DB never created
    out = calibrate.run(cfg)
    assert out["ok"] is True
    assert out["modules"]["meic"]["ok"] is False
    assert "not found" in out["modules"]["meic"]["reason"]
