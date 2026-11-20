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


def test_a_crossing_fills_at_the_limit_and_a_gap_at_the_market():
    # crossed since the last tick: the order was resting, so the limit
    assert rules.buy_fill(93.0, 92.10, prev_ask=93.40) == 93.0
    assert rules.sell_fill(96.72, 97.50, prev_bid=96.10) == 96.72
    # the session's first quote, or the market already through the level a tick ago: the market
    assert rules.buy_fill(93.0, 80.02, prev_ask=None) == 80.02
    assert rules.buy_fill(93.0, 80.02, prev_ask=81.00) == 80.02
    assert rules.sell_fill(96.72, 98.0, prev_bid=None) == 98.0
    assert rules.sell_fill(96.72, 98.0, prev_bid=97.0) == 98.0
    # a touch is the limit either way
    assert rules.buy_fill(93.0, 93.0, prev_ask=None) == 93.0


def test_session_steps_down_from_the_reference_close_only():
    assert rules.buy_level("session", ref_close=100.0, last_buy=80.0) == 93.0
    assert rules.buy_level("session", ref_close=100.0, last_buy=80.0, bought_today=1) == 86.49
    assert rules.buy_level("session", ref_close=100.0, last_buy=None, bought_today=2) == 80.43
    assert rules.buy_level("session", ref_close=None, last_buy=93.0) is None


def test_the_groups_posted_soxl_fills_are_the_session_rule():
    """11-13: previous close 65.53, buys posted at 56.65 and 52.69, sold at 58.92 and 54.80.
    11-19: previous close 49.26 with lots from earlier sessions open, buy 45.82, sold 47.66."""
    second = rules.buy_level("session", ref_close=65.53, last_buy=None, bought_today=1)
    third = rules.buy_level("session", ref_close=65.53, last_buy=None, bought_today=2)
    assert abs(second - 56.65) < 0.05
    assert abs(third - 52.69) < 0.05
    level = rules.buy_level("session", ref_close=49.26, last_buy=50.43, bought_today=0)
    assert abs(level - 45.82) < 0.05
    assert abs(rules.target("fixed", cost=level, shares=21, age=0) - 47.66) < 0.05
    # chain, anchored on an older lot, would have been nowhere near
    assert rules.buy_level("chain", ref_close=49.26, last_buy=50.43) == 46.89


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


def test_breakeven_covers_both_fills_fees_and_no_more():
    cost, shares = 50.0, 20
    be = rules.breakeven(cost, shares)
    buy = fees.stock_trade_fee(shares, cost, side="buy", ndigits=4)
    assert buy > 0  # the clearing fee: a buy is not free
    assert be * shares - fees.stock_trade_fee(shares, be, side="sell", ndigits=4) - buy >= cost * shares
    below = round(be - 0.01, 2)
    assert below * shares - fees.stock_trade_fee(shares, below, side="sell", ndigits=4) - buy < cost * shares


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
