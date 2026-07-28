"""The compact suite-dashboard card (strategy_section.build_section): a cherrypick.core.viz
payload over the same strategy_metrics reads as the full dashboard, so the two can't disagree."""

import sqlite3
from datetime import datetime

import pytest

import strategy_metrics as sm
import strategy_section as ss

DAY1 = datetime(2026, 7, 20, 9, 50).timestamp()
DAY2 = datetime(2026, 7, 21, 9, 50).timestamp()


@pytest.fixture(autouse=True)
def _restore_db_path():
    """build_section repoints the module-global sm.DB_PATH (as the CLIs do) — put it back so
    test order can never leak one test's DB into another's reads."""
    orig = sm.DB_PATH
    yield
    sm.DB_PATH = orig


def _make_db(path, closed=(), open_rows=()):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (order_id TEXT, profile TEXT, strategy TEXT, symbol TEXT, "
        "quantity INTEGER, entry_credit REAL, capital_at_risk REAL, entry_cost REAL, "
        "exit_cost REAL, pnl REAL, opened_at REAL, closed_at REAL, expiration TEXT, "
        "entry_context TEXT, entry_iv REAL, exit_iv REAL)"
    )
    conn.execute("CREATE TABLE scan_log (profile TEXT, reason TEXT)")
    for row in closed:
        conn.execute(
            "INSERT INTO trades (profile, strategy, symbol, pnl, entry_cost, exit_cost, "
            "opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    for row in open_rows:
        conn.execute(
            "INSERT INTO trades (profile, strategy, symbol, quantity, entry_credit, "
            "capital_at_risk, entry_cost, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            row,
        )
    conn.commit()
    conn.close()
    return path


def test_missing_db_is_a_plain_error_not_a_stray_file(tmp_path):
    missing = tmp_path / "nope.db"
    payload = ss.build_section(db_override=str(missing))
    assert payload["ok"] is False
    assert "no paper trades DB yet" in payload["error"]
    assert not missing.exists()  # sqlite must not have created an empty file as a side effect


def test_unknown_mode_is_an_error_payload():
    payload = ss.build_section(mode="bogus")
    assert payload["ok"] is False
    assert "bogus" in payload["error"]


def test_empty_book_is_ok_not_error(tmp_path):
    db = _make_db(tmp_path / "paper.db")
    payload = ss.build_section(db_override=str(db))
    assert payload["ok"] is True
    assert "no trades yet" in payload["title"]
    assert payload["subtitle"].startswith("paper")


def test_payload_metrics_and_timeseries(tmp_path):
    db = _make_db(
        tmp_path / "paper.db",
        closed=[
            # profile, strategy, symbol, pnl, entry_cost, exit_cost, opened_at, closed_at
            ("strat_test:iron_fly", "iron_fly", "AAPL", 120.0, 10.0, 0.0, DAY1 - 60000, DAY1),
            ("strat_test:iron_condor", "iron_condor", "MSFT", -40.0, 10.0, 0.0, DAY2 - 60000, DAY2),
            ("default", "iron_fly", "TSLA", 999.0, 0.0, 0.0, DAY1 - 60000, DAY1),  # other book: excluded
        ],
        open_rows=[("strat_test:iron_fly", "iron_fly", "NVDA", 1, 2.5, 400.0, 5.0, DAY2, )],
    )
    payload = ss.build_section(db_override=str(db))
    assert payload["ok"] is True
    assert payload["subtitle"] == "paper · profile strat_test"

    by_label = {m["label"]: m for m in payload["metrics"]}
    # net = (120-10) + (-40-10) = 60; the 'default'-book trade must not leak in
    assert by_label["Net P&L"]["value"] == "$60.00"
    assert by_label["Net P&L"]["tone"] == "pos"
    assert by_label["Expectancy / trade"]["value"] == "$30.00"
    assert by_label["Closed trades"]["value"] == "2"
    assert by_label["Open overnight"]["value"] == "1"
    assert by_label["Sample"]["value"] == "2/100"

    ts = payload["timeseries"]
    assert ts["labels"] == ["2026-07-20", "2026-07-21"]
    assert ts["series"][0]["values"] == [110.0, 60.0]


def test_daily_equity_series_collapses_same_day_and_orders():
    trades = [
        {"pnl": 50.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": DAY2},
        {"pnl": 100.0, "entry_cost": 10.0, "exit_cost": 0.0, "closed_at": DAY1},
        {"pnl": -20.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": DAY1 + 300},
    ]
    labels, values = sm.daily_equity_series(trades)
    assert labels == ["2026-07-20", "2026-07-21"]
    # day1 ends at 90 - 20 = 70; day2 adds 50 -> 120
    assert values == [70.0, 120.0]
