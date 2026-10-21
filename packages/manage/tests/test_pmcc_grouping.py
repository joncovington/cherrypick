"""Trade grouping and the PMCC link: a short call sold in an order of its own is joined to the long call
the PMCC view links it to, and nothing else moves. Synthetic names and histories, as in test_pmcc.py."""

from test_pmcc import DEEP, ENTRY, LONG, OLD_SHORT, SHORT, evaluate, history, legs, snapshot, view_of
from test_wheel import occ, opt_leg, tx

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules


def rules_of(t):
    return {f["rule"] for f in t["fired"]}


def test_the_chains_alone_split_a_short_sold_in_its_own_order():
    groups = ev.group_trades(legs(), "order_chain", history(), True)
    assert sorted(len(g) for g, _ in groups) == [1, 1]


def test_the_link_joins_them_into_one_diagonal_with_every_linked_order_as_its_basis():
    a = evaluate(snapshot(history(), legs()))
    (t,) = a["trades"]
    assert t["trade_id"] == "XYZ" and t["grouped_by"] == "pmcc link"
    assert t["shape"] == "diagonal" and t["basis"] == "order_chain"
    # The long, the May short sold with it, and the November short sold alone: the basis counts both
    # premiums, as the PMCC view's net cost does.
    assert t["cost"] == view_of(a)["width"]["net_cost"] == DEEP * 100 - 150.0 - 120.0
    assert len(t["chain"]["orders"]) == 2
    # No naked-call reading: the short is covered, so no unbounded-loss review.
    assert "risk.oversize" not in rules_of(t)
    assert view_of(a)["trade_ids"] == ["XYZ"] and view_of(a)["grouping"].startswith("joined")


def test_a_diagonal_entered_as_one_order_is_untouched():
    # TLT's shape: one order, a short further out than a PMCC's. One chain already, nothing joined.
    short = occ("2027-01-15", "C", 55)
    rows = [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", LONG, 1, DEEP),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", short, 1, 1.20),
    ]
    a = evaluate(snapshot(rows, legs(short=short)))
    (t,) = a["trades"]
    assert t["grouped_by"] is None and a["pmcc"]["positions"] == []


def test_a_separate_short_further_out_than_a_pmcc_s_stays_apart():
    short = occ("2027-01-15", "C", 55)
    rows = [*history()[:3], tx(2, "2026-09-25T15:00:00Z", "Sell to Open", short, 1, 1.20)]
    a = evaluate(snapshot(rows, legs(short=short)))
    assert len(a["trades"]) == 2 and all(t["grouped_by"] is None for t in a["trades"])


def test_an_unreconciled_link_is_never_joined():
    # The history starts after the long was bought: the view cannot replay it, so the chains stand.
    rows = [tx(2, "2026-09-25T15:00:00Z", "Sell to Open", SHORT, 1, 1.20)]
    a = evaluate(snapshot(rows, legs()), {"positions": {"XYZ": {"intent": "pmcc"}}})
    assert not view_of(a)["reconciled"]
    assert len(a["trades"]) == 2 and all(t["grouped_by"] is None for t in a["trades"])


def test_an_unrecognised_link_is_joined_too():
    # Coverage is a fact about the legs: a long entered near the money is a diagonal, not a PMCC, and
    # its separately sold short is still covered by it.
    near = occ("2028-01-21", "C", 50)
    a = evaluate(snapshot(history(long=near, long_price=6.0), legs(long=near, long_price=6.0, long_mid=6.5)))
    assert not view_of(a)["recognized"]
    (t,) = a["trades"]
    assert t["grouped_by"] == "pmcc link"


def test_a_short_grouped_with_other_legs_is_not_moved():
    # The November call was sold in one order with a put: their group is not the short alone.
    put = occ("2026-11-20", "P", 45)
    rows = [
        *history()[:3],
        tx(2, "2026-09-25T15:00:00Z", "Sell to Open", SHORT, 1, 1.20),
        tx(2, "2026-09-25T15:00:00Z", "Sell to Open", put, 1, 0.90),
    ]
    a = evaluate(snapshot(rows, [*legs(), opt_leg(put, -1, 0.70, 0.90)]))
    assert all(t["grouped_by"] is None for t in a["trades"]) and len(a["trades"]) == 2


def test_other_groupings_are_left_alone():
    for grouping in ("expiry_chain", "underlying"):
        a = evaluate(snapshot(history(), legs()), {"trade_grouping": grouping})
        assert all(t["grouped_by"] is None for t in a["trades"])


def test_a_join_that_fails_keeps_the_chains_grouping(monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("a history row layout never seen")

    monkeypatch.setattr(ev._chains, "from_orders", boom)
    a = evaluate(snapshot(history(), legs()))
    assert len(a["trades"]) == 2 and "PMCC grouping failed" in a["pmcc"]["grouping_error"]


def test_the_wheel_and_pmcc_views_name_the_regrouped_trades():
    """A wheel on one name and a split PMCC on another: after the join, each view names trades that
    exist and hold its legs."""
    put = occ("2026-11-20", "P", 40, root="ABC")
    wheel_rows = [tx(9, "2026-10-01T15:00:00Z", "Sell to Open", put, 1, 1.00)]
    snap = snapshot(history(), legs())
    acct = snap["positions"]["accounts"][0]
    acct["underlyings"].append(
        {"underlying": "ABC", "legs": [{**opt_leg(put, -1, 0.80, 1.00), "underlying": "ABC"}]}
    )
    snap["transactions"]["ABC"] = wheel_rows
    snap["metrics"]["ABC"] = {"last": 45.0}
    a = ev.evaluate(snap, _rules.effective(None)[0])["accounts"][0]
    ids = {t["trade_id"]: t for t in a["trades"]}
    (cycle,) = a["wheel"]["cycles"]
    assert cycle["trade_ids"] == ["ABC"] and put in {leg["symbol"] for leg in ids["ABC"]["legs"]}
    (v,) = a["pmcc"]["positions"]
    assert v["trade_ids"] == ["XYZ"]
    assert {LONG, SHORT} <= {leg["symbol"] for leg in ids["XYZ"]["legs"]}
    assert OLD_SHORT not in {leg["symbol"] for leg in ids["XYZ"]["legs"]}


# --------------------------------------------------------------------------- the join itself
def _linked_view():
    a = evaluate(snapshot(history(), legs()))
    return a["pmcc"]


def test_the_join_moves_only_groups_holding_the_long_or_the_short_alone():
    view = _linked_view()
    put = opt_leg(occ("2026-11-20", "P", 45), -1, 0.70, 0.90)
    long, short = legs()
    rules = _rules.effective(None)[0]
    # The short shares its group with a put: nothing moves, whatever the view links.
    groups = [([long], None), ([short, put], None)]
    assert ev._join_pmcc(groups, view, "XYZ", history(), rules) == (groups, set())
    # Each alone: joined, into the first one's place.
    out, joined = ev._join_pmcc([([long], None), ([short], None)], view, "XYZ", history(), rules)
    assert len(out) == 1 and joined == {0} and {leg["symbol"] for leg in out[0][0]} == {LONG, SHORT}


def test_the_join_is_the_order_chain_grouping_s_alone():
    view = _linked_view()
    long, short = legs()
    groups = [([long], None), ([short], None)]
    for grouping in ("expiry_chain", "underlying"):
        rules = _rules.effective({"trade_grouping": grouping})[0]
        assert ev._join_pmcc(groups, view, "XYZ", history(), rules) == (groups, set())
