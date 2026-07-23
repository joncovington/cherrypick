import json

from strategies import iron_fly


def test_apply_tiering_accepts_when_all_pass(base_strategy_config, good_criteria):
    result = iron_fly.apply_tiering(good_criteria, base_strategy_config)
    assert result["accepted"] is True
    assert result["reject_reasons"] == []


def test_apply_tiering_rejects_missing_price(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "price": None}
    result = iron_fly.apply_tiering(criteria, base_strategy_config)
    assert result["accepted"] is False
    assert "price_unverified" in result["reject_reasons"]


def test_apply_tiering_rejects_atm_delta_above_max(base_strategy_config, good_criteria):
    criteria = {**good_criteria, "atm_delta_abs": 0.90}
    result = iron_fly.apply_tiering(criteria, base_strategy_config)
    assert "atm_delta_abs_above_maximum" in result["reject_reasons"]


def test_apply_tiering_rejects_soft_below_pass_band(base_strategy_config, good_criteria):
    # At the default "pass" level, a value between the near-miss and pass thresholds is a reject
    # (there is no Tier 2 middle band any more).
    criteria = {**good_criteria, "avg_volume": 1200000}
    result = iron_fly.apply_tiering(criteria, base_strategy_config)
    assert result["accepted"] is False
    assert "avg_volume_below_minimum" in result["reject_reasons"]


def test_soft_criterion_at_near_miss_level_accepts_between_bands(base_strategy_config, good_criteria):
    # With avg_volume screened at "near_miss", a value above the near-miss floor (1.0M) but below
    # the pass threshold (1.5M) is accepted -- the configurable-level replacement for the Tier 2 band.
    config = {**base_strategy_config, "_symbol_screen": {"avg_volume": "near_miss"}}
    criteria = {**good_criteria, "avg_volume": 1200000}
    result = iron_fly.apply_tiering(criteria, config)
    assert result["accepted"] is True


def test_soft_criterion_off_ignores_below_floor_value(base_strategy_config, good_criteria):
    # "off" drops the criterion entirely: even a far-below-floor value does not reject.
    config = {**base_strategy_config, "_symbol_screen": {"avg_volume": "off"}}
    criteria = {**good_criteria, "avg_volume": 100}
    result = iron_fly.apply_tiering(criteria, config)
    assert result["accepted"] is True


def test_wing_width_multiple_bands():
    config = {
        "wing_width_multiple_low": 2.5, "wing_width_multiple_mid": 3.0, "wing_width_multiple_high": 3.5,
        "wing_width_band_low_max": 1.25, "wing_width_band_mid_max": 1.75,
    }
    assert iron_fly._wing_width_multiple(1.0, config) == 2.5
    assert iron_fly._wing_width_multiple(1.5, config) == 3.0
    assert iron_fly._wing_width_multiple(2.0, config) == 3.5


def test_wing_width_multiple_falls_back_when_ratio_unknown():
    config = {"wing_width_credit_multiple": 3.0}
    assert iron_fly._wing_width_multiple(None, config) == 3.0


def _legs_json():
    return json.dumps([
        {"symbol": "SC", "action": "Sell to Open", "quantity": 1},
        {"symbol": "SP", "action": "Sell to Open", "quantity": 1},
        {"symbol": "LC", "action": "Buy to Open", "quantity": 1},
        {"symbol": "LP", "action": "Buy to Open", "quantity": 1},
    ])


def test_evaluate_position_holds_on_incomplete_quotes():
    position = {"legs_json": _legs_json(), "entry_credit": 2.0}
    result = iron_fly.evaluate_position(position, quotes={}, config={})
    assert result == {"action": "hold"}


def test_evaluate_position_profit_target():
    position = {"legs_json": _legs_json(), "entry_credit": 4.0}
    quotes = {
        "SC": {"bid": 0.2, "ask": 0.3}, "SP": {"bid": 0.2, "ask": 0.3},
        "LC": {"bid": 0.05, "ask": 0.1}, "LP": {"bid": 0.05, "ask": 0.1},
    }
    result = iron_fly.evaluate_position(position, quotes, config={"profit_target_pct": 0.50})
    assert result["action"] == "close_all"
    assert result["reason"] == "profit_target"


def test_evaluate_position_stop_loss():
    position = {"legs_json": _legs_json(), "entry_credit": 1.0}
    quotes = {
        "SC": {"bid": 1.0, "ask": 1.2}, "SP": {"bid": 1.0, "ask": 1.2},
        "LC": {"bid": 0.05, "ask": 0.1}, "LP": {"bid": 0.05, "ask": 0.1},
    }
    result = iron_fly.evaluate_position(position, quotes, config={"stop_loss_credit_multiple": 1.5})
    assert result["action"] == "close_all"
    assert result["reason"] == "stop_loss"
