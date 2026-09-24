# ---- before ANY package import: the home is a throwaway, for the whole session -------------------
# Several modules resolve paths ONCE, at import (`db_paper.DB_PATH`, `db.DB_PATH` -- the LIVE ledger --,
# the paper loop's lock and pid files). A function-scoped fixture runs too late to move those: they
# already point at the real home. Measured 2026-09-23: with a per-test fixture in place, a plain run
# still inserted 5 frozen-clock `loop_iterations` rows into the REAL earnings ledger every time. So the
# master override is set here, when pytest imports this conftest and before it imports a single
# test module -- which catches every import-time path, including ones nobody has listed.
import os as _os
import tempfile as _tempfile

import pytest

_os.environ["CHERRYPICK_HOME"] = _tempfile.mkdtemp(prefix="cherrypick-test-home-")
for _leaked in (
    "EARNINGS_DATA_DIR",
    "EARNINGS_LOGS_DIR",
    "EARNINGS_CONFIG",
    "MARKETDATA_DATA_DIR",
    "CHERRYPICK_MODULES_HOME",
):
    _os.environ.pop(_leaked, None)


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
    """Criteria dict that clears every hard filter and soft-criterion pass
    threshold in base_strategy_config -- an accepted baseline every test can
    mutate.
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


# --------------------------------------------------------------------------- the home is never the real one
@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Every test runs against a throwaway cherrypick home.

    Measured 2026-09-23 by running this suite with `CHERRYPICK_HOME` pointed at an empty directory
    and listing what appeared. Without this fixture a plain `pytest` here writes into the REAL home:
    meic touched its paper ledger, `state/meic.heartbeat` (which the watchdog reads -- a test run
    could make a dead loop look alive) and `state/stream_requests/meic.json` (which the streamer
    reads); earnings inserted `loop_iterations` rows into the real paper ledger at a frozen test
    clock, 221 of them by the time anyone looked. The streamer package's own tests stopped the
    production streamer the same way.

    The master override moves the whole tree; the narrow per-scope overrides are cleared because
    a leaked one wins over it.
    """
    home = tmp_path_factory.mktemp("cherrypick-home")
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    for leaked in (
        "EARNINGS_DATA_DIR",
        "EARNINGS_LOGS_DIR",
        "MARKETDATA_DATA_DIR",
        "CHERRYPICK_MODULES_HOME",
    ):
        monkeypatch.delenv(leaked, raising=False)
    return home
