import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import pytest  # noqa: E402  # intentional: after the sys.path bootstrap above


@pytest.fixture
def base_strategy_config():
    """Generic sub-config satisfying every strategy's apply_tiering. Individual
    tests override specific keys via {**base_strategy_config, "key": value}.
    """
    return {
        "min_price": 10.00,
        "max_front_expiration_days": 9,
        "require_weekly_options": True,
        "min_combined_open_interest": 2000,
        "max_atm_delta_abs": 0.57,
        "min_expected_move_dollars": 0.90,
        "min_expected_move_pct": 0.04,
        "min_term_structure": -0.004,
        "min_avg_volume": 1500000,
        "near_miss_min_avg_volume": 1000000,
        "min_iv_rv_ratio": 1.25,
        "near_miss_min_iv_rv_ratio": 1.00,
        "min_winrate": 0.50,
        "near_miss_min_winrate": 0.40,
        "max_bid_ask_spread_pct": 0.15,
        "min_market_cap": 2000000000,
        "near_miss_min_market_cap": 1000000000,
        "min_combined_option_volume": 500,
        "near_miss_min_combined_option_volume": 200,
        "min_skew_abs": 0.02,
        "skew_delta_target": 0.25,
        "back_month_min_days_after": 21,
        "max_realized_move_dispersion_pct": 0.15,
    }


@pytest.fixture
def good_criteria():
    """Criteria dict that clears every hard filter/near-miss band in
    base_strategy_config -- a Tier 1 baseline every test can mutate.
    """
    return {
        "price": 150.0,
        "term_structure": -0.05,
        "expected_move_dollars": 5.0,
        "expected_move_pct": 0.06,
        "atm_delta_abs": 0.50,
        "front_expiration_days": 3,
        "chain_complete": True,
        "avg_volume": 2000000,
        "iv_rv_ratio": 1.5,
        "winrate": 0.60,
        "bid_ask_spread_pct": 0.05,
        "has_weekly_options": True,
        "market_cap": 5000000000,
        "combined_open_interest": 3000,
        "combined_option_volume": 1000,
        "skew_abs": 0.05,
    }
