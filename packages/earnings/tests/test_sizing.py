import pytest

import sizing

BASE_CONFIG = {"available_capital_paper_mode": 100000, "risk_pct_multiplier": 1.0, "max_contracts_per_leg": 20}
STRATEGY_CONFIG = {"max_risk_per_trade_pct": 0.05}


def test_iron_fly_per_contract_max_loss():
    order = {
        "strategy": "iron_fly", "credit": 0.90,
        "short_strike": 150.0, "long_call_strike": 155.0, "long_put_strike": 145.0,
    }
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx((5.0 - 0.90) * 100)


def test_iron_condor_per_contract_max_loss():
    order = {
        "strategy": "iron_condor", "credit": 0.80,
        "short_call_strike": 155.0, "short_put_strike": 145.0,
        "long_call_strike": 160.0, "long_put_strike": 140.0,
    }
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx((5.0 - 0.80) * 100)


def test_directional_credit_spread_max_loss():
    order = {"strategy": "directional_credit_spread", "credit": 0.40, "short_strike": 150.0, "long_strike": 145.0}
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx((5.0 - 0.40) * 100)


def test_calendar_max_loss_is_debit():
    order = {"strategy": "atm_calendar", "debit": 3.12}
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx(3.12 * 100)

    order2 = {"strategy": "double_calendar", "debit": 0.45}
    assert sizing.per_contract_max_loss(order2, {}) == pytest.approx(0.45 * 100)


def test_reverse_fly_uses_max_loss_field():
    order = {"strategy": "reverse_fly", "max_loss": 1.75}
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx(1.75 * 100)


def test_broken_wing_butterfly_max_loss():
    order = {"strategy": "broken_wing_butterfly", "near_width": 2.0, "far_width": 5.0, "net_debit": 0.20}
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx((5.0 - 2.0 + 0.20) * 100)


def test_broken_wing_butterfly_net_credit_offsets_gap():
    order = {"strategy": "broken_wing_butterfly", "near_width": 2.0, "far_width": 5.0, "net_debit": -1.0}
    assert sizing.per_contract_max_loss(order, {}) == pytest.approx((5.0 - 2.0 - 1.0) * 100)


def test_unknown_strategy_returns_none():
    order = {"strategy": "not_a_real_strategy"}
    assert sizing.per_contract_max_loss(order, {}) is None


def test_missing_fields_returns_none():
    order = {"strategy": "iron_fly", "credit": 0.90}  # missing strikes
    assert sizing.per_contract_max_loss(order, {}) is None


def test_compute_position_size_matches_pep_example():
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, BASE_CONFIG)
    assert result["ok"] is True
    assert result["quantity"] == 16
    assert result["capital_at_risk"] == pytest.approx(4992.0)
    assert result["risk_budget"] == pytest.approx(5000.0)


def test_compute_position_size_respects_risk_pct_multiplier():
    config = {**BASE_CONFIG, "risk_pct_multiplier": 0.6}
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, config)
    assert result["risk_budget"] == pytest.approx(3000.0)
    assert result["quantity"] == 9  # floor(3000/312)


def test_compute_position_size_hits_hard_cap():
    config = {**BASE_CONFIG, "max_contracts_per_leg": 3}
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, config)
    assert result["quantity"] == 3


def test_compute_position_size_rejects_when_under_one_contract():
    config = {**BASE_CONFIG, "available_capital_paper_mode": 100}
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, config)
    assert result["ok"] is False
    assert result["reason"] == "risk_cap_exceeded"


def test_compute_position_size_missing_max_risk_pct():
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, {}, BASE_CONFIG)
    assert result["ok"] is False
    assert result["reason"] == "max_risk_per_trade_pct_missing"


def test_compute_position_size_unverified_max_loss():
    order = {"strategy": "atm_calendar"}  # no debit field
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, BASE_CONFIG)
    assert result["ok"] is False
    assert result["reason"] == "max_loss_unverified"


def test_live_mode_uses_injected_nlv_not_paper_capital():
    config = {
        "enable_live_trading": True, "_live_nlv": 50000, "risk_pct_multiplier": 1.0,
        "max_contracts_per_leg": 20, "available_capital_paper_mode": 999999,
    }
    order = {"strategy": "atm_calendar", "debit": 3.12}
    result = sizing.compute_position_size(order, STRATEGY_CONFIG, config)
    assert result["risk_budget"] == pytest.approx(50000 * 0.05)
