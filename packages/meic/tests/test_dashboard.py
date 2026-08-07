"""
Tests for dashboard.py data layer.

No HTTP server, no browser, no MCP required — all tests operate on an
in-memory or temp SQLite database and call dashboard functions directly.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import dashboard

# ── Fixtures ──────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS ic_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL, entry_time TEXT, expiration TEXT,
    symbol TEXT NOT NULL, put_strike REAL, call_strike REAL, wing_width REAL,
    put_symbol TEXT, call_symbol TEXT, long_put_symbol TEXT, long_call_symbol TEXT,
    put_credit REAL, call_credit REAL, net_credit REAL, quantity INTEGER DEFAULT 1,
    put_delta_at_entry REAL, call_delta_at_entry REAL,
    long_put_delta_at_entry REAL, long_call_delta_at_entry REAL,
    underlying_price_entry REAL, iv_rank_at_entry REAL, iv_pct_at_entry REAL,
    session_quality TEXT, iv_skew_signal TEXT,
    price_action_signal TEXT, ai_entry_reasoning TEXT,
    ic_order_id TEXT UNIQUE NOT NULL,
    put_spread_entry_order_id TEXT, call_spread_entry_order_id TEXT,
    put_stop_order_id TEXT, call_stop_order_id TEXT,
    stop_trigger_original REAL, stop_limit_original REAL,
    stop_trigger_current REAL, stop_limit_current REAL,
    stop_adjustment_count INTEGER DEFAULT 0,
    stop_adjustment_history TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    exit_time TEXT, exit_price REAL, exit_reason TEXT, exit_analysis TEXT,
    put_stop_cost REAL, call_stop_cost REAL,
    pnl REAL, fees REAL, fill_confirmed_at TEXT,
    risk_profile TEXT, execution_mode TEXT, iv_rank_source TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ic_spread_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ic_order_id TEXT NOT NULL REFERENCES ic_trades(ic_order_id),
    side TEXT NOT NULL CHECK (side IN ('put', 'call')),
    status TEXT NOT NULL DEFAULT 'open',
    exit_time TEXT, exit_reason TEXT, exit_price REAL, pnl REAL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(ic_order_id, side)
);
CREATE TABLE IF NOT EXISTS daily_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date TEXT UNIQUE NOT NULL, symbol TEXT,
    total_entries INTEGER DEFAULT 0, entries_filled INTEGER DEFAULT 0,
    entries_stopped INTEGER DEFAULT 0, entries_expired INTEGER DEFAULT 0,
    entries_cancelled INTEGER DEFAULT 0,
    gross_credit REAL DEFAULT 0, gross_pnl REAL DEFAULT 0,
    fees REAL DEFAULT 0, net_pnl REAL DEFAULT 0,
    closing_nlv REAL, win_count INTEGER DEFAULT 0,
    win_rate_pct REAL, avg_iv_rank REAL,
    sessions_entered TEXT DEFAULT '[]', ai_day_summary TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loop_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_time TEXT NOT NULL, loop_date TEXT NOT NULL,
    action TEXT, reasoning TEXT,
    open_trades_n INTEGER DEFAULT 0, today_count INTEGER DEFAULT 0,
    today_pnl REAL DEFAULT 0, iv_rank REAL, underlying_price REAL,
    session_quality TEXT, mcp_errors TEXT DEFAULT '[]',
    duration_ms INTEGER, created_at TEXT NOT NULL
);
"""

_NOW = "2026-06-20 10:30:00"
_TODAY = "2026-06-20"
_YESTERDAY = "2026-06-19"


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for stmt in DDL.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.commit()
    return conn


