"""The study's confirmed rules on the charts and the watchlist: walked with the study's own code,
read off the same dollar volume, and listed apart from the setups; and the options-tradable label."""

from __future__ import annotations

import json
import statistics
import sys
from types import SimpleNamespace

from cherrypick.technicals import chart, paths, setups, store, tradable, universe, watchlist

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "tests")
from test_setups import _land, _readings, _walk  # noqa: E402


def _blocked_signal(r):
    """A reversion position during which the setup signalled again, and that first blocked signal."""
    enter = setups.RULES["reversion"][0]
    for t in setups.run("reversion", r):
        inside = [j for j in range(t.entry + 1, t.exit or len(r.closes)) if enter(r, j)]
        if inside:
            return t, inside[0]
    raise AssertionError("the fixture should have a blocked signal")


def _bars(n):
    return [SimpleNamespace(date=f"d{i:04d}") for i in range(n)]


def test_a_tested_rule_is_re_walked_so_a_filtered_entry_never_blocks_a_qualifying_one():
    """The blocking position's entry is under $300M and the blocked signal is over it. The study's
    walk takes the blocked signal; filtering the finished setup walk would have lost it."""
    r = _readings()
    t, blocked = _blocked_signal(r)
    n = len(r.closes)
    basis = [4e8] * n
    basis[t.entry] = 1e8
    [rule] = chart.tested_trades("ABC", _bars(n), r, 0, [True] * n, basis, {})
    assert (rule["id"], rule["setup"], rule["family"], rule["side"]) == (
        "mr-300m",
        "reversion",
        "reversion",
        "long",
    )
    entries = {x["entry_date"] for x in rule["trades"]}
    assert f"d{t.entry:04d}" not in entries and f"d{blocked:04d}" in entries
    naive = {x.entry for x in setups.run("reversion", r) if basis[x.entry] >= universe.VIEW_DOLLAR_VOLUME}
    assert blocked not in naive, "the fixture must tell the two walks apart"
    assert all(x["dollar_volume"] >= universe.VIEW_DOLLAR_VOLUME for x in rule["trades"])


def test_an_entry_outside_the_studys_universe_is_dropped_but_still_held():
    """The study walked every qualifying signal and then counted only entries in its universe (a
    close of $5 or more, say): an uncounted entry still held the position, so it still blocks."""
    r = _readings()
    t, blocked = _blocked_signal(r)
    n = len(r.closes)
    member = [True] * n
    member[t.entry] = False
    [rule] = chart.tested_trades("ABC", _bars(n), r, 0, member, [4e8] * n, {})
    entries = {x["entry_date"] for x in rule["trades"]}
    assert f"d{t.entry:04d}" not in entries and f"d{blocked:04d}" not in entries


def test_the_chart_reads_the_rule_off_the_raw_dollar_volume():
    """A name trading ~$500M a day carries the rule's trades, each with its dollar volume; the same
    name at ~$5M a day carries none, though the setup itself still trades."""
    highs, lows, closes, vols = _walk()
    conn = store.connect()
    _land(conn, "BIG", closes, highs, lows, [v * 5 for v in vols])
    _land(conn, "SMALL", closes, highs, lows, [v / 20 for v in vols])
    big, small = chart.build(conn, "BIG"), chart.build(conn, "SMALL")
    last50 = [c * v * 5 for c, v in zip(closes[-50:], vols[-50:], strict=True)]
    assert big["dollar_volume_50d"] == round(statistics.median(last50))  # today's, for the label
    [rule] = big["tested"]
    assert rule["trades"], "the fixture should trade the rule, or the test proves nothing"
    assert all(x["dollar_volume"] >= universe.VIEW_DOLLAR_VOLUME for x in rule["trades"])
    reversion = next(s for s in small["setups"] if s["id"] == "reversion")
    assert reversion["trades"] and small["tested"][0]["trades"] == []
    assert all(x["dollar_volume"] < universe.VIEW_DOLLAR_VOLUME for x in reversion["trades"])


