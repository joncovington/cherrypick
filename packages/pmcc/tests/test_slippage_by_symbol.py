"""pmcc's roll ticket prices slippage at core's per-symbol fraction (`fees.slippage_frac`)."""

import pytest
from cherrypick.core import fees

from cherrypick.pmcc import book

LEGS = [
    {"bid": 1.0, "ask": 1.2, "opening": False, "selling": False},
    {"bid": 2.0, "ask": 2.2, "opening": True, "selling": True},
]


@pytest.fixture
def suite_spy(monkeypatch):
    monkeypatch.setitem(fees.DEFAULT_COSTS, fees.BY_SYMBOL_KEY, {"SPY": 0.05})


def test_unset_map_keeps_the_flat_default():
    ticket, _ = book._ticket("SPY", LEGS, 1, {})
    assert ticket["slippage"] == 5.0  # 0.40 x 0.125 x 100


def test_suite_entry_reaches_the_ticket(suite_spy):
    assert book._ticket("SPY", LEGS, 1, {})[0]["slippage"] == 2.0
    assert book._ticket("QQQ", LEGS, 1, {})[0]["slippage"] == 5.0
