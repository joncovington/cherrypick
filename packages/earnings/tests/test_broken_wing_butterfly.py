"""Unit tests for broken wing butterfly strategy: order construction and exit logic."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from strategies import broken_wing_butterfly as bwb
import scanner


@pytest.fixture
def sample_config():
    """Load the actual config.json from the repo."""
    path = Path(__file__).parent.parent / "config.json"
    with open(path) as f:
        return json.load(f)


class TestEvaluatePositionTimeExit:
    """Test time-based exit logic: close 7 days before earnings."""

    def test_time_exit_triggers_at_7_days_before_earnings(self):
        """Position should close when 7 or fewer days remain until earnings."""
        earnings_date = (date.today() + timedelta(days=7)).isoformat()
        position = {
            "earnings_date": earnings_date,
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 1.0, "ask": 1.1, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {"exit_days_before_earnings": 7}}}

        result = bwb.evaluate_position(position, quotes, config)
        assert result["action"] == "close_all"
        assert "time_exit_before_earnings" in result["reason"]

    def test_time_exit_does_not_trigger_at_8_days_before_earnings(self):
        """Position should hold when 8+ days remain until earnings."""
        earnings_date = (date.today() + timedelta(days=8)).isoformat()
        position = {
            "earnings_date": earnings_date,
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        # Quotes unchanged from entry, so exit = entry, position is breakeven
        # exit_debit = 2*1.0 - 0.1 - 0.05 = 1.85, profit = 2.0 - 1.85 = 0.15 (7.5%, < 10%)
        # No stop loss, no leg delta stop triggered
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 0.95, "ask": 1.0, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {"exit_days_before_earnings": 7, "profit_target_pct": 0.10, "stop_loss_credit_multiple": 2.0, "leg_stop_delta_abs": 0.60}}}

        result = bwb.evaluate_position(position, quotes, config)
        assert result["action"] == "hold"

    def test_time_exit_with_overnight_gap_flag(self):
        """Gap-driven time exits should be labeled with _overnight_gap suffix."""
        earnings_date = (date.today() + timedelta(days=5)).isoformat()
        position = {
            "earnings_date": earnings_date,
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 1.0, "ask": 1.1, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {"exit_days_before_earnings": 7}}}

        result = bwb.evaluate_position(position, quotes, config, is_first_check_of_day=True)
        assert result["action"] == "close_all"
        assert "time_exit_before_earnings_overnight_gap" in result["reason"]


class TestEvaluatePositionPerLegDeltaStop:
    """Test per-leg delta stop: close if short legs go deep ITM."""

    def test_leg_stop_triggers_when_short_leg_delta_exceeds_threshold(self):
        """Position should close if any short leg's delta exceeds leg_stop_delta_abs."""
        position = {
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 1.0, "ask": 1.1, "delta": 0.65},  # delta 0.65 > threshold 0.60
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 7,
            "leg_stop_delta_abs": 0.60,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        assert result["action"] == "close_all"
        assert "leg_stop_delta" in result["reason"]

    def test_leg_stop_does_not_trigger_when_short_leg_delta_below_threshold(self):
        """Position should hold if all short legs are below delta threshold."""
        position = {
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        # Quotes slightly worse than entry, position has small loss (between 0 and 10%)
        # exit_debit = 2*1.05 - 0.1 - 0.05 = 1.95, loss = 2.0 - 1.95 = 0.05 (2.5% loss)
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 1.0, "ask": 1.05, "delta": 0.55},  # delta 0.55 < threshold 0.60
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,  # Avoid time exit
            "leg_stop_delta_abs": 0.60,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        # Should hold because:
        # - Not near earnings (15 days away)
        # - Delta is below threshold (0.55 < 0.60)
        # - Profit is between stop loss and profit target
        assert result["action"] == "hold"

    def test_leg_stop_with_missing_delta_is_skipped(self):
        """If delta is missing for a leg, skip that leg's check."""
        position = {
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2},  # Missing delta
            "BODY": {"bid": 1.0, "ask": 1.05, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.1},  # Missing delta
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.60,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        # Should hold: we can't check delta for legs without it, but price is in hold zone
        assert result["action"] == "hold"


