"""The covered-call join: a short call sold in an order of its own, which a long call on the name covers,
is one trade with that long whatever its days left, and never a naked call. Synthetic names and
histories, as in test_pmcc.py; the PMCC link's own join is test_pmcc_grouping.py's."""

from test_pmcc import DEEP, ENTRY, LONG, OLD_SHORT, evaluate, legs, snapshot
from test_wheel import occ, opt_leg, rd, share_leg, tx

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

FAR = occ("2027-01-15", "C", 55)  # 94 days out: past the PMCC view's 60


def split(short=FAR, short_qty=1, long_qty=1, long=LONG, short_price=1.20):
    """The long bought with a May short (expired), then `short` sold in an order of its own."""
    rows = [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", long, long_qty, DEEP),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", OLD_SHORT, 1, 1.50),
        rd("2026-05-15T22:00:00Z", "Expiration", "Buy to Close", OLD_SHORT, 1),
        tx(2, "2026-09-25T15:00:00Z", "Sell to Open", short, short_qty, short_price),
    ]
    cur = [opt_leg(long, long_qty, 14.0, DEEP), opt_leg(short, -short_qty, 1.00, short_price)]
    return snapshot(rows, cur)


def by(t):
    return {f["rule"] for f in t["fired"]}


def test_a_separate_short_past_sixty_days_is_one_diagonal_with_its_long():
    a = evaluate(split())
    (t,) = a["trades"]
    assert t["grouped_by"] == "covered call" and t["shape"] == "diagonal" and t["trade_id"] == "XYZ"
    # Both chains' orders: the long with the May short sold beside it, and the January short.
    assert t["cost"] == DEEP * 100 - 150.0 - 120.0 and len(t["chain"]["orders"]) == 2
    assert "risk.oversize" not in by(t) and a["pmcc"]["positions"] == [] and a["grouping_errors"] == []


def test_the_chains_alone_split_it():
    snap = split()
    u = snap["positions"]["accounts"][0]["underlyings"][0]
    groups = ev.group_trades(u["legs"], "order_chain", snap["transactions"]["XYZ"], True)
    assert sorted(len(g) for g, _ in groups) == [1, 1]


def test_a_long_at_the_short_s_expiry_and_below_its_strike_covers_it():
    long = occ("2027-01-15", "C", 40)
    (t,) = evaluate(split(long=long))["trades"]
    assert t["grouped_by"] == "covered call" and t["shape"] == "debit spread"


def test_a_long_more_than_covering_is_joined_and_one_covering_less_is_not():
    (t,) = evaluate(split(long_qty=2))["trades"]
    assert t["grouped_by"] == "covered call"
    a = evaluate(split(short_qty=2))
    assert len(a["trades"]) == 2 and {t["grouped_by"] for t in a["trades"]} == {None}


def test_an_uncovered_short_stays_apart():
    # A long above the short's strike, or expiring before it, covers nothing.
    for long in (occ("2028-01-21", "C", 60), occ("2026-12-18", "C", 40)):
        a = evaluate(split(long=long))
        assert len(a["trades"]) == 2 and {t["grouped_by"] for t in a["trades"]} == {None}


