"""Reconciling Dolt's dividends with tastytrade's -- each rule from a case the vendor's bars exposed."""

from __future__ import annotations

import json

from cherrypick.technicals import dividends, paths, store
from cherrypick.technicals.adjust import Dividend, Split


def _d(*rows):
    return [Dividend(d, a) for d, a in rows]


def test_a_dividend_missing_from_dolt_is_filled_from_tastytrade():
    """CSCO's July 2024 dividend is absent from Dolt."""
    out, disputes = dividends.reconcile(
        _d(("2024-04-03", 0.40)), _d(("2024-04-03", 0.40), ("2024-07-05", 0.40))
    )
    assert [x.ex_date for x in out] == ["2024-04-03", "2024-07-05"] and disputes == []


def test_a_zero_in_dolt_does_not_hide_tastytrades_amount():
    """BAP's September 2024 dividend is 0.0 in Dolt and 2.9167 at tastytrade."""
    out, _ = dividends.reconcile(_d(("2024-09-23", 0.0)), _d(("2024-09-23", 2.9167)))
    assert out == [Dividend("2024-09-23", 2.9167)]


def test_the_same_dividend_a_day_apart_is_one_event_not_two():
    """AU's March 2024 dividend: the 13th in Dolt, the 14th at tastytrade."""
    out, _ = dividends.reconcile(_d(("2024-03-13", 0.19)), _d(("2024-03-14", 0.19)))
    assert out == [Dividend("2024-03-14", 0.19)]


def test_a_modest_disagreement_takes_tastytrades_amount():
    """CHT 2026: 1.653 in Dolt, 1.611 at tastytrade; the vendor's bars followed tastytrade."""
    out, disputes = dividends.reconcile(_d(("2026-07-09", 1.65252)), _d(("2026-07-09", 1.611233)))
    assert out == [Dividend("2026-07-09", 1.611233)] and disputes == []


def test_a_gross_outlier_on_either_side_loses_to_the_names_usual_payout():
    """BAP pays ~10 a year: Dolt's 0.939 (2024) and tastytrade's 50.00 (2026) are both errors."""
    dolt = _d(("2024-05-17", 0.939), ("2025-05-19", 10.84), ("2026-05-18", 14.478))
    tasty = _d(("2024-05-17", 9.2875), ("2025-05-19", 11.0111), ("2026-05-18", 50.0))
    out, disputes = dividends.reconcile(dolt, tasty)
    assert {x.ex_date: x.amount for x in out} == {
        "2024-05-17": 9.2875,
        "2025-05-19": 11.0111,
        "2026-05-18": 14.478,
    }
    assert [(x["ex_date"], x["chose"]) for x in disputes] == [
        ("2024-05-17", "tastytrade"),
        ("2026-05-18", "dolt"),
    ]


def test_a_split_adjusted_amount_dolt_confirms_is_restated_on_its_own_basis():
    """BKNG 2024: 0.35 at tastytrade, 8.75 in Dolt, the 2026 25-for-1 between them."""
    split = [Split("2026-04-06", 25.0, 1.0)]
    out, disputes = dividends.reconcile(_d(("2024-03-07", 8.75)), _d(("2024-03-07", 0.35)), split)
    assert [(x.ex_date, round(x.amount, 6)) for x in out] == [("2024-03-07", 8.75)] and disputes == []


def test_an_amount_dolt_does_not_confirm_keeps_its_basis():
    """A sector fund's pre-split distribution is on its own basis at tastytrade: nothing restates
    it, whether Dolt agrees with it as stated or has no entry at all."""
    split = [Split("2025-12-05", 2.0, 1.0)]
    same, _ = dividends.reconcile(_d(("2024-03-18", 0.34)), _d(("2024-03-18", 0.34)), split)
    alone, _ = dividends.reconcile([], _d(("2024-03-18", 0.34)), split)
    assert same == alone == [Dividend("2024-03-18", 0.34)]


def test_a_split_before_the_dividend_restates_nothing():
    split = [Split("2020-08-31", 4.0, 1.0)]
    out, _ = dividends.reconcile(_d(("2024-05-10", 0.25)), _d(("2024-05-10", 0.25)), split)
    assert out == [Dividend("2024-05-10", 0.25)]