def _doc(tested_trades, dollar_volume_50d=2.5e8):
    dates = [f"2026-09-{d:02d}" for d in range(1, 29)]
    trade = {
        "entry_date": dates[-3],
        "entry_price": 100.0,
        "exit_date": None,
        "exit_price": None,
        "reason": None,
        "target": None,
        "dollar_volume": 4.2e8,
    }
    return {
        "symbol": "ABC",
        "session": dates[-1],
        "bars": {"date": dates, "close": [100.0] * len(dates)},
        "trend_short": [-2] * len(dates),
        "trend_long": [1] * len(dates),
        "rank": 4,
        "dollar_volume_50d": dollar_volume_50d,
        "setups": [
            {
                "id": "reversion",
                "name": "Mean reversion",
                "family": "reversion",
                "side": "long",
                "trades": [trade],
            }
        ],
        "tested": [
            {
                "id": "mr-300m",
                "setup": "reversion",
                "name": "Mean reversion",
                "family": "reversion",
                "side": "long",
                "change": "median dollar volume >= $300M",
                "trades": [trade] if tested_trades else [],
            }
        ],
    }


def test_watchlist_rows_say_which_are_a_tested_rules():
    rows = watchlist.rows(_doc(True), {}, {"ABC"})
    assert [(r["setup"], r["tested"]) for r in rows] == [("reversion", None), ("reversion", "mr-300m")]
    assert all(r["family"] == "reversion" and r["side"] == "long" for r in rows)
    assert all(r["dollar_volume"] == 4.2e8 and r["options_tradable"] is True for r in rows)
    assert [r["tested"] for r in watchlist.rows(_doc(False), {}, set())] == [None]


def test_options_tradable_needs_weeklies_and_100m_a_day_and_is_null_without_a_label():
    assert watchlist.rows(_doc(False), {})[0]["options_tradable"] is None
    assert watchlist.rows(_doc(False), {}, {"XYZ"})[0]["options_tradable"] is False  # no weeklies
    assert watchlist.rows(_doc(False, 9.9e7), {}, {"ABC"})[0]["options_tradable"] is False
    assert watchlist.rows(_doc(False, 1e8), {}, {"ABC"})[0]["options_tradable"] is True
    assert watchlist.rows(_doc(False, None), {}, {"ABC"})[0]["options_tradable"] is False


def test_a_cash_index_is_judged_by_its_weeklies_alone():
    """SPX has no stock volume of its own; its options are the most liquid there are."""
    assert tradable.tradable("SPX", {"SPX"}, None) is True
    assert tradable.tradable("SPX", set(), None) is False


def _label(monkeypatch, tmp_path, days):
    f = tmp_path / "tastytrade.json"
    monkeypatch.setattr(paths, "tastytrade_iv_rank", lambda: f)
    if days is not None:
        f.write_text(json.dumps({"days": days}), encoding="utf-8")


DAYS = {
    "2026-10-02": {
        "RATED": {"expiries_35d": 4, "liquidity_rating": 3},
        # Weeklies, but tastytrade rates it 2 -- as it rates HD, LOW and APP: in the label, out of
        # the study's declared view.
        "PRICEY": {"expiries_35d": 5, "liquidity_rating": 2},
        "MONTHLY": {"expiries_35d": 2, "liquidity_rating": 4},
        "NOLABEL": {"liquidity_rating": 4},
    },
    "2026-10-03": {"LATE": {"expiries_35d": 9, "liquidity_rating": 4}},
}


def test_weeklies_are_read_from_the_day_that_labels_most_names(monkeypatch, tmp_path):
    """4+ expiries in 35 days, on the day with the most labelled names -- not the newest day, which a
    weekend fetch leaves nearly empty. Tastytrade's rating plays no part."""
    _label(monkeypatch, tmp_path, None)
    assert tradable.weeklies() == (set(), None)
    _label(monkeypatch, tmp_path, DAYS)
    assert tradable.weeklies() == ({"RATED", "PRICEY"}, "2026-10-02")


def test_the_studys_view_keeps_the_rating_it_declared(monkeypatch, tmp_path):
    _label(monkeypatch, tmp_path, DAYS)
    assert tradable.rated_today() == ({"RATED"}, "2026-10-02")
