import argparse
import json

import pytest

from cherrypick.earnings import db_paper


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _save(order_id, symbol, strategy, entry_credit):
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "strategy": strategy,
                    "expiration": "2026-08-21",
                    "entry_credit": entry_credit,
                    "legs_json": "[]",
                }
            )
        )
    )


def _close(order_id, exit_debit, pnl):
    db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "exit_debit": exit_debit,
                    "pnl": pnl,
                }
            )
        )
    )


def test_save_and_get_open_positions_roundtrip():
    _save("P1", "AAPL", "iron_fly", 2.0)
    positions = db_paper.cmd_get_open_positions(_ns())
    assert len(positions["positions"]) == 1


def test_pnl_summary_empty():
    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))
    assert result == {
        "ok": True,
        "strategy_filter": None,
        "profile_filter": None,
        "total_trades": 0,
        "total_pnl": 0.0,
        "avg_pnl": None,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": None,
        "avg_win": None,
        "avg_loss": None,
        "by_strategy": {},
        "by_profile": {},
        "trades": [],
    }


def test_pnl_summary_computes_win_loss_stats():
    _save("W1", "AAPL", "iron_fly", 2.0)
    _close("W1", 1.0, 100.0)
    _save("L1", "MSFT", "iron_fly", 2.0)
    _close("L1", 3.0, -50.0)

    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None))
    assert result["total_trades"] == 2
    assert result["total_pnl"] == pytest.approx(50.0)
    assert result["win_count"] == 1
    assert result["loss_count"] == 1
    assert result["win_rate"] == pytest.approx(0.5)
    assert result["avg_win"] == pytest.approx(100.0)
    assert result["avg_loss"] == pytest.approx(-50.0)


def test_pnl_summary_filters_by_strategy():
    _save("A1", "AAPL", "iron_fly", 2.0)
    _close("A1", 1.0, 100.0)
    _save("B1", "MSFT", "double_calendar", 2.0)
    _close("B1", 1.0, 50.0)

    result = db_paper.cmd_get_pnl_summary(_ns(strategy="iron_fly"))
    assert result["total_trades"] == 1
    assert result["total_pnl"] == pytest.approx(100.0)


def test_pnl_summary_by_strategy_breakdown():
    _save("A1", "AAPL", "iron_fly", 2.0)
    _close("A1", 1.0, 100.0)
    _save("A2", "GOOG", "iron_fly", 2.0)
    _close("A2", 1.0, 50.0)

    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None))
    assert result["by_strategy"]["iron_fly"] == {"trades": 2, "total_pnl": 150.0, "avg_pnl": 75.0}


def test_pnl_summary_ignores_open_positions():
    _save("OPEN1", "AAPL", "iron_fly", 2.0)
    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None))
    assert result["total_trades"] == 0


def test_migration_adds_new_columns_without_dropping_rows(tmp_path, monkeypatch):
    import sqlite3

    old_db = tmp_path / "old_schema.db"
    conn = sqlite3.connect(old_db)
    conn.executescript("""
        CREATE TABLE trades (
            order_id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, expiration TEXT,
            short_strike REAL, long_call_strike REAL, long_put_strike REAL,
            legs_json TEXT, entry_credit REAL, exit_debit REAL, pnl REAL,
            opened_at REAL, closed_at REAL
        );
        CREATE TABLE scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT, strategy TEXT,
            symbol TEXT, tier TEXT, outcome TEXT, reason TEXT, logged_at REAL
        );
    """)
    conn.execute(
        "INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, opened_at) "
        "VALUES ('LEGACY1', 'iron_fly', 'AAPL', '2026-08-21', 2.0, 100.0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_paper, "DB_PATH", old_db)
    positions = db_paper.cmd_get_open_positions(_ns())
    assert len(positions["positions"]) == 1
    assert positions["positions"][0]["order_id"] == "LEGACY1"
    assert positions["positions"][0]["profile"] == "default"
    assert positions["positions"][0]["quantity"] is None


def test_save_trade_persists_profile_quantity_and_costs():
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": "PT1",
                    "symbol": "AAPL",
                    "strategy": "atm_calendar",
                    "expiration": "2026-08-21",
                    "legs_json": "[]",
                    "entry_credit": -15.60,
                    "profile": "strat_test",
                    "quantity": 5,
                    "capital_at_risk": 1560.0,
                    "entry_cost": 12.30,
                    "entry_context": {"iv_rv_ratio": 1.1, "dispersion": 0.12},
                }
            )
        )
    )
    positions = db_paper.cmd_get_open_positions(_ns())["positions"]
    row = positions[0]
    assert row["profile"] == "strat_test"
    assert row["quantity"] == 5
    assert row["capital_at_risk"] == pytest.approx(1560.0)
    assert row["entry_cost"] == pytest.approx(12.30)
    assert json.loads(row["entry_context"]) == {"iv_rv_ratio": 1.1, "dispersion": 0.12}


