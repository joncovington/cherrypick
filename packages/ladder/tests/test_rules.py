import pytest
from cherrypick.core import fees

from cherrypick.ladder import rules


def test_once_prices_from_the_reference_close_only():
    assert rules.buy_level("once", ref_close=100.0, last_buy=80.0) == 93.0


def test_chain_prices_from_the_last_lot_while_one_is_held():
    assert rules.buy_level("chain", ref_close=100.0, last_buy=None) == 93.0
    assert rules.buy_level("chain", ref_close=100.0, last_buy=93.0) == 86.49


def test_no_reference_is_no_level():
    assert rules.buy_level("once", ref_close=None, last_buy=None) is None
    assert rules.buy_level("chain", ref_close=0.0, last_buy=None) is None


def test_unknown_rules_raise():
    with pytest.raises(ValueError):
        rules.buy_level("steps", ref_close=100.0, last_buy=None)
    with pytest.raises(ValueError):
        rules.target("trailing", cost=10.0, shares=10, age=0)


def test_levels_round_down_and_targets_round_up():
    assert rules.buy_level("once", ref_close=65.53, last_buy=None) == 60.94  # 60.9429
    assert rules.target("fixed", cost=56.65, shares=21, age=0) == 58.92  # 58.916
    assert rules.target("fixed", cost=52.69, shares=20, age=0) == 54.80  # 54.7976


def test_the_groups_posted_soxl_fills_are_the_chain_rule():
    """11-13: previous close 65.53, buys posted at 56.65 and 52.69, sold at 58.92 and 54.80."""
    first = rules.buy_level("chain", ref_close=65.53, last_buy=None)
    second = rules.buy_level("chain", ref_close=65.53, last_buy=first)
    third = rules.buy_level("chain", ref_close=65.53, last_buy=second)
    assert abs(second - 56.65) < 0.05
    assert abs(third - 52.69) < 0.05


def test_decay_schedule():
    cost, shares = 50.0, 20
    assert rules.target("decay", cost=cost, shares=shares, age=30) == 52.0
    be = rules.target("decay", cost=cost, shares=shares, age=31)
    assert be == rules.breakeven(cost, shares)
    assert rules.target("decay", cost=cost, shares=shares, age=120) == be
    assert rules.target("decay", cost=cost, shares=shares, age=121) == 47.5
    assert [rules.target_stage("decay", a) for a in (0, 30, 31, 120, 121)] == [
        "gain",
        "gain",
        "breakeven",
        "breakeven",
        "floor",
    ]
    assert rules.target_stage("fixed", 500) == "gain"
    assert rules.target("fixed", cost=cost, shares=shares, age=500) == 52.0


def test_breakeven_covers_the_sale_fees_and_no_more():
    cost, shares = 50.0, 20
    be = rules.breakeven(cost, shares)
    assert be * shares - fees.stock_trade_fee(shares, be, side="sell") >= cost * shares
    below = round(be - 0.01, 2)
    assert below * shares - fees.stock_trade_fee(shares, below, side="sell") < cost * shares


def test_lot_shares_never_spend_more_than_the_lot():
    assert rules.lot_shares(1000, 60.94) == 16
    assert rules.lot_shares(1000, 1000.01) == 0
    assert rules.lot_shares(1000, 0) == 0


def test_money_adds_up():
    m = rules.money(16, 60.94, 63.38)
    assert m["entry_value"] == -975.04
    assert m["exit_value"] == 1014.08
    assert m["gross_pnl"] == round(m["entry_value"] + m["exit_value"], 2)
    assert m["net_pnl"] == round(m["gross_pnl"] - m["fees"] - m["settlement"] - m["slippage"], 2)
    assert m["fees"] > 0 and m["slippage"] == 0 and m["settlement"] == 0
