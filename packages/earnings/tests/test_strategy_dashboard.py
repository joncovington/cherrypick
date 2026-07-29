"""The static earnings dashboard after the matplotlib retirement: built from inline
cherrypick.core.viz cards plus plain-HTML surfaces, still one self-contained offline file."""

import sqlite3
from datetime import datetime, timedelta

import pytest

import strategy_dashboard as sd
import strategy_metrics as sm

DAY1 = datetime(2026, 7, 20, 9, 50).timestamp()
DAY2 = datetime(2026, 7, 21, 9, 50).timestamp()


@pytest.fixture(autouse=True)
def _restore_db_path():
    orig = sm.DB_PATH
    yield
    sm.DB_PATH = orig


@pytest.fixture
def trades_db(tmp_path, monkeypatch):
    db = tmp_path / "paper.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (order_id TEXT, profile TEXT, strategy TEXT, symbol TEXT, "
        "quantity INTEGER, entry_credit REAL, capital_at_risk REAL, entry_cost REAL, "
        "exit_cost REAL, pnl REAL, opened_at REAL, closed_at REAL, expiration TEXT, "
        "entry_context TEXT, entry_iv REAL, exit_iv REAL)"
    )
    conn.execute("CREATE TABLE scan_log (profile TEXT, reason TEXT)")
    conn.execute(
        "INSERT INTO trades (profile, strategy, symbol, pnl, entry_cost, exit_cost, opened_at, closed_at) "
        "VALUES ('strat_test:iron_fly', 'iron_fly', 'AAPL', 120.0, 10.0, 0.0, ?, ?)",
        (DAY1 - 60000, DAY1),
    )
    conn.execute(
        "INSERT INTO trades (profile, strategy, symbol, quantity, entry_credit, capital_at_risk, "
        "entry_cost, opened_at, expiration) "
        "VALUES ('strat_test:iron_fly', 'iron_fly', 'NVDA', 1, 2.5, 400.0, 5.0, ?, '2026-07-24')",
        (DAY2,),
    )
    conn.execute(
        "INSERT INTO scan_log (profile, reason) VALUES ('strat_test:iron_fly', 'iv_rv below min; oi too thin')"
    )
    conn.commit()
    conn.close()
    sm.DB_PATH = db
    monkeypatch.setattr(sd.scanner, "_load_config", lambda: {"available_capital_paper_mode": 10000.0})
    return db


def test_dashboard_is_viz_cards_not_pngs(trades_db):
    html = sd.build_dashboard("strat_test", None, "paper")
    assert "data:image/png" not in html
    assert "matplotlib" not in html.lower()
    # Inline viz cards: the timeframe panels and one card per strategy, payloads baked in.
    assert 'data-cp-section="tf-cumulative"' in html
    assert 'data-cp-section="strategy-iron_fly"' in html
    assert 'class="cpdata"' in html
    # The shared client renderer and its CSS variables ship with the page (offline, no CDN).
    assert "renderTimeseries" in html
    assert "--accent" in html
    assert "PAPER — Simulated" in html
    # The non-viz surfaces still carry their data.
    assert "iv_rv below min" in html and "oi too thin" in html  # rejection bars
    assert "NVDA" in html  # open positions table


def test_dashboard_live_mode_badge(trades_db):
    html = sd.build_dashboard("strat_test", None, "live")
    assert "LIVE — Real Money" in html


def test_portfolio_ts_payload_windows_by_close_date():
    now = datetime.now().timestamp()
    old = now - timedelta(days=40).total_seconds()
    trades = [
        {"pnl": 100.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": old},
        {"pnl": 50.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": now - 3600},
    ]
    full = sd._portfolio_ts_payload(trades, None)
    assert full["timeseries"]["series"][0]["values"][-1] == 150.0
    windowed = sd._portfolio_ts_payload(trades, 7)
    # Only the recent close is in the window, so the cumulative restarts from it.
    assert windowed["timeseries"]["series"][0]["values"] == [50.0]
    assert sd._portfolio_ts_payload([], 7) == {"ok": True, "subtitle": "no closed trades in this window"}


def test_strategy_ts_payload_includes_drawdown():
    trades = [
        {"pnl": 100.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": DAY1},
        {"pnl": -30.0, "entry_cost": 0.0, "exit_cost": 0.0, "closed_at": DAY2},
    ]
    payload = sd._strategy_ts_payload(trades)
    series = {s["name"]: s["values"] for s in payload["timeseries"]["series"]}
    assert series["equity"] == [100.0, 70.0]
    assert series["drawdown"] == [0.0, -30.0]


def test_weekly_pnl_html_buckets_by_iso_week():
    trades = [
        {
            "pnl": 100.0,
            "entry_cost": 0.0,
            "exit_cost": 0.0,
            "closed_at": datetime(2026, 7, 20, 10, 0).timestamp(),
        },  # ISO week 30
        {
            "pnl": -40.0,
            "entry_cost": 0.0,
            "exit_cost": 0.0,
            "closed_at": datetime(2026, 7, 27, 10, 0).timestamp(),
        },  # ISO week 31
    ]
    html = sd._weekly_pnl_html(trades)
    assert "2026-W30" in html and "2026-W31" in html
    assert "$100.00" in html and "-$40.00" in html
    assert sd._weekly_pnl_html([]).count("no closed trades") == 1


def test_regime_table_shades_by_count():
    html = sd._regime_table_html({"iron_fly": {"high (>=1.00) / tight (<0.10)": 3}})
    assert "iron_fly" in html
    assert ">3</td>" in html
    assert "rgba(88,166,255" in html
    assert "no regime data yet" in sd._regime_table_html({})


def test_rejection_bars_ordered_by_count():
    html = sd._rejection_bars_html({"rare": 1, "common": 9})
    assert html.index("common") < html.index("rare")
    assert "no rejections logged" in sd._rejection_bars_html({})
