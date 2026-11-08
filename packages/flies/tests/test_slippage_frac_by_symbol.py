"""flies' fill fraction goes through core's per-symbol lookup (`fees.slippage_frac`), under flies'
own key names (`slippage_frac`, `slippage_frac_by_symbol`)."""

import pytest
from cherrypick.core import fees

from cherrypick.flies import fly


@pytest.fixture
def suite_spx(monkeypatch):
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"SPX": 0.05})


def test_unset_map_keeps_the_flat_default():
    assert fly.slippage_frac({}, "SPX") == fly.DEFAULT_SLIPPAGE_FRAC == 0.125
    assert fly.slippage_frac({"slippage_frac": 0.2}, "SPX") == 0.2


def test_suite_entry_and_own_keys(suite_spx):
    assert fly.slippage_frac({}, "SPX") == 0.05
    assert fly.slippage_frac({}, "XSP") == 0.125
    assert fly.slippage_frac({"slippage_frac": 0.2}, "SPX") == 0.2
    assert fly.slippage_frac({"slippage_frac_by_symbol": {"XSP": 0.03}}, "XSP") == 0.03