def test_an_exact_repeat_inside_one_source_is_one_event():
    """Tastytrade lists EWZ's 2025-12-30 distribution twice."""
    tasty = _d(("2025-12-16", 1.026785), ("2025-12-30", 0.106184), ("2025-12-30", 0.106184))
    out, _ = dividends.reconcile(_d(("2025-12-16", 1.02678), ("2025-12-30", 0.10618)), tasty)
    assert [x.ex_date for x in out] == ["2025-12-16", "2025-12-30"]


def test_a_disputes_usual_payout_is_read_from_nearby_years():
    """BAP: decades of small payouts put its all-history median near 1.5, which picked Dolt's
    0.939 over tastytrade's 9.2875 in 2024; the payouts around it are ~10."""
    old = [(f"{y}-05-15", 1.5) for y in range(2000, 2020)]
    dolt = _d(*old, ("2024-05-17", 0.939), ("2025-05-19", 10.84))
    tasty = _d(*old, ("2024-05-17", 9.2875), ("2025-05-19", 11.0111))
    out, disputes = dividends.reconcile(dolt, tasty)
    assert {x.ex_date: x.amount for x in out}["2024-05-17"] == 9.2875
    assert [(x["ex_date"], x["chose"]) for x in disputes] == [("2024-05-17", "tastytrade")]


def test_yahoo_keeps_a_special_payout_the_usual_one_would_beat():
    """CME 2026-03-10: tastytrade's 7.45 is the annual variable dividend with the regular 1.30, and
    Yahoo states it; without Yahoo the usual 1.25-1.30 wins."""
    dolt = _d(("2025-12-12", 1.25), ("2026-03-10", 1.30), ("2026-06-09", 1.30))
    tasty = _d(("2025-12-12", 1.25), ("2026-03-10", 7.45), ("2026-06-09", 1.30))
    out, disputes = dividends.reconcile(dolt, tasty, [], _d(("2026-03-10", 7.45)))
    assert {x.ex_date: x.amount for x in out}["2026-03-10"] == 7.45
    assert [(x["chose"], x.get("by")) for x in disputes] == [("tastytrade", "yahoo")]
    alone, _ = dividends.reconcile(dolt, tasty)
    assert {x.ex_date: x.amount for x in alone}["2026-03-10"] == 1.30


def test_yahoo_decides_a_disagreement_inside_the_outlier_band():
    """HON: tastytrade states twice Dolt's 1.19; Yahoo's 1.19 decides, where rule 3 kept 2.38."""
    out, disputes = dividends.reconcile(
        _d(("2025-11-14", 1.19)), _d(("2025-11-14", 2.38)), [], _d(("2025-11-14", 1.19))
    )
    assert out == [Dividend("2025-11-14", 1.19)] and disputes[0]["chose"] == "dolt"


def test_yahoo_does_not_touch_a_modest_disagreement():
    """CHT: Yahoo's 1.653 is Dolt's, but a gap under THIRD_RATIO is a currency conversion and stays
    tastytrade's, as the vendor's bars had it."""
    out, disputes = dividends.reconcile(
        _d(("2026-07-09", 1.65252)), _d(("2026-07-09", 1.611233)), [], _d(("2026-07-09", 1.653))
    )
    assert out == [Dividend("2026-07-09", 1.611233)] and disputes == []


def test_yahoo_matching_neither_side_leaves_the_old_rules():
    dolt = _d(("2024-05-17", 0.939), ("2025-05-19", 10.84))
    tasty = _d(("2024-05-17", 9.2875), ("2025-05-19", 11.0111))
    with_yahoo, _ = dividends.reconcile(dolt, tasty, [], _d(("2024-05-17", 5.0)))
    assert with_yahoo == dividends.reconcile(dolt, tasty)[0]


