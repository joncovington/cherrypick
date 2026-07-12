"""Tests for the profiles calibration + promotion advisor surface (orchestrator.calibrate).

Unit lane: builds temp paper DBs with multi-session closed trades, asserts per-profile readings
(sample/win_rate/days) and the advisory promotion recommendation (graduate/hold, deliberate-only,
top-of-ladder, off-ladder), and that a missing DB is reported rather than fatal.
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


_LADDER = ["conservative", "moderate", "aggressive", "very-aggressive"]


def _winning_rows(profile, n, base_day=1):
    # n net-positive closed trades (pnl 20, fees 5 -> net +15), each on a distinct session day.
    return [(profile, 20.0, 5.0, f"2026-06-{base_day + i:02d}T15:45") for i in range(n)]


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


def test_eligible_profile_recommends_graduation(tmp_path):
    # conservative: 20 winning trades across 20 distinct days -> meets sample(20)/win(1.0)/days(20).
    _rows_to_db(tmp_path, _winning_rows("conservative", 20))
    cfg = _cfg(tmp_path, {"ladder": _LADDER, "deliberate_only": ["very-aggressive"]})
    out = calibrate.run(cfg)
    rec = out["modules"]["meic"]["profiles"]["conservative"]["recommendation"]
    assert rec["eligible"] is True
    assert rec["recommendation"] == "graduate:moderate"


def test_insufficient_sample_holds(tmp_path):
    # Only 5 trades -> sample + days below threshold -> hold.
    _rows_to_db(tmp_path, _winning_rows("conservative", 5))
    cfg = _cfg(tmp_path, {"ladder": _LADDER})
    rec = calibrate.run(cfg)["modules"]["meic"]["profiles"]["conservative"]["recommendation"]
    assert rec["eligible"] is False
    assert rec["recommendation"] == "hold"
    assert rec["checks"]["sample"]["pass"] is False


def test_deliberate_only_next_rung_holds(tmp_path):
    # aggressive fully meets thresholds, but its next rung (very-aggressive) is deliberate-only -> hold.
    _rows_to_db(tmp_path, _winning_rows("aggressive", 20))
    cfg = _cfg(tmp_path, {"ladder": _LADDER, "deliberate_only": ["very-aggressive"]})
    rec = calibrate.run(cfg)["modules"]["meic"]["profiles"]["aggressive"]["recommendation"]
    assert rec["eligible"] is False
    assert rec["recommendation"] == "hold"
    assert "deliberate" in rec["reason"]


def test_top_of_ladder_holds(tmp_path):
    _rows_to_db(tmp_path, _winning_rows("very-aggressive", 20))
    cfg = _cfg(tmp_path, {"ladder": _LADDER, "deliberate_only": ["very-aggressive"]})
    rec = calibrate.run(cfg)["modules"]["meic"]["profiles"]["very-aggressive"]["recommendation"]
    assert rec["next"] is None
    assert rec["recommendation"] == "hold"


def test_off_ladder_profile_has_no_recommendation(tmp_path):
    _rows_to_db(tmp_path, _winning_rows("experimental", 20))
    cfg = _cfg(tmp_path, {"ladder": _LADDER})
    prof = calibrate.run(cfg)["modules"]["meic"]["profiles"]["experimental"]
    assert prof["recommendation"] is None
    assert prof["reading"]["sample"] == 20


def test_no_ladder_gives_readings_only(tmp_path):
    _rows_to_db(tmp_path, _winning_rows("conservative", 3))
    cfg = _cfg(tmp_path, meic_cal=None)  # no calibration block
    m = calibrate.run(cfg)["modules"]["meic"]
    assert m["ok"] is True and m["ladder"] == []
    assert m["profiles"]["conservative"]["recommendation"] is None


def test_missing_db_reported_not_fatal(tmp_path):
    cfg = _cfg(tmp_path, {"ladder": _LADDER})  # DB never created
    out = calibrate.run(cfg)
    assert out["ok"] is True
    assert out["modules"]["meic"]["ok"] is False
    assert "not found" in out["modules"]["meic"]["reason"]
