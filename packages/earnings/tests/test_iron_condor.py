import json

from strategies import iron_condor


def test_apply_tiering_tier1_when_all_pass(base_strategy_config, good_criteria):
    result = iron_condor.apply_tiering(good_criteria, base_strategy_config)
    assert result["tier"] == "Tier 1"


def test_apply_tiering_rejects_expected_move_pct_below_minimum(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "expected_move_pct": 0.001}
    result = iron_condor.apply_tiering(criteria, base_strategy_config)
    assert "expected_move_pct_below_minimum" in result["hard_fail_reasons"]


def test_apply_tiering_has_no_atm_delta_check(base_strategy_config, good_criteria):
    # iron_condor doesn't check atm_delta_abs at all -- a missing value must not hard-fail.
    criteria = {**good_criteria}
    criteria.pop("atm_delta_abs", None)
    result = iron_condor.apply_tiering(criteria, base_strategy_config)
    assert "atm_delta_abs_unverified" not in result["hard_fail_reasons"]
    assert result["tier"] == "Tier 1"


def _legs_json():
    return json.dumps([
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "SP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "LC", "action": "Buy to Open", "quantity": 1},
        {"symbol": "LP", "action": "Buy to Open", "quantity": 1},
    ])


def test_evaluate_position_hold_on_incomplete_quotes():
    position = {"legs_json": _legs_json(), "entry_credit": 2.0}
    assert iron_condor.evaluate_position(position, quotes={}, config={}) == {"action": "hold"}


def test_evaluate_position_profit_target():
    position = {"legs_json": _legs_json(), "entry_credit": 4.0}
    quotes = {
        "SC": {"bid": 0.2, "ask": 0.3}, "SP": {"bid": 0.2, "ask": 0.3},
        "LC": {"bid": 0.05, "ask": 0.1}, "LP": {"bid": 0.05, "ask": 0.1},
    }
    result = iron_condor.evaluate_position(position, quotes, config={"profit_target_pct": 0.50})
    assert result == {"action": "close_all", "reason": "profit_target"}
