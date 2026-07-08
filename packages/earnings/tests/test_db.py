import argparse
import json

import pytest

import db


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "earnings_trades.db")
    db.cmd_init_db(argparse.Namespace())


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def test_init_db_is_idempotent():
    result = db.cmd_init_db(_ns())
    assert result["ok"] is True


def test_save_trade_requires_fields():
    result = db.cmd_save_trade(_ns(data=json.dumps({"symbol": "AAPL"})))
    assert result["ok"] is False


def test_save_trade_and_get_open_positions_roundtrip():
    trade = {
        "order_id": "O1", "symbol": "AAPL", "strategy": "iron_fly",
        "expiration": "2026-08-21", "entry_credit": 2.50,
        "legs_json": json.dumps([{"symbol": "AAPL_C", "action": "Sell to Open", "quantity": 1}]),
    }
    result = db.cmd_save_trade(_ns(data=json.dumps(trade)))
    assert result["ok"] is True

    positions = db.cmd_get_open_positions(_ns())
    assert positions["ok"] is True
    assert len(positions["positions"]) == 1
    assert positions["positions"][0]["symbol"] == "AAPL"


def test_save_trade_with_legs_populates_trade_legs():
    trade = {
        "order_id": "O2", "symbol": "MSFT", "strategy": "double_calendar",
        "expiration": "2026-08-21", "entry_credit": -3.0,
        "legs_json": "[]",
        "legs": [
            {"leg_role": "front_call", "symbol": "MSFT_FC", "action": "Sell to Open", "quantity": 1},
            {"leg_role": "front_put", "symbol": "MSFT_FP", "action": "Sell to Open", "quantity": 1},
        ],
    }
    result = db.cmd_save_trade(_ns(data=json.dumps(trade)))
    assert result["ok"] is True

    legs = db.cmd_get_open_legs(_ns(order_id="O2"))
    assert legs["ok"] is True
    assert {leg["leg_role"] for leg in legs["legs"]} == {"front_call", "front_put"}


def test_save_trade_duplicate_order_id_fails():
    trade = {
        "order_id": "DUPE", "symbol": "AAPL", "strategy": "iron_fly",
        "expiration": "2026-08-21", "entry_credit": 2.50, "legs_json": "[]",
    }
    assert db.cmd_save_trade(_ns(data=json.dumps(trade)))["ok"] is True
    result = db.cmd_save_trade(_ns(data=json.dumps(trade)))
    assert result["ok"] is False


def test_save_leg_close_updates_open_leg():
    trade = {
        "order_id": "O3", "symbol": "TSLA", "strategy": "short_strangle",
        "expiration": "2026-08-21", "entry_credit": 4.0, "legs_json": "[]",
        "legs": [{"leg_role": "short_call", "symbol": "TSLA_SC", "action": "Sell to Open", "quantity": 1}],
    }
    db.cmd_save_trade(_ns(data=json.dumps(trade)))

    result = db.cmd_save_leg_close(_ns(data=json.dumps({
        "order_id": "O3", "leg_role": "short_call", "close_price": 1.0,
    })))
    assert result["ok"] is True

    legs = db.cmd_get_open_legs(_ns(order_id="O3"))
    assert legs["legs"] == []


def test_save_leg_close_missing_position_fails():
    result = db.cmd_save_leg_close(_ns(data=json.dumps({
        "order_id": "NOPE", "leg_role": "short_call", "close_price": 1.0,
    })))
    assert result["ok"] is False


def test_save_close_updates_trade():
    trade = {
        "order_id": "O4", "symbol": "NVDA", "strategy": "iron_fly",
        "expiration": "2026-08-21", "entry_credit": 3.0, "legs_json": "[]",
    }
    db.cmd_save_trade(_ns(data=json.dumps(trade)))

    result = db.cmd_save_close(_ns(data=json.dumps({
        "order_id": "O4", "exit_debit": 1.0, "pnl": 200.0,
    })))
    assert result["ok"] is True

    positions = db.cmd_get_open_positions(_ns())
    assert all(p["symbol"] != "NVDA" for p in positions["positions"])


def test_save_close_missing_order_id_fails():
    result = db.cmd_save_close(_ns(data=json.dumps({"exit_debit": 1.0})))
    assert result["ok"] is False


def test_log_scan_requires_fields():
    result = db.cmd_log_scan(_ns(data=json.dumps({"symbol": "AAPL"})))
    assert result["ok"] is False


def test_log_scan_success():
    result = db.cmd_log_scan(_ns(data=json.dumps({
        "scan_date": "2026-07-07", "symbol": "AAPL", "strategy": "iron_fly", "tier": "Tier 1",
    })))
    assert result["ok"] is True
