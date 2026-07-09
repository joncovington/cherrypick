import pytest

import costs


CONFIG = {"tastytrade_costs": {
    "commission_open_per_contract": 1.00,
    "commission_close_per_contract": 0.00,
    "commission_cap_per_leg": 10.00,
    "clearing_fee_per_contract": 0.10,
    "regulatory_fee_per_contract": 0.04,
    "slippage_frac_of_spread": 0.25,
}}

TWO_LEG_ORDER = {"order": {"legs": [{"symbol": "A"}, {"symbol": "B"}]}}
TWO_LEG_QUOTES = [{"bid": 3.00, "ask": 3.35}, {"bid": 1.00, "ask": 1.35}]


def test_entry_costs_match_tastytrade_schedule_worked_example():
    result = costs.apply_entry_costs(TWO_LEG_ORDER, TWO_LEG_QUOTES, quantity=5, config=CONFIG)
    assert result["commission"] == pytest.approx(10.00)
    assert result["pass_through_fees"] == pytest.approx(1.40)
    assert result["slippage"] == pytest.approx(87.5)
    assert result["total_cost"] == pytest.approx(98.9)


def test_exit_costs_have_zero_commission_by_default():
    result = costs.apply_exit_costs(TWO_LEG_ORDER, TWO_LEG_QUOTES, quantity=5, config=CONFIG)
    assert result["commission"] == pytest.approx(0.0)
    assert result["pass_through_fees"] == pytest.approx(1.40)
    assert result["slippage"] == pytest.approx(87.5)


def test_commission_cap_binds_per_leg():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.10}]
    assert costs.apply_entry_costs(order, quotes, quantity=5, config=CONFIG)["commission"] == pytest.approx(5.0)
    assert costs.apply_entry_costs(order, quotes, quantity=10, config=CONFIG)["commission"] == pytest.approx(10.0)
    assert costs.apply_entry_costs(order, quotes, quantity=15, config=CONFIG)["commission"] == pytest.approx(10.0)
    assert costs.apply_entry_costs(order, quotes, quantity=100, config=CONFIG)["commission"] == pytest.approx(10.0)


def test_cap_applies_per_leg_not_per_order():
    order = {"order": {"legs": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}, {"symbol": "D"}]}}
    quotes = [{"bid": 1.0, "ask": 1.05}] * 4
    result = costs.apply_entry_costs(order, quotes, quantity=15, config=CONFIG)
    assert result["commission"] == pytest.approx(4 * 10.0)


def test_defaults_used_when_config_missing_tastytrade_costs_block():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.20}]
    result = costs.apply_entry_costs(order, quotes, quantity=2, config={})
    assert result["commission"] == pytest.approx(2.0)
    assert result["pass_through_fees"] == pytest.approx(2 * (0.10 + 0.04))
    assert result["slippage"] == pytest.approx(0.20 * 0.25 * 100 * 2)


def test_zero_width_quote_produces_zero_slippage():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.00, "ask": 1.00}]
    result = costs.apply_entry_costs(order, quotes, quantity=1, config=CONFIG)
    assert result["slippage"] == pytest.approx(0.0)


def test_negative_spread_treated_as_zero():
    order = {"order": {"legs": [{"symbol": "A"}]}}
    quotes = [{"bid": 1.10, "ask": 1.00}]  # crossed/bad quote
    result = costs.apply_entry_costs(order, quotes, quantity=1, config=CONFIG)
    assert result["slippage"] == pytest.approx(0.0)
