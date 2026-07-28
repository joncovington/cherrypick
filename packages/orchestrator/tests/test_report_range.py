"""report.run's session_range: bounded readings plus the daily net-P&L series (the feed
for a suite equity curve), with the bounds pushed into reader SQL where exact."""

import sqlite3

import pytest

from cherrypick.orchestrator import report

pytestmark = pytest.mark.unit


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


def _cfg(tmp_path):
    (tmp_path / "meic").mkdir(exist_ok=True)
    return {"modules": {"meic": {
        "enabled": True, "path": str(tmp_path / "meic"),
        "paper": {"paper_db": "p.db", "trade_schema": "meic_ic"},
    }}}


def _seed(tmp_path):
    _meic_db(tmp_path / "meic" / "p.db", [
        ("SPX", "conservative", 20.0, 5.0, "2026-07-20T15:45"),
        ("SPX", "conservative", -8.0, 2.0, "2026-07-21T15:45"),
        ("SPX", "conservative", 12.0, 2.0, "2026-07-21T16:01"),
        ("SPX", "conservative", 30.0, 5.0, "2026-07-24T15:45"),
    ])


def test_session_range_bounds_the_totals_and_emits_the_daily_series(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    out = report.run(cfg, session_range=("2026-07-21", "2026-07-24"))
    # 07-20 excluded: 3 trades, nets -10, +10, +25.
    assert out["suite"]["trades"] == 3
    assert out["suite"]["net_pnl"] == pytest.approx(25.0)
    assert out["session_range"] == ["2026-07-21", "2026-07-24"]
    daily = out["daily"]
    assert [d["session"] for d in daily] == ["2026-07-21", "2026-07-24"]
    assert daily[0]["net_pnl"] == pytest.approx(0.0)   # -10 + 10
    assert daily[0]["trades"] == 2
    assert daily[0]["by_module"] == {"meic": 0.0}
    assert daily[1]["net_pnl"] == pytest.approx(25.0)


def test_open_ended_range_and_cumulative_agree(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    unbounded = report.run(cfg, session_range=(None, None))
    cumulative = report.run(cfg)
    assert unbounded["suite"]["net_pnl"] == cumulative["suite"]["net_pnl"]
    assert "daily" not in cumulative  # the series only exists when a range was asked for
    assert len(unbounded["daily"]) == 3


def test_single_session_still_works_and_range_is_exclusive_with_it(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    day = report.run(cfg, session="2026-07-21")
    assert day["suite"]["trades"] == 2
    with pytest.raises(ValueError):
        report.run(cfg, session="2026-07-21", session_range=("2026-07-20", "2026-07-21"))
