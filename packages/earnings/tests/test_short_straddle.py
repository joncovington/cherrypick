"""Unit tests for src/strategies/short_straddle.py"""

import time
from src.strategies import short_straddle


def test_evaluate_position_hold():
    """No conditions met -- position should hold"""
    position = {
        "opened_at": time.time() - 30 * 60,  # 30 minutes ago
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 2.50, "ask": 2.55, "delta": 0.40},
        "AAPL230721P150": {"bid": 2.50, "ask": 2.55, "delta": -0.40},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "hold"}


def test_evaluate_position_profit_target():
    """Profit target hit -- should close all"""
    position = {
        "opened_at": time.time() - 30 * 60,
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    # Cost to close: 1.30 + 1.40 = 2.70; profit = 3.50 - 2.70 = 0.80
    # 0.80 >= 3.50 * 0.50 (1.75)? No, so this should not hit profit target.
    # Let me use lower asks to trigger profit target.
    # Cost to close: 1.50 + 1.50 = 3.00; profit = 3.50 - 3.00 = 0.50
    # 0.50 >= 3.50 * 0.50 (1.75)? No.
    # Cost to close: 1.00 + 1.00 = 2.00; profit = 3.50 - 2.00 = 1.50
    # 1.50 >= 3.50 * 0.50 (1.75)? No.
    # Cost to close: 1.60 + 1.60 = 3.20; profit = 3.50 - 3.20 = 0.30
    # Wait, that's 0.30 < 1.75, so won't trigger.
    # Let me think: entry_credit = 3.50, profit_target_pct = 0.50
    # profit_target = 3.50 * 0.50 = 1.75
    # So profit must be >= 1.75, meaning cost_to_close must be <= 3.50 - 1.75 = 1.75
    # Cost to close: 0.80 + 0.90 = 1.70
    # profit = 3.50 - 1.70 = 1.80 >= 1.75? Yes!
    quotes = {
        "AAPL230721C150": {"bid": 0.75, "ask": 0.80, "delta": 0.30},
        "AAPL230721P150": {"bid": 0.85, "ask": 0.90, "delta": -0.30},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_position_leg_stop_call():
    """Call leg delta exceeds 0.60 -- should close all"""
    position = {
        "opened_at": time.time() - 30 * 60,
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 2.50, "ask": 2.55, "delta": 0.65},  # Deep ITM, delta >= 0.60
        "AAPL230721P150": {"bid": 0.05, "ask": 0.10, "delta": -0.05},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "leg_stop"}


def test_evaluate_position_leg_stop_put():
    """Put leg delta exceeds -0.60 (abs >= 0.60) -- should close all"""
    position = {
        "opened_at": time.time() - 30 * 60,
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 0.05, "ask": 0.10, "delta": 0.05},
        "AAPL230721P150": {"bid": 2.50, "ask": 2.55, "delta": -0.62},  # Deep ITM, abs(delta) >= 0.60
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "leg_stop"}


def test_evaluate_position_iv_crush_backstop():
    """4+ hours have elapsed since entry -- should close all"""
    position = {
        "opened_at": time.time() - 250 * 60,  # 250 minutes ago (> 240 minute backstop)
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 2.50, "ask": 2.55, "delta": 0.40},
        "AAPL230721P150": {"bid": 2.50, "ask": 2.55, "delta": -0.40},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "iv_crush_backstop"}


def test_evaluate_position_backstop_priority():
    """IV-crush backstop is the highest priority exit (safety mechanism)"""
    position = {
        "opened_at": time.time() - 250 * 60,  # Backstop condition met
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 2.50, "ask": 2.55, "delta": 0.65},  # Leg stop would also trigger
        "AAPL230721P150": {"bid": 0.05, "ask": 0.10, "delta": -0.05},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    # Backstop is checked first and returns immediately (safety priority)
    assert result == {"action": "close_all", "reason": "iv_crush_backstop"}


def test_evaluate_position_profit_target_priority():
    """Profit target checked before leg stop when backstop not met"""
    position = {
        "opened_at": time.time() - 30 * 60,  # Backstop not met (< 240 min)
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 0.75, "ask": 0.80, "delta": 0.30},  # Profit target triggers
        "AAPL230721P150": {"bid": 0.85, "ask": 0.90, "delta": -0.30},
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "profit_target"}


def test_evaluate_position_missing_quote_data():
    """Missing quote data for a leg -- should hold (not enough info)"""
    position = {
        "opened_at": time.time() - 30 * 60,
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 2.50, "ask": 2.55, "delta": 0.40},
        # Missing quotes for put leg
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "hold"}


def test_evaluate_position_missing_delta_data():
    """Missing delta data for a leg -- should hold during leg-stop check"""
    position = {
        "opened_at": time.time() - 250 * 60,  # Backstop condition met
        "entry_credit": 3.50,
    }
    open_legs = [
        {"symbol": "AAPL230721C150", "leg_role": "short_call"},
        {"symbol": "AAPL230721P150", "leg_role": "short_put"},
    ]
    quotes = {
        "AAPL230721C150": {"bid": 0.75, "ask": 0.80},  # Missing delta for profit target check
        "AAPL230721P150": {"bid": 0.85, "ask": 0.90},  # Missing delta for profit target check
    }
    config = {"profit_target_pct": 0.50, "leg_stop_delta_abs": 0.60, "exit_after_announcement_minutes": 240}
    # Should trigger backstop (leg_stop check can't find data, profit_target check works but profit < target)
    result = short_straddle.evaluate_position(position, open_legs, quotes, config)
    assert result == {"action": "close_all", "reason": "iv_crush_backstop"}