def test_save_close_persists_exit_cost():
    _save("PC1", "AAPL", "iron_fly", 2.0)
    db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {
                    "order_id": "PC1",
                    "exit_debit": 1.0,
                    "pnl": 100.0,
                    "exit_cost": 3.40,
                }
            )
        )
    )
    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))
    assert result["trades"][0]["exit_cost"] == pytest.approx(3.40)


def test_log_scan_persists_profile():
    db_paper.cmd_log_scan(
        _ns(
            data=json.dumps(
                {
                    "scan_date": "2026-07-08",
                    "symbol": "AAPL",
                    "strategy": "iron_fly",
                    "tier": "Tier 1",
                    "outcome": "Tier 1",
                    "profile": "strat_test",
                }
            )
        )
    )
    import sqlite3

    conn = sqlite3.connect(db_paper.DB_PATH)
    row = conn.execute("SELECT profile FROM scan_log WHERE symbol = 'AAPL'").fetchone()
    conn.close()
    assert row[0] == "strat_test"


def test_pnl_summary_filters_by_profile():
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": "PB1",
                    "symbol": "AAPL",
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "legs_json": "[]",
                    "entry_credit": 2.0,
                    "profile": "conservative",
                }
            )
        )
    )
    db_paper.cmd_save_close(_ns(data=json.dumps({"order_id": "PB1", "exit_debit": 1.0, "pnl": 100.0})))

    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": "PB2",
                    "symbol": "MSFT",
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "legs_json": "[]",
                    "entry_credit": 2.0,
                    "profile": "aggressive",
                }
            )
        )
    )
    db_paper.cmd_save_close(_ns(data=json.dumps({"order_id": "PB2", "exit_debit": 1.0, "pnl": 50.0})))

    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile="conservative"))
    assert result["total_trades"] == 1
    assert result["total_pnl"] == pytest.approx(100.0)
    assert result["by_profile"]["conservative"]["trades"] == 1


def test_save_trade_persists_entry_iv():
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": "IV1",
                    "symbol": "AAPL",
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "legs_json": "[]",
                    "entry_credit": 2.0,
                    "entry_iv": 0.45,
                }
            )
        )
    )
    positions = db_paper.cmd_get_open_positions(_ns())["positions"]
    assert positions[0]["entry_iv"] == pytest.approx(0.45)


def test_save_close_persists_exit_iv():
    _save("IV2", "AAPL", "iron_fly", 2.0)
    db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {
                    "order_id": "IV2",
                    "exit_debit": 1.0,
                    "pnl": 100.0,
                    "exit_iv": 0.20,
                }
            )
        )
    )
    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))
    assert result["trades"][0]["exit_iv"] == pytest.approx(0.20)


def test_migration_adds_iv_columns_to_legacy_schema(tmp_path, monkeypatch):
    import sqlite3

    old_db = tmp_path / "no_iv_schema.db"
    conn = sqlite3.connect(old_db)
    conn.executescript("""
        CREATE TABLE trades (
            order_id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, expiration TEXT,
            entry_credit REAL, opened_at REAL, closed_at REAL,
            profile TEXT NOT NULL DEFAULT 'default', quantity INTEGER,
            capital_at_risk REAL, entry_cost REAL, exit_cost REAL, entry_context TEXT
        );
        CREATE TABLE scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT, strategy TEXT,
            symbol TEXT, tier TEXT, outcome TEXT, reason TEXT, logged_at REAL,
            profile TEXT NOT NULL DEFAULT 'default'
        );
    """)
    conn.execute(
        "INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, opened_at) "
        "VALUES ('LEGACY_NO_IV', 'iron_fly', 'AAPL', '2026-08-21', 2.0, 100.0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_paper, "DB_PATH", old_db)
    positions = db_paper.cmd_get_open_positions(_ns())["positions"]
    assert len(positions) == 1
    assert positions[0]["entry_iv"] is None
    assert positions[0]["exit_iv"] is None


# --- record_close_failure: stranded positions accumulate visible attempts -------


def test_record_close_failure_increments_attempts():
    _save("S1", "HALT", "iron_fly", 2.0)
    r1 = db_paper.cmd_record_close_failure(
        _ns(data=json.dumps({"order_id": "S1", "reason": "leg_quotes_unavailable"}))
    )
    r2 = db_paper.cmd_record_close_failure(
        _ns(data=json.dumps({"order_id": "S1", "reason": "leg_quotes_unavailable"}))
    )
    assert r1 == {"ok": True, "order_id": "S1", "close_attempts": 1, "status": "open"}
    assert r2["close_attempts"] == 2
    pos = db_paper.cmd_get_open_positions(_ns())["positions"][0]
    assert pos["close_attempts"] == 2
    assert pos["last_close_error"] == "leg_quotes_unavailable"
    assert pos["last_close_attempt_at"] is not None


def test_a_position_becomes_stranded_at_the_second_failed_close():
    """One missed sweep is a slow open or a halted name; two is a position nothing can price. The
    threshold used to be applied inside a single sweep, so a position that missed one attempt per
    day looked fresh every morning — as a status it outlives the run that noticed it."""
    _save("S2", "HALT", "iron_fly", 2.0)
    first = db_paper.cmd_record_close_failure(_ns(data=json.dumps({"order_id": "S2", "reason": "x"})))
    assert first["status"] == "open"

    second = db_paper.cmd_record_close_failure(_ns(data=json.dumps({"order_id": "S2", "reason": "x"})))
    assert second["status"] == "stranded"
    assert db_paper.cmd_get_open_positions(_ns())["positions"][0]["status"] == "stranded"


def test_closing_a_stranded_position_clears_the_status():
    """Stranded is a state, not a scar: whatever finally prices it closes it like any other."""
    _save("S3", "HALT", "iron_fly", 2.0)
    for _ in range(2):
        db_paper.cmd_record_close_failure(_ns(data=json.dumps({"order_id": "S3", "reason": "x"})))
    db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {"order_id": "S3", "exit_debit": 1.0, "pnl": 100.0, "exit_reason": "close_window"}
            )
        )
    )
    row = db_paper.cmd_get_pnl_summary(_ns(strategy=None, profile=None))["trades"][0]
    assert row["status"] == "closed" and row["exit_reason"] == "close_window"


