import json

import pytest

from strategies import expected_move_butterfly


def test_apply_tiering_tier1_when_all_pass(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3}
    result = expected_move_butterfly.apply_tiering(criteria, base_strategy_config)
    assert result["tier"] == "Tier 1"


def test_apply_tiering_rejects_insufficient_skew(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3, "skew_abs": 0.001}
    result = expected_move_butterfly.apply_tiering(criteria, base_strategy_config)
    assert "insufficient_skew_signal" in result["hard_fail_reasons"]


def test_apply_tiering_rejects_front_expiration_too_far(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 30}
    result = expected_move_butterfly.apply_tiering(criteria, base_strategy_config)
    assert "front_expiration_days_too_far_out" in result["hard_fail_reasons"]


def test_select_side_picks_richer_side(monkeypatch):
    entries = [
        {"option_type": "C", "delta": 0.25, "iv": 0.60},
        {"option_type": "P", "delta": -0.25, "iv": 0.40},
    ]
    monkeypatch.setattr(expected_move_butterfly.scanner, "call_tt", lambda args: {"ok": True, "chain": {"2026-08-21": entries}})
    result = expected_move_butterfly.select_side("AAPL", "2026-08-21", 150.0, {"skew_delta_target": 0.25})
    assert result["ok"] is True
    assert result["side"] == "call"
    assert result["skew"] == pytest.approx(0.20)


def test_select_side_missing_greeks_returns_error(monkeypatch):
    monkeypatch.setattr(expected_move_butterfly.scanner, "call_tt", lambda args: {"ok": True, "chain": {"2026-08-21": []}})
    result = expected_move_butterfly.select_side("AAPL", "2026-08-21", 150.0, {})
    assert result["ok"] is False


def test_evaluate_position_delegates_to_debit_spread_exit():
    legs = [
        {"symbol": "ATM", "action": "Buy to Open", "quantity": 1},
        {"symbol": "SHORT", "action": "Sell to Open", "quantity": 2},
        {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
    ]
    position = {"entry_credit": -2.0, "legs_json": json.dumps(legs)}
    quotes = {
        "ATM": {"bid": 0.1, "ask": 0.2}, "SHORT": {"bid": 1.0, "ask": 1.1}, "FAR": {"bid": 0.05, "ask": 0.1},
    }
    result = expected_move_butterfly.evaluate_position(position, quotes, config={"profit_target_pct": 0.99, "stop_loss_pct_of_debit": 0.01})
    assert result["action"] == "close_all"
    assert result["reason"] == "stop_loss"


def test_evaluate_position_holds_on_incomplete_quotes():
    legs = [{"symbol": "ATM", "action": "Buy to Open", "quantity": 1}]
    position = {"entry_credit": -2.0, "legs_json": json.dumps(legs)}
    result = expected_move_butterfly.evaluate_position(position, quotes={}, config={})
    assert result == {"action": "hold"}
