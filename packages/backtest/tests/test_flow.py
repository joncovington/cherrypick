"""The order rules (flow.py), on synthetic prints: each rule from the plan, and the cases it exists for.
docs/flow-signal-plan.md declares them; nothing here is fitted to data."""

from __future__ import annotations

from cherrypick.backtest import flow

EXP = "2026-11-20"


def P(ts_s, size, price, bid, ask, *, right="CALL", strike=100.0, kind="single", exp=EXP):
    return {"expiration": exp, "strike": strike, "right": right, "ts_ms": int(ts_s * 1000), "size": size,
            "price": price, "bid": bid, "ask": ask, "kind": kind}  # fmt: skip


def test_conditions_sort_into_sweeps_legs_cancels_and_plain_prints():
    k = flow.print_kind
    assert (k(18), k(125), k(129)) == ("single", "single", "single")  # auto, auction, floor
    assert (k(95), k(126), k(128)) == ("sweep", "sweep", "sweep")
    assert {k(c) for c in (35, 38, 130, 131, 134, 135, 138, 143)} == {"multi_leg"}
    assert {k(c) for c in (40, 44)} == {"cancelled"}


def test_the_side_is_read_from_the_fill_and_the_middle_is_unread():
    assert flow.side_of(flow.edge(2.10, 2.00, 2.10)) == "bought"  # at the ask
    assert flow.side_of(flow.edge(2.08, 2.00, 2.10)) == "bought"  # edge +0.6
    assert flow.side_of(flow.edge(2.06, 2.00, 2.10)) == "unread"  # edge +0.2
    assert flow.side_of(flow.edge(1.99, 2.00, 2.10)) == "sold"  # through the bid
    assert flow.edge(2.0, None, 2.1) is None and flow.edge(2.0, 2.2, 2.1) is None  # missing, crossed


def test_a_sweep_across_exchanges_at_one_instant_is_one_order():
    prints = [P(36000.0, 50, 2.10, 2.0, 2.1, kind="sweep") for _ in range(4)]
    (o,) = flow.orders(prints)
    assert (o["size"], o["prints"], o["side"], o["sweep"]) == (200, 4, "bought", True)
    assert abs(o["premium"] - 200 * 2.10 * 100) < 1e-6


def test_a_sliced_block_stays_one_order_until_a_gap_or_a_change_of_side():
    gap = flow.ORDER_GAP_S
    prints = [
        P(0.0, 100, 2.1, 2.0, 2.1),
        P(gap, 100, 2.1, 2.0, 2.1),  # within the gap of the one before: same order
        P(2 * gap + 0.5, 100, 2.1, 2.0, 2.1),  # past it: a new order
        P(2 * gap + 1.0, 100, 2.0, 2.0, 2.1),  # sold inside the gap: a new order all the same
    ]
    out = flow.orders(prints)
    assert [(o["side"], o["size"]) for o in out] == [("bought", 200), ("bought", 100), ("sold", 100)]


def test_multi_leg_legs_and_cancels_never_make_an_order():
    prints = [P(0, 500, 2.1, 2.0, 2.1, kind="multi_leg"), P(1, 500, 2.1, 2.0, 2.1, kind="cancelled")]
    assert flow.orders(prints) == []


def test_contracts_never_merge():
    prints = [
        P(0, 10, 2.1, 2.0, 2.1),
        P(0, 10, 2.1, 2.0, 2.1, right="PUT"),
        P(0, 10, 2.1, 2.0, 2.1, strike=105),
    ]
    assert len(flow.orders(prints)) == 3


def test_the_top_share_cut_is_read_off_the_pooled_histograms():
    small = [{"premium": 1_000.0}] * 990
    big = [{"premium": 400_000.0}] * 10
    cut = flow.top_share_cut([flow.histogram(small), flow.histogram(big)])
    # The top 1% of 1,000 orders is the ten large ones: the cut is the floor of their bucket.
    assert flow.bucket_floor(flow.bucket(400_000.0)) == cut and 350_000 < cut <= 400_000
    assert flow.top_share_cut([]) is None


def test_a_contract_count_alone_never_makes_an_order_large():
    """100 contracts of a nickel option is $500 of premium: below every cut."""
    (o,) = flow.orders([P(0, 100, 0.05, 0.04, 0.05)])
    assert o["premium"] < min(flow.CUTS)


def test_an_order_opened_when_open_interest_rose_by_half_of_it():
    assert flow.opened(1000, 5000, 5500) is True
    assert flow.opened(1000, 5000, 5499) is False
    assert flow.opened(1000, 5000, None) is None