def _insert_trade(conn, **kwargs):
    defaults = dict(
        trade_date=_TODAY,
        entry_time=_NOW,
        symbol="SPX",
        put_strike=5400,
        call_strike=5600,
        wing_width=5,
        put_credit=0.55,
        call_credit=0.65,
        net_credit=1.20,
        quantity=1,
        iv_rank_at_entry=0.40,
        session_quality="prime",
        ic_order_id="IC-001",
        status="expired",
        pnl=1.20,
        fees=0.10,
        exit_time="2026-06-20 16:00:00",
        exit_reason="expired_eod",
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(f"INSERT INTO ic_trades ({cols}) VALUES ({placeholders})", list(defaults.values()))
    conn.commit()


def _insert_leg(conn, **kwargs):
    defaults = dict(
        ic_order_id="IC-001",
        side="put",
        status="open",
        exit_time=None,
        exit_reason=None,
        exit_price=None,
        pnl=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(f"INSERT INTO ic_spread_legs ({cols}) VALUES ({placeholders})", list(defaults.values()))
    conn.commit()


def _insert_summary(conn, **kwargs):
    defaults = dict(
        summary_date=_YESTERDAY,
        symbol="SPX",
        entries_filled=3,
        win_count=2,
        net_pnl=2.50,
        closing_nlv=100500.0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(f"INSERT INTO daily_summary ({cols}) VALUES ({placeholders})", list(defaults.values()))
    conn.commit()


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    """Temp DB with schema; monkeypatches dashboard._DB_PATH and the date helpers
    so stats windows are anchored to _TODAY regardless of the real wall-clock date
    the tests happen to run on."""
    path = str(tmp_path / "meic_trades.db")
    conn = _make_db(path)
    conn.close()
    monkeypatch.setattr(dashboard, "_DB_PATH", path)
    monkeypatch.setattr(dashboard, "_today", lambda: _TODAY)
    monkeypatch.setattr(dashboard, "_week_start", lambda: "2026-06-15")
    monkeypatch.setattr(dashboard, "_month_start", lambda: "2026-06-01")
    monkeypatch.setattr(dashboard, "_year_start", lambda: "2026-01-01")
    return path


# ── _wl_ratio ─────────────────────────────────────────────────────────────────


def test_wl_ratio_normal():
    assert dashboard._wl_ratio(3, 1) == 75.0


def test_wl_ratio_all_wins():
    assert dashboard._wl_ratio(5, 0) == 100.0


def test_wl_ratio_all_losses():
    assert dashboard._wl_ratio(0, 4) == 0.0


def test_wl_ratio_no_trades():
    assert dashboard._wl_ratio(0, 0) is None


def test_wl_ratio_none_inputs():
    assert dashboard._wl_ratio(None, None) is None


# ── _spread_statuses ──────────────────────────────────────────────────────────


def _trade(**kwargs):
    base = {"status": "open", "exit_time": None, "exit_analysis": None}
    base.update(kwargs)
    return base


def test_status_open():
    put_s, call_s = dashboard._spread_statuses(_trade(status="open"))
    assert put_s["type"] == "monitoring"
    assert call_s["type"] == "monitoring"


def test_status_expired():
    put_s, call_s = dashboard._spread_statuses(_trade(status="expired"))
    assert put_s["type"] == "expired"
    assert call_s["type"] == "expired"


def test_status_pending():
    put_s, call_s = dashboard._spread_statuses(_trade(status="pending"))
    assert put_s["type"] == "pending"
    assert call_s["type"] == "pending"


def test_status_partial_entry():
    put_s, call_s = dashboard._spread_statuses(_trade(status="partial_entry"))
    assert put_s["type"] == "pending"
    assert call_s["type"] == "pending"


def test_status_cancelled():
    put_s, call_s = dashboard._spread_statuses(_trade(status="cancelled"))
    assert put_s["type"] == "cancelled"
    assert call_s["type"] == "cancelled"


def test_status_force_closed():
    put_s, call_s = dashboard._spread_statuses(_trade(status="force_closed"))
    assert put_s["type"] == "force_closed"
    assert call_s["type"] == "force_closed"


def test_status_stopped_no_exit_analysis():
    put_s, call_s = dashboard._spread_statuses(_trade(status="stopped", exit_time="2026-06-20T11:21:00"))
    assert put_s["type"] == "stopped"
    assert call_s["type"] == "stopped"


def test_status_stopped_put_side():
    put_leg = {"status": "stopped", "exit_time": "2026-06-20T11:21:00", "pnl": -0.5}
    call_leg = {"status": "open", "exit_time": None, "pnl": None}
    put_s, call_s = dashboard._spread_statuses(_trade(status="partial"), put_leg, call_leg)
    assert put_s["type"] == "stopped"
    assert call_s["type"] == "monitoring"


def test_status_stopped_call_side():
    put_leg = {"status": "open", "exit_time": None, "pnl": None}
    call_leg = {"status": "stopped", "exit_time": "2026-06-20T14:05:00", "pnl": -0.5}
    put_s, call_s = dashboard._spread_statuses(_trade(status="partial"), put_leg, call_leg)
    assert put_s["type"] == "monitoring"
    assert call_s["type"] == "stopped"


def test_status_stopped_time_in_label():
    put_leg = {"status": "stopped", "exit_time": "2026-06-20T11:21:00", "pnl": -0.5}
    call_leg = {"status": "open", "exit_time": None, "pnl": None}
    put_s, _ = dashboard._spread_statuses(_trade(status="partial"), put_leg, call_leg)
    assert "11:21" in put_s["label"]


# ── _stats_for_period ────────────────────────────────────────────────────────
# Replaces the old _today_stats/_historical_stats/_merge trio: dashboard.py now
# computes stats for any date range (today, week, ..., all_time) directly from
# ic_trades + ic_spread_legs in one function, rather than pre-aggregating into
# daily_summary and merging today's live numbers on top.


def test_stats_for_period_empty(db_path):
    conn = dashboard._connect()
    result = dashboard._stats_for_period(conn, start=_TODAY, end=_TODAY)
    conn.close()
    assert result["net_pnl"] == 0.0
    assert result["total_trades"] == 0
    assert result["wl_ratio"] is None


def test_stats_for_period_with_trades(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", pnl=1.20, status="expired")
    _insert_trade(conn, ic_order_id="IC-002", pnl=-0.80, status="stopped")
    _insert_trade(conn, ic_order_id="IC-003", pnl=None, status="cancelled")
    result = dashboard._stats_for_period(conn, start=_TODAY, end=_TODAY)
    conn.close()
    assert result["total_trades"] == 2  # cancelled excluded
    # One win definition module-wide: per TRADE, net of fees (pnl - fees > 0).
    # 1.20 - 0.10 wins; -0.80 - 0.10 loses. The per-side spread lens is a
    # per-row display in the trade log, never these headline counts.
    assert result["wins"] == 1
    assert result["losses"] == 1
    assert result["wl_ratio"] == 50.0
    assert abs(result["net_pnl"] - 0.40) < 0.01


def test_stats_for_period_excludes_pending(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", pnl=None, status="pending")
    result = dashboard._stats_for_period(conn, start=_TODAY, end=_TODAY)
    conn.close()
    assert result["total_trades"] == 0


def test_stats_for_period_excludes_out_of_range(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", trade_date=_YESTERDAY, pnl=5.00, status="expired")
    result = dashboard._stats_for_period(conn, start=_TODAY, end=_TODAY)
    conn.close()
    assert result["total_trades"] == 0


def test_stats_for_period_scores_the_trade_not_its_legs(db_path):
    """A partially-stopped IC is ONE trade with one net outcome — leg rows must not
    inflate the headline counts (that was the per-leg win-rate definition R3 retired;
    per-side outcomes remain visible per row in the trade log)."""
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", status="partial", pnl=0.30, fees=0.10)
    _insert_leg(conn, ic_order_id="IC-001", side="put", status="stopped", pnl=-0.50)
    _insert_leg(conn, ic_order_id="IC-001", side="call", status="expired", pnl=0.80)
    result = dashboard._stats_for_period(conn, start=_TODAY, end=_TODAY)
    conn.close()
    # net = 0.30 - 0.10 > 0: one winning trade, zero losses.
    assert result["wins"] == 1
    assert result["losses"] == 0


# ── _build_api_data ───────────────────────────────────────────────────────────


def test_build_api_data_no_db(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "_DB_PATH", str(tmp_path / "nonexistent.db"))
    result = dashboard._build_api_data()
    assert result["ok"] is False
    assert "error" in result


def test_build_api_data_empty_db(db_path):
    result = dashboard._build_api_data()
    assert result["ok"] is True
    assert "stats" in result
    assert "trades" in result
    assert "nlv_series" in result
    assert "analytics" in result
    assert result["trades"] == []
    assert result["nlv_series"] == []


def test_build_api_data_stats_keys(db_path):
    result = dashboard._build_api_data()
    for period in ("today", "week", "month", "year", "all_time"):
        assert period in result["stats"]
        s = result["stats"][period]
        assert "net_pnl" in s
        assert "total_trades" in s
        assert "wins" in s
        assert "losses" in s
        assert "wl_ratio" in s


def test_build_api_data_with_trade(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", pnl=1.20, status="expired")
    conn.close()
    result = dashboard._build_api_data()
    assert result["ok"] is True
    assert len(result["trades"]) == 1
    t = result["trades"][0]
    assert t["ic_order_id"] == "IC-001"
    assert "put_status" in t
    assert "call_status" in t


def test_build_api_data_trade_has_no_exit_analysis_key(db_path):
    """exit_analysis should be consumed internally, not exposed in trades list."""
    conn = dashboard._connect()
    ea = json.dumps({"stopped_spread": "put"})
    _insert_trade(
        conn,
        ic_order_id="IC-001",
        status="partial",
        exit_analysis=ea,
        exit_time="2026-06-20T11:21:00",
        pnl=None,
    )
    conn.close()
    result = dashboard._build_api_data()
    assert "exit_analysis" not in result["trades"][0]


def test_build_api_data_nlv_series(db_path):
    conn = dashboard._connect()
    _insert_summary(conn, summary_date="2026-06-18", closing_nlv=100200.0, net_pnl=1.00)
    _insert_summary(conn, summary_date="2026-06-19", closing_nlv=100350.0, net_pnl=1.50)
    conn.close()
    result = dashboard._build_api_data()
    assert len(result["nlv_series"]) == 2
    assert result["nlv_series"][0]["closing_nlv"] == 100200.0
    assert result["nlv_series"][1]["date"] == "2026-06-19"


def test_build_api_data_analytics_keys(db_path):
    result = dashboard._build_api_data()
    ana = result["analytics"]
    assert "by_session" in ana
    assert "by_exit" in ana
    assert "by_iv" in ana
    assert "fee_summary" in ana
    fs = ana["fee_summary"]
    assert "gross_credit" in fs
    assert "total_fees" in fs
    assert "net_pnl" in fs
    assert "fee_drag_pct" in fs


def test_build_api_data_fee_drag_none_when_no_trades(db_path):
    result = dashboard._build_api_data()
    assert result["analytics"]["fee_summary"]["fee_drag_pct"] is None


def test_build_api_data_today_merges_into_week(db_path):
    """Stats are computed directly from ic_trades over the period's date range, so a
    trade from earlier in the week and today's trade should both land in 'week'."""
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-001", trade_date=_TODAY, pnl=2.00, status="expired")
    _insert_trade(conn, ic_order_id="IC-002", trade_date="2026-06-17", pnl=1.00, status="expired")
    conn.close()
    result = dashboard._build_api_data()
    assert abs(result["stats"]["week"]["net_pnl"] - 3.00) < 0.01
    assert result["stats"]["week"]["total_trades"] == 2
    assert abs(result["stats"]["today"]["net_pnl"] - 2.00) < 0.01
    assert result["stats"]["today"]["total_trades"] == 1


# ── _load_symbols / _load_symbol ─────────────────────────────────────────────


def _write_config(monkeypatch, tmp_path, cfg):
    path = str(tmp_path / "config.json")
    with open(path, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr(dashboard._paths, "config_path", lambda: path)


def test_load_symbols_list(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {"symbols": ["xsp", "spx"]})
    assert dashboard._load_symbols() == ["XSP", "SPX"]


def test_load_symbols_deprecated_singular_alias(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {"symbol": "xsp"})
    assert dashboard._load_symbols() == ["XSP"]


def test_load_symbols_default_when_missing(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {})
    assert dashboard._load_symbols() == ["XSP"]


def test_load_symbols_missing_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard._paths, "config_path", lambda: str(tmp_path / "nonexistent.json"))
    assert dashboard._load_symbols() == ["XSP"]


# ── _build_api_data symbol filtering ─────────────────────────────────────────


def test_build_api_data_reports_configured_symbols(db_path, monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {"symbols": ["XSP", "SPX"]})
    result = dashboard._build_api_data()
    assert result["symbols"] == ["XSP", "SPX"]
    assert result["selected_symbol"] == "ALL"


def test_build_api_data_no_filter_includes_all_symbols(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-X", symbol="XSP", pnl=1.00, status="expired")
    _insert_trade(conn, ic_order_id="IC-S", symbol="SPX", pnl=3.00, status="expired")
    conn.close()
    result = dashboard._build_api_data()
    assert len(result["trades"]) == 2
    assert abs(result["stats"]["today"]["net_pnl"] - 4.00) < 0.01


def test_build_api_data_filtered_by_symbol(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-X", symbol="XSP", pnl=1.00, status="expired")
    _insert_trade(conn, ic_order_id="IC-S", symbol="SPX", pnl=3.00, status="expired")
    conn.close()
    result = dashboard._build_api_data("XSP")
    assert result["selected_symbol"] == "XSP"
    assert len(result["trades"]) == 1
    assert result["trades"][0]["symbol"] == "XSP"
    assert abs(result["stats"]["today"]["net_pnl"] - 1.00) < 0.01


def test_build_api_data_symbol_filter_case_insensitive(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-X", symbol="XSP", pnl=1.00, status="expired")
    conn.close()
    result = dashboard._build_api_data("xsp")
    assert len(result["trades"]) == 1


def test_build_api_data_all_keyword_is_unfiltered(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-X", symbol="XSP", pnl=1.00, status="expired")
    _insert_trade(conn, ic_order_id="IC-S", symbol="SPX", pnl=3.00, status="expired")
    conn.close()
    result = dashboard._build_api_data("ALL")
    assert len(result["trades"]) == 2


def test_build_api_data_symbol_filter_applies_to_recent_trades(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-X", symbol="XSP", pnl=1.00, status="expired")
    _insert_trade(conn, ic_order_id="IC-S", symbol="SPX", pnl=3.00, status="expired")
    conn.close()
    result = dashboard._build_api_data("SPX")
    recent = result["analytics"]["recent_trades"]
    assert len(recent) == 1
    assert recent[0]["symbol"] == "SPX"


def test_build_api_data_symbol_filter_applies_to_fee_summary(db_path):
    conn = dashboard._connect()
    _insert_trade(
        conn, ic_order_id="IC-X", symbol="XSP", net_credit=0.50, fees=0.10, pnl=1.00, status="expired"
    )
    _insert_trade(
        conn, ic_order_id="IC-S", symbol="SPX", net_credit=1.50, fees=0.30, pnl=3.00, status="expired"
    )
    conn.close()
    xsp_result = dashboard._build_api_data("XSP")
    spx_result = dashboard._build_api_data("SPX")
    assert xsp_result["analytics"]["fee_summary"]["gross_credit"] == 0.50
    assert spx_result["analytics"]["fee_summary"]["gross_credit"] == 1.50


# ── --mode/--db/--port default resolution (paper-trading dashboard) ────────────


def test_resolve_mode_defaults_live_no_overrides():
    db, port = dashboard._resolve_mode_defaults("live", None, None, default_db_path="/x/meic_trades.db")
    assert db == "/x/meic_trades.db"
    assert port == 5050


def test_resolve_mode_defaults_paper_no_overrides():
    db, port = dashboard._resolve_mode_defaults("paper", None, None, default_db_path="/x/meic_trades.db")
    assert db == dashboard._PAPER_DB_PATH
    assert port == 5051


def test_resolve_mode_defaults_explicit_db_overrides_mode():
    db, port = dashboard._resolve_mode_defaults(
        "paper", "/custom/path.db", None, default_db_path="/x/meic_trades.db"
    )
    assert db == "/custom/path.db"
    assert port == 5051  # --db overrides the DB path only; port default still follows mode


def test_resolve_mode_defaults_explicit_port_overrides_mode_default():
    db, port = dashboard._resolve_mode_defaults("live", None, 9999, default_db_path="/x/meic_trades.db")
    assert db == "/x/meic_trades.db"
    assert port == 9999


# ── Profile derivation and filtering (paper-trading dashboard) ─────────────────


def test_build_api_data_profiles_empty_falls_back_to_live(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-1", status="expired", pnl=1.0)  # no risk_profile set
    conn.close()
    result = dashboard._build_api_data()
    assert result["performance"]["profiles"] == ["live"]


def test_build_api_data_profiles_derived_from_distinct_values(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-1", status="expired", pnl=1.0, risk_profile="moderate")
    _insert_trade(conn, ic_order_id="IC-2", status="expired", pnl=2.0, risk_profile="conservative")
    _insert_trade(conn, ic_order_id="IC-3", status="expired", pnl=3.0, risk_profile="conservative")
    conn.close()
    result = dashboard._build_api_data()
    assert result["performance"]["profiles"] == ["conservative", "moderate"]


def test_build_api_data_profile_filter_scopes_trades_stats_and_performance(db_path):
    conn = dashboard._connect()
    _insert_trade(
        conn, ic_order_id="IC-1", status="expired", pnl=100.0, fees=1.0, risk_profile="conservative"
    )
    _insert_trade(conn, ic_order_id="IC-2", status="expired", pnl=50.0, fees=1.0, risk_profile="moderate")
    conn.close()

    filtered = dashboard._build_api_data(None, "conservative")
    unfiltered = dashboard._build_api_data(None, None)

    # profile is a peer of symbol: it scopes Today's Trades, the stats grid, and the
    # performance series alike. net_pnl sums the `pnl` column directly (not fee-subtracted)
    # to match _pnl_series/_stats_for_period's shared convention.
    assert len(filtered["trades"]) == 1
    assert filtered["trades"][0]["ic_order_id"] == "IC-1"
    assert filtered["stats"]["all_time"]["net_pnl"] == pytest.approx(100.0)
    assert sum(b["net_pnl"] for b in filtered["performance"]["daily"]) == pytest.approx(100.0)
    # ...while the blended (ALL) view keeps every profile's trades and their combined stats.
    assert len(unfiltered["trades"]) == 2
    assert unfiltered["stats"]["all_time"]["net_pnl"] == pytest.approx(150.0)
    assert filtered["performance"]["selected_profile"] == "conservative"
    assert unfiltered["performance"]["selected_profile"] == "ALL"


# ── Width study (wing-width forced-sampling arms) ───────────────────────────────


def test_study_arms_has_every_configured_symbol_even_with_no_trades(db_path):
    result = dashboard._build_api_data()
    ws = result["study_arms"]
    assert ws["arms"] == dashboard.STUDY_ARMS
    assert set(ws["symbols"]) == set(dashboard._load_symbols())
    for sym in ws["symbols"]:
        assert set(ws["symbols"][sym]) == set(dashboard.STUDY_ARMS)
        for arm in dashboard.STUDY_ARMS:
            assert ws["symbols"][sym][arm] == []


def test_study_arms_cell_matches_pnl_series_for_that_profile_and_symbol(db_path, monkeypatch, tmp_path):
    # Pins its own symbol set -- the frame only builds cells for CONFIGURED symbols, so this must
    # not depend on whatever the operator currently trades (see the sibling test below).
    _write_config(monkeypatch, tmp_path, {"symbols": ["XSP", "QQQ"]})
    conn = dashboard._connect()
    _insert_trade(
        conn, ic_order_id="IC-1", symbol="XSP", status="expired", pnl=12.0, fees=1.0, risk_profile="open"
    )
    _insert_trade(
        conn,
        ic_order_id="IC-2",
        symbol="XSP",
        status="expired",
        pnl=-4.0,
        fees=1.0,
        risk_profile="control",
    )
    _insert_trade(
        conn,
        ic_order_id="IC-3",
        symbol="QQQ",
        status="expired",
        pnl=7.0,
        fees=1.0,
        risk_profile="open",
    )
    # A ladder trade must never leak into a study-arms cell.
    _insert_trade(
        conn,
        ic_order_id="IC-4",
        symbol="XSP",
        status="expired",
        pnl=99.0,
        fees=1.0,
        risk_profile="conservative",
    )
    conn.close()

    result = dashboard._build_api_data()
    ws = result["study_arms"]["symbols"]

    conn = dashboard._connect()
    for sym, arm in (("XSP", "open"), ("XSP", "control"), ("QQQ", "open")):
        expected = dashboard._pnl_series(conn, "daily", symbol=sym, profile=arm)
        assert ws[sym][arm] == expected
    assert ws["QQQ"]["control"] == []  # an arm with no trades on that symbol is an empty cell
    conn.close()

    xsp_open_pnl = sum(b["net_pnl"] for b in ws["XSP"]["open"])
    assert xsp_open_pnl == pytest.approx(12.0)


def test_study_arms_ignores_the_page_symbol_and_profile_filters(db_path, monkeypatch, tmp_path):
    """Like by_profile above, the study-arms cells always show every configured symbol's arms —
    the page's symbol/profile selectors must not narrow this comparison view.

    Pins its own symbol set: the frame only builds cells for CONFIGURED symbols, so borrowing the
    operator's config made this assert on an unrelated setting (it broke when the set narrowed to
    SPX on 2026-08-01)."""
    _write_config(monkeypatch, tmp_path, {"symbols": ["XSP", "QQQ"]})
    conn = dashboard._connect()
    _insert_trade(
        conn, ic_order_id="IC-1", symbol="XSP", status="expired", pnl=5.0, fees=0.0, risk_profile="open"
    )
    conn.close()

    filtered = dashboard._build_api_data("QQQ", "conservative")
    assert filtered["study_arms"]["symbols"]["XSP"]["open"]
    assert sum(b["net_pnl"] for b in filtered["study_arms"]["symbols"]["XSP"]["open"]) == pytest.approx(5.0)


# ── sessions beside trades ──────────────────────────────────────────────────────


def test_by_profile_compare_reports_sessions_alongside_trades(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-1", trade_date="2026-06-18", risk_profile="open", pnl=1.0)
    _insert_trade(conn, ic_order_id="IC-2", trade_date="2026-06-18", risk_profile="open", pnl=2.0)
    _insert_trade(conn, ic_order_id="IC-3", trade_date="2026-06-19", risk_profile="open", pnl=3.0)
    conn.close()
    rows = dashboard._by_profile_compare(dashboard._connect(), None)
    row = next(r for r in rows if r["profile"] == "open")
    assert row["trades"] == 3
    assert row["sessions"] == 2


def test_by_signal_buckets_report_sessions(db_path):
    conn = dashboard._connect()
    _insert_trade(conn, ic_order_id="IC-1", trade_date="2026-06-18", symbol="SPX", pnl=1.0, fees=0.1)
    _insert_trade(conn, ic_order_id="IC-2", trade_date="2026-06-19", symbol="SPX", pnl=2.0, fees=0.1)
    conn.close()
    conn = dashboard._connect()
    sig = dashboard._by_signal(conn, "", [])
    conn.close()
    row = next(r for r in sig["by_symbol"] if r["bucket"] == "SPX")
    assert row["trades"] == 2
    assert row["sessions"] == 2


# ── arm scorecard / stop-policy card / regime coverage (paper-era read surfaces) ────
# These panels are guarded behind the `era` column (see _arm_scorecard's docstring) — the
# minimal DDL fixture above has no `era` column, so they must degrade gracefully there
# (asserted below), and are otherwise exercised against the real schema via db.cmd_init_db,
# matching how test_eod_supplement.py seeds a full paper DB.

import argparse  # noqa: E402

from cherrypick.meic import db as _db  # noqa: E402


def _ns(**kw):
    return argparse.Namespace(**kw)


def test_arm_scorecard_and_stop_policy_card_are_empty_without_an_era_column(db_path):
    result = dashboard._build_api_data()
    assert result["performance"]["arm_scorecard"] == []
    assert result["performance"]["stop_policy_card"] == []
    assert result["analytics"]["regime"] == {"coverage": {}, "by_dimension": {}}


@pytest.fixture
def paper_db_path(monkeypatch, tmp_path):
    """A temp DB built through db.py's own schema (era column, ic_spread_legs, regime
    columns included) — the real shape _arm_scorecard/_stop_policy_card/_regime_coverage_panel
    read against, unlike the minimal DDL fixture above."""
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(_db, "_DB_PATH", path)
    monkeypatch.setattr(dashboard, "_DB_PATH", path)
    _db.cmd_init_db(_ns())
    return path


def _seed_arm_trade(order_id, arm, *, put_status, call_status, pnl=180.0, fees=6.89, **overrides):
    data = {
        "ic_order_id": order_id,
        "trade_date": "2026-08-06",
        "entry_time": "2026-08-06 11:00:00",
        "symbol": "SPX",
        "put_strike": 6000,
        "call_strike": 6100,
        "wing_width": 10,
        "net_credit": 1.8,
        "put_credit": 0.9,
        "call_credit": 0.9,
        "quantity": 1,
        "underlying_price_entry": 6050.0,
        "risk_profile": arm,
        "status": "expired",
        "exit_reason": "cash_settled_expiration",
        "pnl": pnl,
        "fees": fees,
        "dollar_multiplier": 100,
        "put_settle_value": 0.0,
        "call_settle_value": 0.0,
    }
    data.update(overrides)
    _db.cmd_save_trade(_ns(data=json.dumps(data)))
    for side, status in (("put", put_status), ("call", call_status)):
        _db.cmd_record_leg_exit(
            _ns(
                ic_order_id=order_id,
                side=side,
                status=status,
                exit_time="2026-08-06 16:00:00",
                exit_reason="cash_settled_expiration",
                exit_price=0.0,
                pnl=pnl / 2,
            )
        )


def test_arm_scorecard_reports_breakeven_identity_per_arm(paper_db_path, capsys):
    _seed_arm_trade("C-1", "control", put_status="expired", call_status="expired")
    _seed_arm_trade(
        "O-1",
        "open",
        put_status="expired",
        call_status="expired",
        put_max_cost=0.1,
        call_max_cost=0.15,
    )
    capsys.readouterr()
    result = dashboard._build_api_data()
    rows = {r["arm"]: r for r in result["performance"]["arm_scorecard"]}
    assert "control" in rows and "open" in rows
    assert rows["control"]["clean_pct"] == 100.0
    assert rows["control"]["double_stop_pct"] == 0.0
    assert rows["control"]["sessions"] == 1


def test_stop_policy_card_covers_every_derived_policy(paper_db_path, capsys):
    _seed_arm_trade(
        "O-1", "open", put_status="expired", call_status="expired", put_max_cost=0.1, call_max_cost=0.15
    )
    capsys.readouterr()
    result = dashboard._build_api_data()
    names = {row["policy"] for row in result["performance"]["stop_policy_card"]}
    assert names == {"stop-none", "stop-0.75-net", "stop-2.0-side", "strike-touch"}
    assert all(row["arm"] == "open" for row in result["performance"]["stop_policy_card"])


def test_regime_coverage_panel_withholds_degenerate_dimension_breakdowns(paper_db_path, capsys):
    _seed_arm_trade(
        "C-1",
        "control",
        put_status="expired",
        call_status="expired",
        entry_gex_bucket="positive",
        entry_gex_value=0.4,
    )
    capsys.readouterr()
    result = dashboard._build_api_data()
    reg = result["analytics"]["regime"]
    assert reg["coverage"]["dimensions"]["gex"]["tagged"] == 1
    assert reg["coverage"]["dimensions"]["gex"]["degenerate"] is True
    assert "gex" not in reg["by_dimension"]  # degenerate -> withheld, per the panel's docstring
