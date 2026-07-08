from strategies import jade_lizard


def test_apply_tiering_tier1_when_all_pass(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3}
    result = jade_lizard.apply_tiering(criteria, base_strategy_config)
    assert result["tier"] == "Tier 1"


def test_apply_tiering_rejects_when_naked_disabled(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3, "naked_strategies_allowed": False}
    result = jade_lizard.apply_tiering(criteria, base_strategy_config)
    assert "naked_strategy_disabled" in result["hard_fail_reasons"]


def test_apply_tiering_rejects_when_call_side_not_riskless(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3, "call_side_riskless": False}
    result = jade_lizard.apply_tiering(criteria, base_strategy_config)
    assert "call_side_not_riskless" in result["hard_fail_reasons"]


def test_apply_tiering_hard_fails_when_call_side_riskless_unverified(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3, "call_side_riskless": None}
    result = jade_lizard.apply_tiering(criteria, base_strategy_config)
    assert "call_side_riskless_unverified" in result["hard_fail_reasons"]


def test_wing_width_multiple_bands():
    config = {
        "wing_width_multiple_low": 2.5, "wing_width_multiple_mid": 3.0, "wing_width_multiple_high": 3.5,
        "wing_width_band_low_max": 1.25, "wing_width_band_mid_max": 1.75,
    }
    assert jade_lizard._wing_width_multiple(1.0, config) == 2.5
    assert jade_lizard._wing_width_multiple(2.0, config) == 3.5


def test_fetch_jade_lizard_order_blocked_when_naked_disabled():
    full_config = {"enable_live_trading": True, "allow_naked_strategies": False}
    result = jade_lizard.fetch_jade_lizard_order("AAPL", None, "After market close", full_config)
    assert result["ok"] is False


def test_label_order_legs_assigns_roles():
    order_result = {"order": {"legs": [
        {"symbol": "SP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "LC", "action": "Buy to Open", "quantity": 1},
    ]}}
    labeled = jade_lizard.label_order_legs(order_result)
    assert [leg["leg_role"] for leg in labeled] == ["short_put", "short_call", "long_call"]


def test_evaluate_position_close_put_on_delta_breach():
    open_legs = [{"leg_role": "short_put", "symbol": "SP"}]
    quotes = {"SP": {"delta": -0.50}}
    result = jade_lizard.evaluate_position({}, open_legs, quotes, config={"put_stop_delta_abs": 0.45})
    assert result == {"action": "close_put", "reason": "put_stop"}


def test_evaluate_position_hold_when_no_short_put_leg():
    result = jade_lizard.evaluate_position({}, open_legs=[], quotes={}, config={})
    assert result == {"action": "hold"}


def test_evaluate_position_hold_when_delta_within_bounds():
    open_legs = [{"leg_role": "short_put", "symbol": "SP"}]
    quotes = {"SP": {"delta": -0.20}}
    result = jade_lizard.evaluate_position({}, open_legs, quotes, config={"put_stop_delta_abs": 0.45})
    assert result == {"action": "hold"}


def test_compute_jade_lizard_legs_call_side_riskless_flag(monkeypatch):
    entries = [
        {"option_type": "P", "strike_price": "95", "mid": 1.0},
        {"option_type": "C", "strike_price": "105", "mid": 1.0},
        {"option_type": "C", "strike_price": "110", "mid": 0.3},
    ]
    monkeypatch.setattr(jade_lizard.scanner, "call_tt", lambda args: {"ok": True, "chain": {"2026-08-21": entries}})
    monkeypatch.setattr(jade_lizard.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": False})
    full_config = {"strategies": {"jade_lizard": {"wing_width_credit_multiple": 3.0}}}
    result = jade_lizard._compute_jade_lizard_legs("AAPL", "2026-08-21", 100.0, 5.0, full_config)
    assert result["ok"] is True
    assert result["call_side_riskless"] == (result["total_credit"] >= result["call_spread_width"])
