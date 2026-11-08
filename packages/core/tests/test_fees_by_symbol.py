"""Per-symbol slippage fractions (`fees.slippage_frac`).

The lookup runs most specific first: the caller's per-symbol entry, the caller's flat fraction, the
suite's per-symbol entry, the suite's flat fraction. The suite's map ships empty, so until a declared
boundary fills it every module prices exactly as before; the first test pins that.
"""

import pytest

from cherrypick.core import fees

ORDER_2LEG = {"order": {"legs": [{}, {}]}}
LEG_QUOTES = [{"bid": 1.00, "ask": 1.10}, {"bid": 0.50, "ask": 0.56}]  # spreads 0.10 + 0.06


@pytest.fixture
def suite_spx(monkeypatch):
    """The suite's map with one entry, as a declared boundary would set it."""
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"SPX": 0.05})


def test_suite_map_ships_empty():
    # An entry here moves every paper module's net on that symbol: it lands only at a declared
    # boundary, with its own PR, which updates this test on purpose.
    assert fees.DEFAULT_COSTS[fees.BY_SYMBOL_KEY] == {}
    assert fees.DEFAULT_COSTS["slippage_frac_of_spread"] == 0.125


def test_empty_map_leaves_every_symbol_on_the_flat_fraction():
    for sym in ("SPX", "XSP", "QQQ", "AAPL", None, ""):
        assert fees.slippage_frac(sym) == 0.125
        assert fees.slippage_frac(sym, {"slippage_frac_of_spread": 0.25}) == 0.25


def test_lookup_order(suite_spx):
    own = {fees.BY_SYMBOL_KEY: {"SPX": 0.02}, "slippage_frac_of_spread": 0.25}
    assert fees.slippage_frac("SPX", own) == 0.02  # 1. caller's per-symbol
    assert fees.slippage_frac("XSP", own) == 0.25  # 2. caller's flat
    assert fees.slippage_frac("SPX", {}) == 0.05  # 3. suite's per-symbol
    assert fees.slippage_frac("XSP", {}) == 0.125  # 4. suite's flat


def test_caller_flat_outranks_suite_symbol(suite_spx):
    # A stress run or a declared fill pins its fraction whatever the suite learns about one symbol.
    assert fees.slippage_frac("SPX", {"slippage_frac_of_spread": 0.25}) == 0.25


def test_symbols_match_case_insensitively(suite_spx):
    assert fees.slippage_frac("spx") == 0.05
    assert fees.slippage_frac("SPX", {fees.BY_SYMBOL_KEY: {"spx": 0.01}}) == 0.01


def test_null_entries_fall_through(suite_spx):
    own = {fees.BY_SYMBOL_KEY: {"SPX": None}, "slippage_frac_of_spread": None}
    assert fees.slippage_frac("SPX", own) == 0.05
    assert fees.slippage_frac(None, None) == 0.125


def test_apply_costs_take_the_symbol(suite_spx):
    # 0.16 of spread x 100 x 3 contracts: 0.05 -> 2.40, 0.125 -> 6.00.
    assert fees.apply_entry_costs({**ORDER_2LEG, "symbol": "SPX"}, LEG_QUOTES, 3, {})["slippage"] == 2.40
    assert fees.apply_exit_costs(ORDER_2LEG, LEG_QUOTES, 3, {}, symbol="SPX")["slippage"] == 2.40
    assert fees.apply_entry_costs(ORDER_2LEG, LEG_QUOTES, 3, {})["slippage"] == 6.00


def test_explicit_symbol_outranks_the_orders(suite_spx):
    order = {**ORDER_2LEG, "symbol": "SPX"}
    assert fees.apply_entry_costs(order, LEG_QUOTES, 3, {}, symbol="XSP")["slippage"] == 6.00


def test_config_map_reaches_the_cost_stack():
    cfg = {"tastytrade_costs": {fees.BY_SYMBOL_KEY: {"SPX": 0.05}}}
    assert fees.apply_entry_costs({**ORDER_2LEG, "symbol": "SPX"}, LEG_QUOTES, 3, cfg)["slippage"] == 2.40
    assert fees.slippage_dollars(LEG_QUOTES, 3, cfg, symbol="SPX") == 2.40
    assert fees.slippage_dollars(LEG_QUOTES, 3, cfg) == 6.00


def test_spread_costs_pass_their_symbol(suite_spx):
    assert fees.spread_entry_cost("SPX", LEG_QUOTES, 3, {})["slippage"] == 2.40
    assert fees.spread_close_cost("SPX", LEG_QUOTES, 3, {}, sell_legs=1)["slippage"] == 2.40
    assert fees.spread_entry_cost("SPY", LEG_QUOTES, 3, {})["slippage"] == 6.00
