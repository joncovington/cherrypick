"""scripts/fetch_time_spreads.py: its pure helpers (the broker reads themselves are not run here)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_time_spreads.py"


def load():
    spec = importlib.util.spec_from_file_location("fetch_time_spreads", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reach_is_a_volatility_multiple_with_a_floor():
    m = load()
    assert m.reach(100.0, None, 42) == 8.0  # no IV: the floor
    wide = m.reach(100.0, 0.60, 42)
    assert abs(wide - 100 * 1.6 * 0.60 * (42 / 365) ** 0.5) < 1e-9 and wide > 8.0


def test_metric_plain_reads_the_sdk_object_and_drops_expiries_without_an_iv():
    m = load()
    e = SimpleNamespace
    sdk = e(
        symbol="BRK/B",
        implied_volatility_index_rank="0.42",
        implied_volatility_30_day="31.5",
        historical_volatility_30_day="28.0",
        market_cap="900000000000",
        earnings=e(expected_report_date="2027-02-01"),
        option_expiration_implied_volatilities=[
            e(expiration_date="2026-11-13", implied_volatility="0.40"),
            e(expiration_date="2026-11-20", implied_volatility=None),
        ],
    )
    out = m.metric_plain(sdk, "Financials")
    assert out["symbol"] == "BRK.B" and out["iv_rank"] == 0.42 and out["market_cap"] == 9e11
    assert out["earnings_date"] == "2027-02-01" and out["sector"] == "Financials"
    assert out["expiries"] == [("2026-11-13", 0.40)]