def test_a_lone_long_or_a_lone_short_stays_as_it_is():
    rows = [tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", LONG, 1, DEEP)]
    (t,) = evaluate(snapshot(rows, [opt_leg(LONG, 1, 14.0, DEEP)]))["trades"]
    assert t["grouped_by"] is None and t["shape"] == "long call"
    rows = [tx(2, "2026-09-25T15:00:00Z", "Sell to Open", FAR, 1, 1.20)]
    (t,) = evaluate(snapshot(rows, [opt_leg(FAR, -1, 1.00, 1.20)]))["trades"]
    assert t["grouped_by"] is None and t["shape"] == "naked call"


def test_two_shorts_that_could_pair_are_ambiguous_and_stay_apart():
    other = occ("2027-02-19", "C", 60)
    snap = split()
    snap["transactions"]["XYZ"].append(tx(3, "2026-09-26T15:00:00Z", "Sell to Open", other, 1, 0.80))
    snap["positions"]["accounts"][0]["underlyings"][0]["legs"].append(opt_leg(other, -1, 0.70, 0.80))
    a = evaluate(snap)
    assert len(a["trades"]) == 3 and {t["grouped_by"] for t in a["trades"]} == {None}


def test_shares_on_the_name_are_the_wheel_s_case():
    snap = split()
    snap["positions"]["accounts"][0]["underlyings"][0]["legs"].append(share_leg(100, 50.0, 48.0))
    snap["transactions"]["XYZ"].append(
        tx(5, "2026-09-01T15:00:00Z", "Buy to Open", "XYZ", 100, 48.0, kind="Equity")
    )
    a = evaluate(snap)
    assert {t["grouped_by"] for t in a["trades"]} == {None}


def test_a_long_whose_chain_does_not_reconcile_never_moves():
    # The history starts after the long was bought: it is priced from its open legs, and stays alone.
    rows = [tx(2, "2026-09-25T15:00:00Z", "Sell to Open", FAR, 1, 1.20)]
    a = evaluate(snapshot(rows, [opt_leg(LONG, 1, 14.0, DEEP), opt_leg(FAR, -1, 1.00, 1.20)]))
    assert len(a["trades"]) == 2 and {t["grouped_by"] for t in a["trades"]} == {None}
    # Declined by the rule, not saved by the containment.
    assert a["grouping_errors"] == []


def test_a_pmcc_shaped_short_is_still_the_pmcc_link_s():
    near = occ("2026-11-20", "C", 55)
    (t,) = evaluate(split(short=near))["trades"]
    assert t["grouped_by"] == "pmcc link"


def test_a_diagonal_entered_as_one_order_is_untouched():
    rows = [
        tx(1, f"{ENTRY}T15:00:00Z", "Buy to Open", LONG, 1, DEEP),
        tx(1, f"{ENTRY}T15:00:00Z", "Sell to Open", FAR, 1, 1.20),
    ]
    (t,) = evaluate(snapshot(rows, legs(short=FAR)))["trades"]
    assert t["grouped_by"] is None and t["shape"] == "diagonal"


def test_other_groupings_are_left_alone():
    snap = split()
    u = snap["positions"]["accounts"][0]["underlyings"][0]
    groups = ev.group_trades(u["legs"], "order_chain", snap["transactions"]["XYZ"], True)
    for grouping in ("expiry_chain", "underlying"):
        rules = _rules.effective({"trade_grouping": grouping})[0]
        assert ev._join_covered(groups, snap["transactions"]["XYZ"], rules) == groups


def test_a_join_that_fails_keeps_the_chains_grouping(monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("a history row layout never seen")

    monkeypatch.setattr(ev._chains, "from_orders", boom)
    a = evaluate(split())
    assert len(a["trades"]) == 2 and "covered-call grouping failed" in a["grouping_errors"][0]


def test_the_wheel_and_pmcc_views_still_name_the_right_trades():
    put = occ("2026-11-20", "P", 40, root="ABC")
    snap = split()
    snap["positions"]["accounts"][0]["underlyings"].append(
        {"underlying": "ABC", "legs": [{**opt_leg(put, -1, 0.80, 1.00), "underlying": "ABC"}]}
    )
    snap["transactions"]["ABC"] = [tx(9, "2026-10-01T15:00:00Z", "Sell to Open", put, 1, 1.00)]
    snap["metrics"]["ABC"] = {"last": 45.0}
    a = ev.evaluate(snap, _rules.effective(None)[0])["accounts"][0]
    ids = {t["trade_id"]: {leg["symbol"] for leg in t["legs"]} for t in a["trades"]}
    (cycle,) = a["wheel"]["cycles"]
    assert cycle["trade_ids"] == ["ABC"] and put in ids["ABC"]
    assert ids["XYZ"] == {LONG, FAR}
