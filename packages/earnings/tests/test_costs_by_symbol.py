"""Earnings entries and exits price slippage at core's per-symbol fraction (`fees.slippage_frac`).

Entries carry the symbol on the order; exits build a legs-only order, so they pass it explicitly."""

import pytest
from cherrypick.core import fees

from cherrypick.earnings import costs

QUOTES = [{"bid": 1.00, "ask": 1.10}, {"bid": 0.50, "ask": 0.56}]  # 0.16 of spread
LEGS_ONLY = {"order": {"legs": [{}, {}]}}


@pytest.fixture
def suite_aapl(monkeypatch):
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"AAPL": 0.05})


def test_unset_map_keeps_the_flat_default():
    assert costs.exit_costs("mid", LEGS_ONLY, QUOTES, 1, {}, symbol="AAPL")["slippage"] == 2.0


def test_suite_entry_reaches_entry_and_mid_exit(suite_aapl):
    entry = costs.apply_entry_costs({**LEGS_ONLY, "symbol": "AAPL"}, QUOTES, 1, {})
    assert entry["slippage"] == 0.8
    assert costs.exit_costs("mid", LEGS_ONLY, QUOTES, 1, {}, symbol="AAPL")["slippage"] == 0.8
    assert costs.exit_costs("mid", LEGS_ONLY, QUOTES, 1, {}, symbol="MSFT")["slippage"] == 2.0


def test_natural_exit_still_charges_no_haircut(suite_aapl):
    out = costs.exit_costs("natural", LEGS_ONLY, QUOTES, 1, {}, symbol="AAPL")
    assert out["slippage"] == 0.0
