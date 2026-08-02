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


def test_qualified_challenger_recommends_champion_change(tmp_path):
    # conservative (champion): thin net_pnl per trade. moderate (challenger): qualifies AND has
    # far higher net_pnl -> beats the champion -> recommend the champion change.
    rows = _winning_rows(_CHAMPION, 20, pnl=1.0, fees=0.0) + _winning_rows("moderate", 20, pnl=20.0, fees=5.0)
    _rows_to_db(tmp_path, rows)
    cfg = _cfg(tmp_path, {"champion": _CHAMPION, "deliberate_only": ["very-aggressive"]})
    out = calibrate.run(cfg)
    mod = out["modules"]["meic"]
    assert mod["champion"] == _CHAMPION
    assert mod["profiles"][_CHAMPION]["role"] == "champion"
    assert mod["profiles"]["moderate"]["role"] == "challenger"
    assert mod["profiles"]["moderate"]["beats_champion"] is True
    assert mod["recommendation"]["eligible"] is True
    assert mod["recommendation"]["recommendation"] == "champion:moderate"


def test_unqualified_challenger_retains_champion(tmp_path):
    # champion has a real reading; the challenger has only 5 trades -> below sample/days -> never
    # counted as beating the champion regardless of its raw net_pnl.
    rows = _winning_rows(_CHAMPION, 20) + _winning_rows("moderate", 5, pnl=1000.0, fees=0.0)
    _rows_to_db(tmp_path, rows)
    cfg = _cfg(tmp_path, {"champion": _CHAMPION})
    out = calibrate.run(cfg)["modules"]["meic"]
    assert out["profiles"]["moderate"]["qualified"] is False
    assert out["profiles"]["moderate"]["beats_champion"] is False
    assert out["recommendation"]["eligible"] is False
    assert out["recommendation"]["recommendation"] == f"retain:{_CHAMPION}"


def test_deliberate_only_challenger_never_recommended(tmp_path):
    # very-aggressive fully qualifies and has the best net_pnl of any challenger, but it's
    # deliberate-only -> never auto-recommended even though it would otherwise win outright.
    rows = _winning_rows(_CHAMPION, 20, pnl=5.0, fees=0.0) + _winning_rows(
        "very-aggressive", 20, pnl=9000.0, fees=0.0
    )
    _rows_to_db(tmp_path, rows)
    cfg = _cfg(tmp_path, {"champion": _CHAMPION, "deliberate_only": ["very-aggressive"]})
    out = calibrate.run(cfg)["modules"]["meic"]
    assert out["profiles"]["very-aggressive"]["qualified"] is True
    assert out["profiles"]["very-aggressive"]["beats_champion"] is True
    assert out["profiles"]["very-aggressive"]["deliberate_only"] is True
    assert out["recommendation"]["eligible"] is False
    assert out["recommendation"]["recommendation"] == f"retain:{_CHAMPION}"


def test_readings_only_mode_has_no_recommendation(tmp_path):
    # No "champion" key at all -> qualify_readings, not recommend_champion: every tag gets a
    # reading and a qualification, never a recommendation/eligible/role anywhere in the shape.
    # This is the calibrate-level regression test for the original bug (flies' arms used to be
    # forced through a ladder-graduation comparison that meant nothing for parallel experiments).
    _rows_to_db(tmp_path, _winning_rows("experimental", 20))
    cfg = _cfg(tmp_path, {"rule": {"min_days": 14}})  # calibration present, but no champion
    out = calibrate.run(cfg)["modules"]["meic"]
    assert out["champion"] is None
    assert out["recommendation"] is None
    prof = out["profiles"]["experimental"]
    assert prof["role"] is None
    assert prof["reading"]["sample"] == 20
    assert prof["qualified"] is True
    assert "recommendation" not in prof
    assert "beats_champion" not in prof


def test_missing_calibration_block_defaults_to_readings_only(tmp_path):
    _rows_to_db(tmp_path, _winning_rows("conservative", 3))
    cfg = _cfg(tmp_path, meic_cal=None)  # no calibration block at all
    m = calibrate.run(cfg)["modules"]["meic"]
    assert m["ok"] is True and m["champion"] is None
    assert m["recommendation"] is None
    assert m["profiles"]["conservative"]["role"] is None


def test_multiple_qualified_challengers_best_one_wins_at_calibrate_level(tmp_path):
    rows = (
        _winning_rows(_CHAMPION, 20, pnl=1.0, fees=0.0)
        + _winning_rows("moderate", 20, pnl=10.0, fees=0.0)
        + _winning_rows("aggressive", 20, pnl=20.0, fees=0.0)  # best of the two challengers
    )
    _rows_to_db(tmp_path, rows)
    cfg = _cfg(tmp_path, {"champion": _CHAMPION})
    out = calibrate.run(cfg)["modules"]["meic"]
    assert out["recommendation"]["recommendation"] == "champion:aggressive"
    assert out["profiles"]["moderate"]["beats_champion"] is True  # also beats, just not the best


def test_missing_db_reported_not_fatal(tmp_path):
    cfg = _cfg(tmp_path, {"champion": _CHAMPION})  # DB never created
    out = calibrate.run(cfg)
    assert out["ok"] is True
    assert out["modules"]["meic"]["ok"] is False
    assert "not found" in out["modules"]["meic"]["reason"]
