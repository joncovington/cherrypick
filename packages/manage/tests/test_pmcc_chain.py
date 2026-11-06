"""A PMCC's chain through rolls of its long: in one order (linked through the contracts it touches), or in
two that `trades.<LONG>.continues` confirms. The chain's money and the shorts sold against it run from
its first long; recognition stays the new long's own. Synthetic names, prices and dates."""

from test_pmcc import ENTRY, LONG, OLD_SHORT, SHORT, evaluate, legs, snapshot, view_of
from test_wheel import occ, rd, tx

NEW = occ("2028-06-16", "C", 40)
JULY = occ("2026-07-17", "C", 55)  # a short sold on its own against the first long, expired


def base_rows():
    """The first long with a May short; the May short expired; a July short sold on its own, expired."""
    return [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", LONG, 1, 12.0),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", OLD_SHORT, 1, 1.50),
        rd("2026-05-15T22:00:00Z", "Expiration", "Buy to Close", OLD_SHORT, 1),
        tx(5, "2026-06-01T15:00:00Z", "Sell to Open", JULY, 1, 1.00),
        rd("2026-07-17T22:00:00Z", "Expiration", "Buy to Close", JULY, 1),
    ]


def after_roll(rows):
    """The November short, sold after the roll, on its own."""
    return rows + [tx(2, "2026-09-25T15:00:00Z", "Sell to Open", SHORT, 1, 1.20)]


def view(rows, overrides=None):
    return view_of(evaluate(snapshot(rows, legs(long=NEW, long_price=16.0)), overrides))


def flat(sym):
    return " ".join(sym.split())


def test_a_one_order_roll_keeps_every_short_since_the_first_long():
    rows = after_roll(
        base_rows()
        + [
            tx(3, "2026-08-03T15:00:00Z", "Sell to Close", LONG, 1, 15.0),
            tx(3, "2026-08-03T15:00:00Z", "Buy to Open", NEW, 1, 16.0),
        ]
    )
    v = view(rows)
    assert v["reconciled"], v["problems"]
    assert v["chain_since"] == ENTRY and v["possible_roll"] is None and v["continued"] == []
    # The July short was sold on its own before the roll: it is the chain's, so its premium counts.
    assert flat(JULY) in {s["symbol"] for s in v["shorts"]}
    assert set(v["orders"]) == {1, 2, 3, 5}


def test_a_two_order_roll_is_reported_not_stitched():
    rows = after_roll(
        base_rows()
        + [
            tx(3, "2026-08-03T15:00:00Z", "Sell to Close", LONG, 1, 15.0),
            tx(4, "2026-08-05T15:00:00Z", "Buy to Open", NEW, 1, 16.0),
        ]
    )
    v = view(rows)
    assert v["possible_roll"] == {"symbol": flat(LONG), "leg": "2028-01-21 40C", "closed": "2026-08-03"}
    assert v["continued"] == [] and v["chain_since"] == "2026-08-05"
    assert 1 not in v["orders"] and 3 not in v["orders"]  # the old long's money is left out until confirmed
    assert flat(JULY) not in {s["symbol"] for s in v["shorts"]}


def test_a_confirmed_two_order_roll_carries_the_old_longs_money_and_shorts():
    rows = after_roll(
        base_rows()
        + [
            tx(3, "2026-08-03T15:00:00Z", "Sell to Close", LONG, 1, 15.0),
            tx(4, "2026-08-05T15:00:00Z", "Buy to Open", NEW, 1, 16.0),
        ]
    )
    unlinked = view(rows)
    v = view(rows, {"trades": {NEW: {"continues": LONG}}})
    assert v["reconciled"], v["problems"]
    assert v["continued"] == [flat(LONG)] and v["possible_roll"] is None and v["chain_since"] == ENTRY
    assert set(v["orders"]) == {1, 2, 3, 4, 5}
    assert flat(JULY) in {s["symbol"] for s in v["shorts"]}
    # The old long's buy and sale and the earlier premiums are now in the chain's cash.
    assert v["net_cash"] != unlinked["net_cash"]
    # Recognition stays the new long's own: its entry, not the first long's.
    assert v["long"]["opened"] == "2026-08-05"


def test_a_continues_with_no_matching_sale_is_a_problem_and_links_nothing():
    rows = after_roll(base_rows() + [tx(4, "2026-08-05T15:00:00Z", "Buy to Open", NEW, 1, 16.0)])
    v = view(rows, {"trades": {NEW: {"continues": LONG}}})
    assert v["continued"] == [] and not v["reconciled"]
    assert any(
        "continues: no 2028-01-21 40C sold to close within 5 days of 2026-08-05" in p for p in v["problems"]
    )


def test_a_sale_long_before_the_purchase_is_no_possible_roll():
    rows = after_roll(
        base_rows()
        + [
            tx(3, "2026-06-10T15:00:00Z", "Sell to Close", LONG, 1, 15.0),
            tx(4, "2026-08-05T15:00:00Z", "Buy to Open", NEW, 1, 16.0),
        ]
    )
    assert view(rows)["possible_roll"] is None