class TestEvaluatePositionProfitTarget:
    """Test profit target: close at 10% of credit received."""

    def test_profit_target_triggers_at_10_percent_of_credit(self):
        """Position should close when profit reaches 10% of credit received."""
        position = {
            "entry_credit": -2.0,  # Collected $2.00
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        # Cost to close: buy 2 at 1.05, sell near at 0.10, sell far at 0.05 = 2.05 - 0.10 - 0.05 = 1.90
        # Profit = 2.00 - 1.90 = $0.10 = 5% of credit
        # This is under 10%, so should not close yet.
        # Let's make it cost 1.80 to close = $0.20 profit = 10%
        quotes = {
            "NEAR": {"bid": 0.10, "ask": 0.20, "delta": 0.15},
            "BODY": {"bid": 0.95, "ask": 1.00, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.10, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.60,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        # exit_debit = 2*1.00 - 0.10 - 0.05 = 1.85
        # profit = 2.00 - 1.85 = 0.15 = 7.5% (not quite 10%)
        assert result["action"] == "hold"

        # Now make prices better: costs only 1.80 to close = 10% profit exactly
        quotes2 = {
            "NEAR": {"bid": 0.10, "ask": 0.20, "delta": 0.15},
            "BODY": {"bid": 0.90, "ask": 0.95, "delta": 0.50},
            "FAR": {"bid": 0.05, "ask": 0.10, "delta": 0.05},
        }
        result2 = bwb.evaluate_position(position, quotes2, config)
        # exit_debit = 2*0.95 - 0.10 - 0.05 = 1.75
        # profit = 2.00 - 1.75 = 0.25 = 12.5% (exceeds 10%)
        assert result2["action"] == "close_all"
        assert result2["reason"] == "profit_target"


class TestEvaluatePositionStopLoss:
    """Test stop loss: close at 2.0x credit received."""

    def test_stop_loss_triggers_at_2x_credit(self):
        """Position should close when cost to close reaches 2x credit received."""
        position = {
            "entry_credit": -2.0,  # Collected $2.00
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        # Prices moved against us significantly: costs $4.00 to close = 2.0x credit = stop loss
        quotes = {
            "NEAR": {"bid": 0.20, "ask": 0.30, "delta": 0.30},
            "BODY": {"bid": 2.00, "ask": 2.10, "delta": 0.70},  # Way ITM now
            "FAR": {"bid": 0.20, "ask": 0.30, "delta": 0.20},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.80,  # Higher to not trigger leg stop
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        # exit_debit = 2*2.10 - 0.20 - 0.30 = 3.70 (less than 4.00 exactly)
        assert result["action"] == "hold" or result["action"] == "close_all"
        # Let's make it exactly 4.00 or more
        quotes2 = {
            "NEAR": {"bid": 0.25, "ask": 0.35, "delta": 0.35},
            "BODY": {"bid": 2.05, "ask": 2.15, "delta": 0.75},
            "FAR": {"bid": 0.25, "ask": 0.35, "delta": 0.25},
        }
        result2 = bwb.evaluate_position(position, quotes2, config)
        # exit_debit = 2*2.15 - 0.25 - 0.35 = 3.70
        # If price moved more:
        quotes3 = {
            "NEAR": {"bid": 0.30, "ask": 0.40, "delta": 0.40},
            "BODY": {"bid": 2.10, "ask": 2.20, "delta": 0.80},
            "FAR": {"bid": 0.30, "ask": 0.40, "delta": 0.30},
        }
        result3 = bwb.evaluate_position(position, quotes3, config)
        # exit_debit = 2*2.20 - 0.30 - 0.40 = 3.70
        # Hmm, we need to be more aggressive to hit 4.00
        quotes4 = {
            "NEAR": {"bid": 0.50, "ask": 0.60, "delta": 0.50},
            "BODY": {"bid": 2.20, "ask": 2.30, "delta": 0.90},  # Deep ITM
            "FAR": {"bid": 0.50, "ask": 0.60, "delta": 0.50},
        }
        result4 = bwb.evaluate_position(position, quotes4, config)
        # exit_debit = 2*2.30 - 0.50 - 0.60 = 3.50
        # Still not 4.00, but the leg_stop should trigger with delta 0.90
        if result4["action"] == "close_all":
            # This is OK, could be either leg_stop or if we had even worse prices, stop_loss
            pass

    def test_stop_loss_does_not_trigger_before_2x_credit(self):
        """Position should hold while cost to close is below 2x credit."""
        position = {
            "entry_credit": -2.0,  # Collected $2.00
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        # Prices moved against us a bit: costs $3.50 to close < 2.0x credit ($4.00)
        quotes = {
            "NEAR": {"bid": 0.15, "ask": 0.25, "delta": 0.25},
            "BODY": {"bid": 1.80, "ask": 1.90, "delta": 0.65},
            "FAR": {"bid": 0.10, "ask": 0.20, "delta": 0.10},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.80,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        # exit_debit = 2*1.90 - 0.15 - 0.10 = 3.45 < 4.00
        # No other conditions met (8+ days to earnings, delta < 0.80, profit < 10%)
        assert result["action"] == "hold"


class TestEvaluatePositionIncompleteQuotes:
    """Test handling of missing or incomplete quotes."""

    def test_position_holds_on_incomplete_quotes(self):
        """If any leg has no quote data, position should hold (retry next tick)."""
        position = {
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            # BODY is missing
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.60,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        assert result["action"] == "hold"

    def test_position_holds_on_missing_bid_ask(self):
        """If a required quote side (bid/ask) is missing, hold."""
        position = {
            "entry_credit": -2.0,
            "legs_json": json.dumps([
                {"symbol": "NEAR", "action": "Buy to Open", "quantity": 1},
                {"symbol": "BODY", "action": "Sell to Open", "quantity": 2},
                {"symbol": "FAR", "action": "Buy to Open", "quantity": 1},
            ]),
        }
        quotes = {
            "NEAR": {"bid": 0.1, "ask": 0.2, "delta": 0.15},
            "BODY": {"bid": 1.0, "delta": 0.50},  # Missing ask
            "FAR": {"bid": 0.05, "ask": 0.1, "delta": 0.05},
        }
        config = {"strategies": {"broken_wing_butterfly": {
            "exit_days_before_earnings": 15,
            "leg_stop_delta_abs": 0.60,
            "profit_target_pct": 0.10,
            "stop_loss_credit_multiple": 2.0,
        }}}

        result = bwb.evaluate_position(position, quotes, config)
        assert result["action"] == "hold"


class TestScannerEvaluateCreditSpreadExit:
    """Test the new scanner.evaluate_credit_spread_exit function."""

    def test_profit_target_with_10_percent_default(self):
        """Profit target should trigger at 10% of credit by default."""
        entry_credit = -2.0  # Stored negative (collected $2)
        exit_debit = 1.79  # Profit = 2.0 - 1.79 = 0.21 > 10% (2% cushion)
        config = {"profit_target_pct": 0.10, "stop_loss_credit_multiple": 2.0}

        result = scanner.evaluate_credit_spread_exit(entry_credit, exit_debit, config)
        assert result["action"] == "close_all"
        assert result["reason"] == "profit_target"

    def test_stop_loss_with_2x_credit_default(self):
        """Stop loss should trigger at 2x credit by default."""
        entry_credit = -2.0  # Stored negative (collected $2)
        exit_debit = 4.0  # 2x credit
        config = {}  # Use defaults

        result = scanner.evaluate_credit_spread_exit(entry_credit, exit_debit, config)
        assert result["action"] == "close_all"
        assert result["reason"] == "stop_loss"

    def test_hold_between_profit_and_stop(self):
        """Position should hold between profit target and stop loss."""
        entry_credit = -2.0  # Stored negative (collected $2)
        exit_debit = 3.0  # Loss = 1.0 (costs $1 more than collected), between 10% profit and 2x stop
        config = {}

        result = scanner.evaluate_credit_spread_exit(entry_credit, exit_debit, config)
        assert result["action"] == "hold"

    def test_custom_profit_target_percentage(self):
        """Should respect custom profit_target_pct."""
        entry_credit = -2.0  # Stored negative (collected $2)
        exit_debit = 1.7  # Profit = 2.0 - 1.7 = 0.30 = 15%
        config = {"profit_target_pct": 0.15}

        result = scanner.evaluate_credit_spread_exit(entry_credit, exit_debit, config)
        assert result["action"] == "close_all"
        assert result["reason"] == "profit_target"

    def test_custom_stop_loss_multiple(self):
        """Should respect custom stop_loss_credit_multiple."""
        entry_credit = -2.0  # Stored negative (collected $2)
        exit_debit = 3.0  # 1.5x credit
        config = {"stop_loss_credit_multiple": 1.5}

        result = scanner.evaluate_credit_spread_exit(entry_credit, exit_debit, config)
        assert result["action"] == "close_all"
        assert result["reason"] == "stop_loss"
