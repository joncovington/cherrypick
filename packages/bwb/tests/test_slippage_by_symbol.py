"""bwb's cost stack prices slippage at core's per-symbol fraction (`fees.slippage_frac`)."""

import pytest
from cherrypick.core import fees

from cherrypick.bwb import engine

QUOTES = [{"bid": 1.0, "ask": 1.2}] * 4  # four legs 0.20 wide: 0.80 of spread


@pytest.fixture
def suite_spx(monkeypatch):
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"SPX": 0.05})


def test_unset_map_keeps_the_flat_default():
    assert engine.entry_cost("SPX", QUOTES, 1, {})["slippage"] == 10.0  # 0.80 x 0.125 x 100


def test_suite_entry_reaches_the_base_and_the_add_on(suite_spx):
    assert engine.entry_cost("SPX", QUOTES, 1, {})["slippage"] == 4.0
    assert engine.addon_entry_cost("SPX", QUOTES[:2], 2, {})["slippage"] == 4.0
    assert engine.entry_cost("XSP", QUOTES, 1, {})["slippage"] == 10.0


def test_config_flat_outranks_the_suite_entry(suite_spx):
    cfg = {"tastytrade_costs": {"slippage_frac_of_spread": 0.25}}
    assert engine.entry_cost("SPX", QUOTES, 1, cfg)["slippage"] == 20.0
