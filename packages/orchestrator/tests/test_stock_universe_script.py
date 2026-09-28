"""The stock-universe builder's rule, exercised on synthetic measurements.

`scripts/build_stock_universe.py` decides which names the market report's breadth engines run over,
and the only thing that makes a name belong is a measured tight market. So the value here is in
failing: each test breaks one part of that (a wide option, a stale quote, too few sessions, a low
rating) and asserts the name does not get in for exactly that reason.

It lives here beside the vendor collector's tests because the orchestrator schedules it; both move
to the market-report package once that exists (docs/market-report-plan.md, "Where the code goes").
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_stock_universe.py"
ET = ZoneInfo("America/New_York")


def _module():
    spec = importlib.util.spec_from_file_location("build_stock_universe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bsu = _module()

# Three fixed sessions, measured at 11:00 ET: permanent dates, nothing in them to expire.
DAYS = ("2026-09-21", "2026-09-22", "2026-09-23")


def _at(day: str, hhmm: str = "11:00") -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=ET).astimezone(UTC)


def _quote(bid, ask, at: datetime, age_min: int = 1) -> dict:
    return {"bid": bid, "ask": ask, "updated_at": (at - timedelta(minutes=age_min)).isoformat()}


def _row(at: datetime, *, stock=(99.99, 100.01), call=(2.00, 2.04), put=(1.98, 2.02), rating=4, **kw) -> dict:
    row = {
        "listed": True,
        "is_etf": False,
        "is_illiquid": False,
        "liquidity_rating": rating,
        "quote": _quote(*stock, at),
        "options": {"call": _quote(*call, at), "put": _quote(*put, at)},
    }
    row.update(kw)
    return row


def _measurements(sym="AAA", days=DAYS, **kw) -> list[dict]:
    out = []
    for day in days:
        at = _at(day)
        out.append({"measured_at": at.isoformat(), "names": {sym: _row(at, **kw)}})
    return out


def _volumes(contracts=50_000, sym="AAA", days=DAYS) -> dict:
    """OCC volume by session, keyed the way OCC writes the underlying."""
    return {day: {bsu.to_occ(sym): contracts} for day in days}


def _status(measurements, sym="AAA", volumes=None) -> dict:
    volumes = _volumes(sym=sym) if volumes is None else volumes
    return bsu.build_universe({sym: {"vendor": {}}}, measurements, volumes)["names"][sym]


def test_a_tight_name_measured_in_enough_sessions_is_in():
    name = _status(_measurements())
    assert name["status"] == "in", name["reasons"]


def test_a_wide_option_keeps_a_name_out():
    # 2.00 x 2.20 is 10% of mid and 20 cents: over both legs of the bar.
    name = _status(_measurements(call=(2.00, 2.20)))
    assert name["status"] == "out"
    assert any(r.startswith("option spread") for r in name["reasons"])


def test_a_cheap_option_passes_on_the_absolute_leg():
    # 0.20 x 0.24 is 18% of mid but four cents wide: tight in the only sense a cheap option can be.
    name = _status(_measurements(call=(0.20, 0.24), put=(0.20, 0.24)))
    assert name["status"] == "in", name["reasons"]


def test_a_wide_stock_keeps_a_name_out():
    name = _status(_measurements(stock=(99.90, 100.10)))
    assert name["status"] == "out"
    assert any(r.startswith("stock spread") for r in name["reasons"])


def test_a_one_tick_low_priced_stock_passes():
    name = _status(_measurements(stock=(9.99, 10.00)))
    assert name["status"] == "in", name["reasons"]


def test_the_rating_is_a_guide_not_a_gate():
    """DELL traded 266k contracts on 2026-09-25 rated 2; LYG traded 22 rated 4. The rating is
    noted beside the verdict and never decides it."""
    name = _status(_measurements(rating=2))
    assert name["status"] == "in", name["reasons"]
    assert "tastytrade liquidity rating 2 of 4" in name["guides"]


def test_a_top_rating_does_not_rescue_a_name_nobody_trades():
    name = _status(_measurements(rating=4), volumes=_volumes(22))
    assert name["status"] == "out"
    assert any(r.startswith("option volume 22 contracts/day") for r in name["reasons"])


def test_the_illiquid_flag_is_a_guide_too():
    name = _status(_measurements(is_illiquid=True))
    assert name["status"] == "in" and "tastytrade flags it illiquid" in name["guides"]


def test_thin_option_volume_keeps_a_name_out():
    name = _status(_measurements(), volumes=_volumes(9_999))
    assert name["status"] == "out"


def test_option_volume_known_for_too_few_sessions_is_pending():
    name = _status(_measurements(), volumes=_volumes(days=DAYS[:2]))
    assert name["status"] == "pending"
    assert "option volume known for 2 of 3 sessions" in name["reasons"]


def test_a_session_whose_file_lacks_the_name_counts_as_zero_volume():
    vols = _volumes()
    vols[DAYS[1]] = {"OTHER": 1}
    vols[DAYS[2]] = {"OTHER": 1}
    assert _status(_measurements(), volumes=vols)["status"] == "out"


def test_occ_counts_both_sides_so_contracts_are_half_the_total():
    csv_text = (
        "quantity,underlying,symbol,actype,porc,exchange,actdate\n"
        "1000,AAPL,AAPL,C,C,CBOE,09/25/2026\n"
        "40,AAPL,2AAPL,F,P,CBOE,09/25/2026\n"
        "1040,AAPL,AAPL,M,C,CBOE,09/25/2026\n"
        "8,BRKB,BRKB,C,C,CBOE,09/25/2026\n"
        "8,BRKB,BRKB,M,C,CBOE,09/25/2026\n"
        "x,BAD,BAD,C,C,CBOE,09/25/2026\n"
    )
    assert bsu.occ_volume(csv_text) == {"AAPL": 1040, "BRKB": 8}
    assert bsu.to_occ("BRK.B") == "BRKB"


def test_an_index_or_retired_symbol_is_out():
    at = _at(DAYS[0])
    name = _status([{"measured_at": at.isoformat(), "names": {"AAA": {"listed": False}}}])
    assert name["status"] == "out" and "not a listed equity" in name["reasons"][0]


def test_too_few_sessions_is_pending_not_in():
    name = _status(_measurements(days=DAYS[:2]))
    assert name["status"] == "pending"
    assert "measured in 2 of 3 sessions" in " ".join(name["reasons"])


def test_two_readings_in_one_session_count_as_one_session():
    ms = _measurements(days=DAYS[:2])
    extra = _at(DAYS[1], "14:30")
    ms.append({"measured_at": extra.isoformat(), "names": {"AAA": _row(extra)}})
    assert _status(ms)["status"] == "pending"


def test_an_unmeasured_candidate_is_pending():
    vols = {d: {"AAA": 50_000, "BBB": 50_000} for d in DAYS}
    doc = bsu.build_universe({"AAA": {}, "BBB": {}}, _measurements(), vols)
    assert doc["names"]["BBB"]["status"] == "pending"
    assert doc["members"] == ["AAA"]


def test_the_median_decides_so_one_wide_session_does_not_evict_a_tight_name():
    ms = _measurements()
    wide = _at("2026-09-24")
    ms.append({"measured_at": wide.isoformat(), "names": {"AAA": _row(wide, call=(2.00, 2.40))}})
    assert _status(ms)["status"] == "in"


# --- quotes that must not be measured -----------------------------------------------------------


def test_an_overnight_quote_is_not_a_reading():
    """The weekend snapshot that motivated this: NMR 9.55 x 10.98 stamped at 07:00 ET."""
    at = _at(DAYS[0])
    stale = {"bid": 9.55, "ask": 10.98, "updated_at": _at(DAYS[0], "07:00").isoformat()}
    r = bsu.reading(_row(at, quote=stale), at)
    assert r["stock"] is None and "stock quote stale or one-sided" in r["notes"]


def test_a_quote_from_the_previous_session_is_not_a_reading():
    at = _at(DAYS[1])
    old = {"bid": 99.99, "ask": 100.01, "updated_at": _at(DAYS[0], "15:59").isoformat()}
    assert bsu.reading(_row(at, quote=old), at)["stock"] is None


def test_a_resting_quote_from_this_morning_is_still_a_reading():
    at = _at(DAYS[0])
    resting = {"bid": 99.99, "ask": 100.01, "updated_at": _at(DAYS[0], "09:45").isoformat()}
    assert bsu.reading(_row(at, quote=resting), at)["stock"] is not None


def test_freshness_is_judged_at_the_quote_own_fetch_time():
    """Option quotes arrive minutes after the run starts; they are not newer than the run."""
    start = _at(DAYS[0])
    fetched = start + timedelta(minutes=9)
    q = {"bid": 2.0, "ask": 2.02, "updated_at": (fetched - timedelta(seconds=5)).isoformat()}
    assert not bsu.quote_fresh(q, start)
    assert bsu.quote_fresh({**q, "fetched_at": fetched.isoformat()}, start)


def test_a_one_sided_quote_is_not_a_reading():
    assert bsu.spread(0, 1.0) is None and bsu.spread(1.1, 1.0) is None and bsu.spread(None, 1) is None


def test_measuring_only_inside_the_window():
    def et(hhmm):
        hh, mm = (int(x) for x in hhmm.split(":"))
        return datetime(2026, 9, 21, hh, mm, tzinfo=ET)

    assert bsu.in_window(et("11:00"), True)
    assert not bsu.in_window(et("09:45"), True)  # the opening minutes
    assert not bsu.in_window(et("15:45"), True)  # the closing auction's run-up
    assert not bsu.in_window(et("11:00"), False)  # a holiday or weekend
    assert not bsu.in_window(et("12:45"), True, "13:00")  # an early close


# --- candidates ---------------------------------------------------------------------------------


def test_follow_feed_futures_never_become_candidates():
    orders = {
        "1": {
            "trader_id": 1,
            "filled_at": "2026-09-25T14:00:00Z",
            "order_legs": [{"underlying_symbol": "/MNQZ6"}],
        },
        "2": {
            "trader_id": 1,
            "filled_at": "2026-09-25T15:00:00Z",
            "order_legs": [{"underlying_symbol": "MU"}],
        },
    }
    cands = bsu.follow_candidates(orders, {"1": "Tom"})
    assert list(cands) == ["MU"] and cands["MU"]["traders"] == ["Tom"]


def test_merging_the_same_orders_twice_adds_nothing():
    kept: dict = {}
    batch = [{"id": 7, "order_legs": []}, {"id": 8, "order_legs": []}]
    assert bsu.merge_orders(kept, batch) == 2
    assert bsu.merge_orders(kept, batch) == 0 and len(kept) == 2


def test_edition_candidates_count_each_edition_once():
    link = '<a href="x?symbol={s}">{s}</a>'
    eds = {
        "2026-09-21": link.format(s="AMD") * 3,
        "2026-09-22": link.format(s="AMD") + link.format(s="BRK.B"),
    }
    c = bsu.edition_candidates(eds)
    assert c["AMD"] == {"first": "2026-09-21", "last": "2026-09-22", "editions": 2}
    assert bsu.to_tastytrade("BRK.B") == "BRK/B"


def test_choose_options_takes_the_atm_strike_nearest_thirty_days():
    exps = [
        {"expiration": "2026-10-02", "dte": 5, "strikes": [[100, "C5", "P5"]]},  # too near
        {"expiration": "2026-10-16", "dte": 19, "strikes": [[95, "a", "b"], [100, "C19", "P19"]]},
        {
            "expiration": "2026-10-30",
            "dte": 33,
            "strikes": [[95, "c", "d"], [100, "C33", "P33"], [105, "e", "f"]],
        },
    ]
    pick = bsu.choose_options(exps, 101.0)
    assert (pick["dte"], pick["strike"], pick["call"], pick["put"]) == (33, 100.0, "C33", "P33")
    assert bsu.choose_options(exps[:1], 101.0) is None


# --- the bar itself -----------------------------------------------------------------------------


def test_the_strict_bar_is_not_loosened():
    """The strict bar was chosen on 2026-09-27. Loosening it is a decision to write down, not an
    edit to slip in, so these fail loudly if any limit moves the permissive way."""
    r = bsu.RULE
    assert r["min_option_volume"] >= 10_000
    assert r["stock_max_spread_pct"] <= 0.0005 and r["stock_max_spread_abs"] <= 0.01
    assert r["option_max_spread_pct"] <= 0.03 and r["option_max_spread_abs"] <= 0.05
    assert r["min_sessions"] >= 3


def test_the_follow_feed_is_asked_slowly():
    assert bsu.FOLLOW_PAUSE_RANGE_S[0] >= 5 and bsu.OCC_PAUSE_RANGE_S[0] >= 5 and bsu.TT_PAUSE_S >= 1.0


def test_a_name_never_measured_is_out_once_its_volume_is_known_to_be_thin():
    doc = bsu.build_universe({"THIN": {}, "BUSY": {}}, [], {d: {"THIN": 50, "BUSY": 90_000} for d in DAYS})
    assert doc["names"]["THIN"]["status"] == "out"
    assert doc["names"]["BUSY"]["status"] == "pending"


def test_thin_is_only_decided_on_enough_sessions():
    assert not bsu.volume_too_thin([50, 50])
    assert bsu.volume_too_thin([50, 50, 50])
    assert not bsu.volume_too_thin([50, 20_000, 20_000])


# --- the tastytrade watchlist -------------------------------------------------------------------


def _ours(*symbols):
    return {"group_name": bsu.WATCHLIST_GROUP, "symbols": list(symbols)}


def test_the_first_sync_creates_the_list():
    plan = bsu.watchlist_plan(["MSFT", "BRK.B", "AAPL"], None)
    assert plan["action"] == "create" and plan["entries"] == ["AAPL", "BRK/B", "MSFT"]


def test_no_members_and_no_list_is_a_quiet_no_op():
    assert bsu.watchlist_plan([], None)["action"] == "none"


def test_a_same_named_list_the_script_did_not_make_is_never_replaced():
    """A replace drops every entry left out, so a person's own list of that name would be wiped."""
    plan = bsu.watchlist_plan(["AAPL"], {"group_name": "default", "symbols": ["TSLA", "F"]})
    assert plan["action"] == "refuse" and "will not replace it" in plan["reason"]


