"""MEIC's paper fill fraction goes through core's per-symbol lookup (`fees.slippage_frac`)."""

import pytest
from cherrypick.core import fees

from cherrypick.meic import paper


@pytest.fixture
def suite_spx(monkeypatch):
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"SPX": 0.05})


def test_unset_map_keeps_the_flat_default():
    assert paper.fill_frac({}, "SPX") == paper.DEFAULT_SLIPPAGE_FRAC == 0.125
    assert paper.fill_frac({"slippage_frac_of_spread": 0.2}, "SPX") == 0.2


def test_suite_entry_reaches_an_arm_without_its_own_fraction(suite_spx):
    assert paper.fill_frac({}, "SPX") == 0.05
    assert paper.fill_frac({}, "QQQ") == 0.125


def test_arm_keys_outrank_the_suite(suite_spx):
    assert paper.fill_frac({"slippage_frac_of_spread": 0.2}, "SPX") == 0.2
    arm = {"slippage_frac_of_spread": 0.2, "slippage_frac_of_spread_by_symbol": {"NDX": 0.08}}
    assert paper.fill_frac(arm, "NDX") == 0.08
    assert paper.fill_frac(arm, "QQQ") == 0.2


def test_exit_slippage_uses_the_trades_symbol(suite_spx):
    q = {"bid": 1.0, "ask": 1.2, "mid": 1.1}
    trade = {
        "symbol": "SPX",
        "status": "open",
        "net_credit": 1.0,
        "put_symbol": "p",
        "call_symbol": "c",
        "long_put_symbol": "lp",
        "long_call_symbol": "lc",
        "put_strike": 100.0,
        "call_strike": 120.0,
        "wing_width": 5.0,
        "quantity": 1,
    }
    quotes = {k: dict(q) for k in ("p", "c", "lp", "lc")}
    out = paper.evaluate_open_trade(trade, quotes, {}, force_close=True, underlying_price=110.0)
    other = paper.evaluate_open_trade(
        {**trade, "symbol": "XSP"}, quotes, {}, force_close=True, underlying_price=110.0
    )
    assert out["action"] == other["action"] == "force_close"
    # Each side's two legs are 0.20 wide: 0.40 x frac x 100 -> 2.00 at 0.05, 5.00 at 0.125.
    assert out["put_exit_slippage"] == out["call_exit_slippage"] == pytest.approx(2.0)
    assert other["put_exit_slippage"] == other["call_exit_slippage"] == pytest.approx(5.0)