def test_one_payout_listed_in_parts_is_one_event():
    """GFI 2026-03-13: tastytrade's 0.28211 and 1.159787 are Dolt's 1.44189, paid once."""
    tasty = _d(("2026-03-13", 0.28211), ("2026-03-13", 1.159787))
    out, _ = dividends.reconcile(_d(("2026-03-13", 1.44189)), tasty, [], _d(("2026-03-13", 1.442)))
    assert [(x.ex_date, round(x.amount, 5)) for x in out] == [("2026-03-13", 1.44190)]


def test_parts_that_do_not_sum_to_the_other_source_stay_apart():
    """AMKR 2024-12-04: one of tastytrade's two entries is Dolt's whole amount."""
    tasty = _d(("2024-12-04", 0.08269), ("2024-12-04", 0.40546))
    assert dividends.merge_parts(tasty, _d(("2024-12-04", 0.08269))) == tasty


def test_the_store_breaks_ties_with_yahoo_on_its_own_dates_basis():
    """Yahoo states HON's 1.19 as 1.248, restated through the 2026-06-29 spin-off it lists as a
    1907:2000 split; the store puts it back on its own date's basis before comparing."""
    from cherrypick.technicals import adjust_check

    conn = store.connect()
    store.upsert_dividends(conn, [("HON", "2025-11-14", 1.19)])
    path = paths.tastytrade_dividends()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"symbols": {"HON": {"fetched_at": "x", "dividends": [["2025-11-14", 2.38]]}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    y = adjust_check.connect_yahoo()
    y.execute(
        "INSERT INTO series (symbol, fetched_at, fetched_on, dividends, splits) VALUES (?, ?, ?, ?, ?)",
        (
            "HON",
            "x",
            "2026-10-27",
            json.dumps([["2025-11-14", 1.248034]]),
            json.dumps([["2026-06-29", 1907, 2000]]),
        ),
    )
    y.commit()
    y.close()
    assert [round(x.amount, 4) for x in store.yahoo_dividends("HON")] == [1.19]
    assert store.dividends(conn, "HON")[0] == [Dividend("2025-11-14", 1.19)]


def test_an_excluded_name_gets_no_yahoo_tiebreak(monkeypatch):
    monkeypatch.setattr(store, "yahoo_dividends", lambda s: _d(("2026-04-24", 0.124)))
    conn = store.connect()
    store.upsert_dividends(conn, [("PBR", "2026-04-24", 0.12409)])
    path = paths.tastytrade_dividends()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"symbols": {"PBR": {"fetched_at": "x", "dividends": [["2026-04-24", 0.26031]]}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert "PBR" in dividends.THIRD_EXCLUDED
    assert store.dividends(conn, "PBR")[0] == [Dividend("2026-04-24", 0.26031)]


def test_the_store_restates_by_the_symbols_own_splits():
    conn = store.connect()
    store.upsert_bars(
        conn,
        [
            ("AAA", "2024-03-06", 100.0, 100.0, 100.0, 100.0, 1.0),
            ("AAA", "2024-03-07", 98.0, 98.0, 98.0, 98.0, 1.0),
        ],
    )
    store.upsert_splits(conn, [("AAA", "2026-04-06", 25.0, 1.0)])
    store.upsert_dividends(conn, [("AAA", "2024-03-07", 8.75)])
    path = paths.tastytrade_dividends()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"symbols": {"AAA": {"fetched_at": "x", "dividends": [["2024-03-07", 0.35]]}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert [round(x.amount, 6) for x in store.dividends(conn, "AAA")[0]] == [8.75]


def test_without_a_tastytrade_file_dolt_alone_decides():
    conn = store.connect()
    store.upsert_dividends(conn, [("AAA", "2024-04-03", 0.4)])
    assert store.dividends(conn, "AAA")[0] == [Dividend("2024-04-03", 0.4)]


def test_the_store_reads_the_fetched_file():
    conn = store.connect()
    store.upsert_dividends(conn, [("AAA", "2024-04-03", 0.4)])
    path = paths.tastytrade_dividends()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"symbols": {"AAA": {"fetched_at": "x", "dividends": [["2024-04-03", 0.4], ["2024-07-05", 0.4]]}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert [x.ex_date for x in store.dividends(conn, "AAA")[0]] == ["2024-04-03", "2024-07-05"]