def test_a_sync_replaces_with_the_adds_and_removes_it_reports():
    plan = bsu.watchlist_plan(["AAPL", "MSFT", "NVDA"], _ours("AAPL", "MSFT", "INTC"))
    assert plan["action"] == "replace"
    assert (plan["add"], plan["remove"]) == (["NVDA"], ["INTC"])
    assert plan["entries"] == ["AAPL", "MSFT", "NVDA"]


def test_an_unchanged_universe_sends_nothing():
    assert bsu.watchlist_plan(["AAPL", "MSFT"], _ours("MSFT", "AAPL"))["action"] == "none"


def test_an_empty_universe_never_empties_the_list():
    """Not even when a large cut is allowed: an empty list is never what a sync should leave."""
    assert bsu.watchlist_plan([], _ours("AAPL", "MSFT"), allow_shrink=True)["action"] == "refuse"


def test_a_collapse_of_more_than_half_is_refused_unless_allowed():
    have = _ours(*"ABCDEFGHIJ")
    assert bsu.watchlist_plan(list("ABCD"), have)["action"] == "refuse"
    assert bsu.watchlist_plan(list("ABCD"), have, allow_shrink=True)["action"] == "replace"
    assert bsu.watchlist_plan(list("ABCDEF"), have)["action"] == "replace"


def test_the_body_marks_the_list_as_ours():
    body = bsu.watchlist_body(["AAPL"])
    assert body["group-name"] == bsu.WATCHLIST_GROUP and body["name"] == bsu.WATCHLIST_NAME
    assert body["watchlist-entries"] == [{"symbol": "AAPL", "instrument-type": "Equity"}]
