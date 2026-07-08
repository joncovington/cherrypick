from strategies import short_strangle


def test_apply_tiering_tier1_when_all_pass(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3}
    result = short_strangle.apply_tiering(criteria, base_strategy_config)
    assert result["tier"] == "Tier 1"


def test_apply_tiering_rejects_when_naked_disabled(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "front_expiration_days": 3, "naked_strategies_allowed": False}
    result = short_strangle.apply_tiering(criteria, base_strategy_config)
    assert "naked_strategy_disabled" in result["hard_fail_reasons"]


def test_fetch_short_strangle_order_blocked_when_naked_disabled():
    full_config = {"enable_live_trading": True, "allow_naked_strategies": False}
    result = short_strangle.fetch_short_strangle_order("AAPL", None, "After market close", full_config)
    assert result["ok"] is False
    assert "naked strategies disabled" in result["error"]


def test_label_order_legs_assigns_roles():
    order_result = {"order": {"legs": [
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "SP", "action": "Sell to Open", "quantity": 1},
    ]}}
    labeled = short_strangle.label_order_legs(order_result)
    assert [leg["leg_role"] for leg in labeled] == ["short_call", "short_put"]


def test_evaluate_position_closes_all_on_either_leg_breach():
    open_legs = [{"leg_role": "short_call", "symbol": "SC"}, {"leg_role": "short_put", "symbol": "SP"}]
    quotes = {"SC": {"delta": 0.10}, "SP": {"delta": -0.50}}
    result = short_strangle.evaluate_position({}, open_legs, quotes, config={"leg_stop_delta_abs": 0.45})
    assert result == {"action": "close_all", "reason": "leg_stop"}


def test_evaluate_position_hold_when_both_legs_within_bounds():
    open_legs = [{"leg_role": "short_call", "symbol": "SC"}, {"leg_role": "short_put", "symbol": "SP"}]
    quotes = {"SC": {"delta": 0.10}, "SP": {"delta": -0.10}}
    result = short_strangle.evaluate_position({}, open_legs, quotes, config={"leg_stop_delta_abs": 0.45})
    assert result == {"action": "hold"}
