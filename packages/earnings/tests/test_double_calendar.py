from datetime import date, timedelta

import pytest

from strategies import double_calendar


def test_apply_tiering_tier1_when_all_pass(base_strategy_config, good_criteria):
    result = double_calendar.apply_tiering(good_criteria, base_strategy_config)
    assert result["tier"] == "Tier 1"


def test_apply_tiering_rejects_high_dispersion(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "realized_move_dispersion_pct": 0.30}
    result = double_calendar.apply_tiering(criteria, base_strategy_config)
    assert "realized_move_too_inconsistent" in result["hard_fail_reasons"]


def test_apply_tiering_ignores_dispersion_when_absent(base_strategy_config, good_criteria):
    result = double_calendar.apply_tiering(good_criteria, base_strategy_config)
    assert "realized_move_too_inconsistent" not in result["hard_fail_reasons"]


def test_realized_move_dispersion_insufficient_sample(monkeypatch):
    monkeypatch.setattr(double_calendar.scanner, "compute_winrate", lambda *a, **k: {
        "quarters": [{"realized_move": 5.0, "pre_close": 100.0}],
    })
    result = double_calendar.realized_move_dispersion("AAPL", {}, 8)
    assert result["ok"] is False


def test_realized_move_dispersion_computes_stddev(monkeypatch):
    monkeypatch.setattr(double_calendar.scanner, "compute_winrate", lambda *a, **k: {
        "quarters": [
            {"realized_move": 5.0, "pre_close": 100.0},
            {"realized_move": 10.0, "pre_close": 100.0},
            {"realized_move": 15.0, "pre_close": 100.0},
        ],
    })
    result = double_calendar.realized_move_dispersion("AAPL", {}, 8)
    assert result["ok"] is True
    assert result["sample_size"] == 3
    assert result["mean_realized_move_pct"] == pytest.approx(0.10)


def test_label_order_legs_assigns_roles():
    order_result = {"order": {"legs": [
        {"symbol": "FC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "FP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "BC", "action": "Buy to Open", "quantity": 1},
        {"symbol": "BP", "action": "Buy to Open", "quantity": 1},
    ]}}
    labeled = double_calendar.label_order_legs(order_result)
    assert [leg["leg_role"] for leg in labeled] == ["front_call", "front_put", "back_call", "back_put"]


def _open_legs():
    return [
        {"leg_role": "front_call", "symbol": "FC"},
        {"leg_role": "front_put", "symbol": "FP"},
        {"leg_role": "back_call", "symbol": "BC"},
        {"leg_role": "back_put", "symbol": "BP"},
    ]


def test_evaluate_position_profit_target():
    position = {"entry_credit": -4.0, "expiration": str(date.today() + timedelta(days=30))}
    quotes = {
        "FC": {"bid": 0.02, "ask": 0.05}, "FP": {"bid": 0.02, "ask": 0.05},
        "BC": {"bid": 3.0, "ask": 3.1}, "BP": {"bid": 3.0, "ask": 3.1},
    }
    result = double_calendar.evaluate_position(position, _open_legs(), quotes, config={"profit_target_pct": 0.10})
    assert result["action"] == "close_all"
    assert result["reason"] == "profit_target"


def test_evaluate_position_time_exit_near_expiration():
    from datetime import timedelta
    position = {"entry_credit": -4.0, "expiration": str(date.today() + timedelta(days=2))}
    result = double_calendar.evaluate_position(position, _open_legs(), quotes={}, config={"exit_days_before_front_expiration": 5})
    assert result == {"action": "close_all", "reason": "time_exit"}


def test_evaluate_position_leg_stop_on_high_delta():
    from datetime import timedelta
    position = {"entry_credit": -4.0, "expiration": str(date.today() + timedelta(days=30))}
    quotes = {"FC": {"delta": 0.55}}
    result = double_calendar.evaluate_position(position, _open_legs(), quotes, config={"leg_stop_delta_abs": 0.45})
    assert result == {"action": "close_side", "side": "call", "reason": "leg_stop"}


def test_evaluate_position_overnight_gap_suffix():
    from datetime import timedelta
    position = {"entry_credit": -4.0, "expiration": str(date.today() + timedelta(days=30))}
    quotes = {"FC": {"delta": 0.55}}
    result = double_calendar.evaluate_position(position, _open_legs(), quotes, config={"leg_stop_delta_abs": 0.45}, is_first_check_of_day=True)
    assert result["reason"] == "leg_stop_overnight_gap"


def test_evaluate_position_hold_when_nothing_triggers():
    from datetime import timedelta
    position = {"entry_credit": -4.0, "expiration": str(date.today() + timedelta(days=30))}
    result = double_calendar.evaluate_position(position, _open_legs(), quotes={}, config={})
    assert result == {"action": "hold"}
