"""A wall needs a strike on its own side of zero (2026-10-09): the expired 0DTE chain after the bell
reads 0 everywhere but a spike at the close, and its "put wall" was the first row of a 0-against-0 tie."""

from cherrypick.core import gex


def row(strike, net):
    return {"strike": strike, "net_gex": net}


def test_an_expired_chain_with_no_negative_strike_has_no_put_wall():
    chain = [row(3000, 0), row(7805, 2455e6), row(7810, 41904e6), row(7815, 5604e6), row(9000, 0)]
    assert gex.net_walls(chain) == (7810, None)


def test_no_positive_strike_means_no_call_wall():
    assert gex.net_walls([row(7700, -5), row(7740, -10), row(7800, 0)]) == (None, 7740)


def test_a_two_sided_chain_is_unchanged():
    assert gex.net_walls([row(7730, -300), row(7765, -10), row(7800, 6000), row(7810, 5000)]) == (7800, 7730)


def test_empty_is_still_none():
    assert gex.net_walls([]) == (None, None)