def test_record_close_failure_ignores_closed_and_unknown_positions():
    _save("C1", "AAPL", "iron_fly", 2.0)
    _close("C1", 1.0, 100.0)
    closed = db_paper.cmd_record_close_failure(_ns(data=json.dumps({"order_id": "C1", "reason": "x"})))
    unknown = db_paper.cmd_record_close_failure(_ns(data=json.dumps({"order_id": "NOPE", "reason": "x"})))
    assert closed["ok"] is False
    assert unknown["ok"] is False


def test_save_entry_review_requires_fields():
    result = db_paper.cmd_save_entry_review(_ns(data=json.dumps({"symbol": "AAPL"})))
    assert result["ok"] is False


def test_save_entry_review_and_get_roundtrip_new_columns():
    spec = {
        "scan_date": "2026-08-07",
        "symbol": "AAPL",
        "timing": "After market close",
        "strategy": "iron_fly",
        "iv_rv_ratio": 1.4,
        "iv_rv_source": "tastytrade",
        "avg_actual_move_pct": 0.04,
        "move_dispersion_pct": 0.01,
        "max_actual_move_pct": 0.06,
        "implied_vs_avg_actual": 1.3,
        "move_tail_veto": True,
        "iv_rank": 0.7,
        "iv_percentile": 0.65,
        "net_combo_spread_pct": 0.03,
        "composite_score": 0.42,
        "selected": True,
        "reason": "opened iron_fly",
        "criteria_json": {"price": 150.0},
    }
    saved = db_paper.cmd_save_entry_review(_ns(data=json.dumps(spec)))
    assert saved["ok"] is True

    fetched = db_paper.cmd_get_entry_reviews(_ns(date=None, scan_date="2026-08-07"))
    row = fetched["reviews"][0]
    assert row["iv_rv_source"] == "tastytrade"
    assert row["move_tail_veto"] == 1
    assert row["net_combo_spread_pct"] == 0.03
    assert row["composite_score"] == 0.42
    assert json.loads(row["criteria_json"]) == {"price": 150.0}


def test_entry_reviews_schema_parity_with_live_db(tmp_path, monkeypatch):
    """db.py and db_paper.py's entry_reviews tables must carry the same columns (minus db_paper's
    market_context/entry_reviews-adjacent tables that don't exist on the live side) -- a drift here
    would silently lose fields depending on which module wrote a row. Mirrors this project's existing
    schema-parity discipline for trades/scan_log (see either module's own docstring)."""
    from cherrypick.earnings import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "earnings_trades.db")
    db.cmd_init_db(argparse.Namespace())

    paper_conn = db_paper._conn()
    live_conn = db._conn()
    try:
        paper_cols = {r[1] for r in paper_conn.execute("PRAGMA table_info(entry_reviews)")}
        live_cols = {r[1] for r in live_conn.execute("PRAGMA table_info(entry_reviews)")}
    finally:
        paper_conn.close()
        live_conn.close()
    assert paper_cols == live_cols
