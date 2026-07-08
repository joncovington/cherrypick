import argparse
import json

import pytest

import db_paper


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _save(order_id, symbol, strategy, entry_credit):
    db_paper.cmd_save_trade(_ns(data=json.dumps({
        "order_id": order_id, "symbol": symbol, "strategy": strategy,
        "expiration": "2026-08-21", "entry_credit": entry_credit, "legs_json": "[]",
    })))


def _close(order_id, exit_debit, pnl):
    db_paper.cmd_save_close(_ns(data=json.dumps({
        "order_id": order_id, "exit_debit": exit_debit, "pnl": pnl,
    })))


def test_save_and_get_open_positions_roundtrip():
    _save("P1", "AAPL", "iron_fly", 2.0)
    positions = db_paper.cmd_get_open_positions(_ns())
    assert len(positions["positions"]) == 1


def test_pnl_summary_empty():
    result = db_paper.cmd_get_pnl_summary(_ns(strategy=None))
    assert result == {
        "ok": True, "strategy_filter": None, "total_trades": 0, "total_pnl": 0.0,
        "avg_pnl": None, "win_count": 0, "loss_count": 0, "win_rate": None,
        "avg_win": None, "avg_loss": None, "by_strategy": {}, "trades": [],
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
