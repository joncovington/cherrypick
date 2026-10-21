"""The earnings calendar (earnings_calendar.py): how a row's date, time, move and spread are chosen.

Each case is one the 2026-10-16 build met against the vendor's own calendar page: PM's date a day off
in Dolt with the right time, META's Dolt date a different announcement, CB's event expiry a monthly.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest

from cherrypick.technicals import earnings_calendar as ec
from cherrypick.technicals import paths

BMO, AMC = ec.BMO, ec.AMC


@pytest.fixture(autouse=True)
def vendor_on():
    """These cases were met on the vendor's list, so the vendor is switched on for them: the suite
    config the collector reads, in the test's own home."""
    from cherrypick.core import home

    cfg = {"market_report": {"collector": True, "vendor_dashboard_url": "x", "vendor_edition_title": "y"}}
    home.config_path().parent.mkdir(parents=True, exist_ok=True)
    home.config_path().write_text(json.dumps(cfg), encoding="utf-8")


def test_company_names_lose_the_share_class_boilerplate():
    assert ec.company("Netflix, Inc. - Common Stock") == "Netflix, Inc."
    assert ec.company("Chubb Limited  Common Stock") == "Chubb Limited"
    assert ec.company("Meta Platforms, Inc. - Class A Common Stock") == "Meta Platforms, Inc."
    assert ec.company("Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares") == (
        "Taiwan Semiconductor Manufacturing Company Ltd."
    )
    assert ec.company(None) is None


def test_a_confirmed_broker_date_wins_and_keeps_dolts_time_a_day_off():
    """PM: Dolt had Oct 20 before the open; the broker's confirmed Oct 21 is right, and so is the time."""
    got = ec.pick_date((date(2026, 10, 20), BMO), (date(2026, 10, 21), False))
    assert got == (date(2026, 10, 21), "bmo", "broker", True, 1, "dolt")


def test_a_dolt_date_far_from_the_confirmed_one_gives_no_time_of_its_own():
    """META: Dolt's Nov 4 is another announcement; without the broker's time, Oct 21's is unknown."""
    d, timing, _, _, gap, tsrc = ec.pick_date((date(2026, 11, 4), AMC), (date(2026, 10, 21), False))
    assert (d, timing, gap, tsrc) == (date(2026, 10, 21), None, 14, None)


def test_the_brokers_time_belongs_to_its_own_date():
    """META on 2026-10-16: the broker sent AMC with its confirmed Oct 21, which is what the vendor shows."""
    got = ec.pick_date((date(2026, 11, 4), None), (date(2026, 10, 21), False, "AMC"))
    assert got[1] == "amc" and got[5] == "broker"
    assert ec.pick_date(None, (date(2026, 10, 21), False, "BTO"))[1] == "bmo"
    # Dolt's date shown (the broker's only an estimate two days off): the broker's time is not carried
    # onto it, and Dolt's own blank stays unknown.
    got = ec.pick_date((date(2026, 11, 3), None), (date(2026, 11, 5), True, "AMC"))
    assert (got[0], got[1], got[5]) == (date(2026, 11, 3), None, None)
    # An unrecognised value is no time.
    assert ec.pick_date(None, (date(2026, 10, 21), False, "DMH"))[1] is None


def test_dolt_beats_an_estimated_broker_date_and_an_estimate_alone_says_so():
    assert ec.pick_date((date(2026, 11, 3), AMC), (date(2026, 11, 5), True))[:4] == (
        date(2026, 11, 3),
        "amc",
        "dolt",
        None,
    )
    assert ec.pick_date(None, (date(2026, 11, 5), True))[:4] == (date(2026, 11, 5), None, "broker", False)
    assert ec.pick_date(None, None) is None


def test_nasdaq_times_come_after_the_brokers_and_before_dolts():
    oct21 = date(2026, 10, 21)
    # The broker's own time wins over Nasdaq's.
    got = ec.pick_date(None, (oct21, False, "AMC"), [(oct21, "bmo")])
    assert (got[1], got[5]) == ("amc", "broker")
    # No broker time: Nasdaq's, for its date within a day of the one shown, over Dolt's.
    got = ec.pick_date((oct21, AMC), (oct21, False), [(date(2026, 10, 22), "bmo")])
    assert (got[1], got[5]) == ("bmo", "nasdaq")
    # Nasdaq listing the name without a time, or for a date further off, leaves it to Dolt.
    assert ec.pick_date((oct21, AMC), (oct21, False), [(oct21, None)])[5] == "dolt"
    assert ec.pick_date((oct21, AMC), (oct21, False), [(date(2026, 10, 28), "bmo")])[5] == "dolt"


def test_the_usual_time_is_the_last_timed_report_within_400_days():
    hist = [(date(2026, 4, 20), AMC), (date(2026, 7, 21), BMO), (date(2025, 1, 2), AMC)]
    assert ec.usual_time(hist, date(2026, 10, 20)) == "bmo"
    assert ec.usual_time([(date(2025, 1, 2), AMC)], date(2026, 10, 20)) is None  # too old
    assert ec.usual_time([], date(2026, 10, 20)) is None
    assert ec.usual_time([(date(2026, 10, 25), AMC)], date(2026, 10, 20)) is None  # not before


def test_the_times_file_is_the_newest_fetched_before_the_next_session():
    """The calendar of Friday 10-16 reads Saturday's fetch, not the following Monday evening's."""
    folder = paths.earnings_times_dir()
    for day, t in (("2026-10-15", "amc"), ("2026-10-17", "bmo"), ("2026-10-20", "amc")):
        _write(folder / f"{day}.json", {"dates": {"2026-10-21": {"PM": t}}})
    at, times = ec.times_on_file("2026-10-16")
    assert at == "2026-10-17" and times["PM"] == [(date(2026, 10, 21), "bmo")]


def test_the_side_and_the_read_are_ours_and_the_brokers_never_the_vendors():
    """Every name reads strong bull at rank 40 on the vendor's list; none of that is used.

    BRK: our strong bull, broker rank 62 -> a credit put spread (the vendor's 40 would buy a debit).
    WEAK: the vendor's strong bull but our plain bear -> no suggestion.
    NOR: our strong bull but no broker rank -> no suggestion, never the vendor's rank as a stand-in.
    NONE: no bars of ours -> no direction at all, though the vendor has one."""
    names = ("BRK", "WEAK", "NOR", "NONE")
    _write(
        paths.market_report_dir() / "vendor-securities" / "2026-10-16.json",
        {
            "rows": [
                {
                    "symbol": s,
                    "type": "Stock",
                    "directionalEdge": "sb",
                    "technicalRank": 9,
                    "ivRank": 40,
                    "liquidityRank": 1,
                    "hasOptions": True,
                }
                for s in names
            ]
        },
    )
    _write(paths.expiry_iv_dir() / "2026-10-16.json", {"names": {s: {"2026-11-20": 0.4} for s in names}})
    ranks = {"BRK": {"iv_rank": 62.0}, "WEAK": {"iv_rank": 62.0}, "NONE": {"iv_rank": 62.0}}
    _write(paths.tastytrade_iv_rank(), {"days": {"2026-10-16": ranks}})
    dolt = {
        "events": {s: (date(2026, 10, 20), AMC) for s in names},
        "names": {},
        "closes": {s: 100.0 for s in names},
        "close_date": "2026-10-16",
    }
    read = {"edge": "sb", "edge_label": "strong bull", "strength": 8, "session": "2026-10-16"}
    ours = {"BRK": read, "NOR": read, "WEAK": dict(read, edge="brn", edge_label="bear", strength=5)}
    rows = {r["symbol"]: r for r in ec.build("2026-10-16", dolt=dolt, ours=ours)["rows"]}
    b = rows["BRK"]
    assert (b["edge"], b["strength"], b["iv_rank"]) == ("sb", 8, 62.0)
    assert (b["vendor_edge"], b["vendor_strength"], b["vendor_iv_rank"]) == ("sb", 9, 40)  # kept, unused
    assert b["suggestion"]["kind"] == "credit" and b["suggestion"]["right"] == "P"
    assert rows["WEAK"]["edge"] == "brn" and rows["WEAK"]["suggestion"] is None
    assert rows["NOR"]["iv_rank"] is None and rows["NOR"]["suggestion"] is None
    assert rows["NONE"]["edge"] is None and rows["NONE"]["suggestion"] is None


def test_our_reads_need_a_lookup_and_a_held_name():
    """No saved security list to fit Bearing on, or a name the store does not hold: no read."""
    assert ec.our_reads("2026-10-16", ["NFLX"]) == {}  # the test home holds no lists
    assert ec.CODES["strong bull"] == "sb" and ec.CODES["strong bear"] == "sbe"


def test_reaction_day_and_entry_session_follow_the_time_of_day():
    tue, wed = date(2026, 10, 20), date(2026, 10, 21)
    assert (ec.reaction_day(tue, "amc"), ec.entry_session(tue, "amc")) == (wed, tue)
    assert (ec.reaction_day(wed, "bmo"), ec.entry_session(wed, "bmo")) == (wed, tue)
    # Unknown: the later reaction day (never an expiry the market could not trade the news on), no entry.
    assert (ec.reaction_day(wed, None), ec.entry_session(wed, None)) == (date(2026, 10, 22), None)


def test_the_move_is_one_sd_to_the_event_expiry():
    """CB on 2026-10-16: only monthlies listed, so the event expiry is Nov 20, 35 days out."""
    ivs = {"2026-11-20": 0.228215, "2026-12-18": 0.214303}
    exp, iv = ec.event_expiry(ivs, date(2026, 10, 21))
    assert (exp, iv) == ("2026-11-20", 0.228215)
    sd = ec.sd_move(359.79, iv, date(2026, 10, 16), exp)
    assert sd == pytest.approx(359.79 * 0.228215 * math.sqrt(35 / 365))
    assert round(100 * sd / 359.79, 2) == 7.07  # the vendor's page: 6.8
    assert ec.event_expiry(ivs, date(2027, 1, 4)) is None


def test_the_suites_expected_move_is_about_two_thirds_of_an_sd():
    assert ec.EM_TO_SD == pytest.approx(0.678, abs=0.001)


def test_monthlies_and_the_cycle_expiry():
    assert ec.is_monthly("2026-11-20") and not ec.is_monthly("2026-11-27")
    assert ec.is_monthly("2025-04-17")  # Good Friday 2025: the Thursday before
    ivs = {"2026-10-23": 0.8, "2026-11-20": 0.3, "2026-12-18": 0.25}
    s = date(2026, 10, 16)
    assert ec.trade_expiry(ivs, s, "cycle", "2026-10-23") == "2026-11-20"  # 45-60 empty, 35 days in 30-60
    assert ec.trade_expiry(ivs, s, "event", "2026-10-23") == "2026-10-23"
    assert ec.trade_expiry({"2026-10-23": 0.8}, s, "cycle", "2026-10-23") is None


def test_the_suggestion_follows_the_navigator_rule_on_strong_reads_only():
    # Strong bull, IV rank under the cut: a call debit spread, long at the money, short at the move.
    assert ec.suggestion("sb", 40, 100.0, 5.0, 50) == {
        "kind": "debit",
        "right": "C",
        "long": 100.0,
        "short": 105.0,
    }
    # Strong bear, at or over the cut: a call credit spread, short at the money, long beyond the move.
    assert ec.suggestion("sbe", 50, 100.0, 5.0, 50) == {
        "kind": "credit",
        "right": "C",
        "short": 100.0,
        "long": 105.0,
    }
    # Strong bull, rich: a put credit spread below.
    assert ec.suggestion("sb", 74, 100.0, 5.0, 50)["long"] == 95.0
    assert ec.suggestion("bp", 40, 100.0, 5.0, 50) is None  # a pullback is not strong
    assert ec.suggestion("sb", None, 100.0, 5.0, 50) is None


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_build_joins_the_sources_into_one_row_per_announcement():
    mr = paths.market_report_dir()
    _write(
        mr / "vendor-securities" / "2026-10-16.json",
        {
            "rows": [
                {
                    "symbol": "NFLX",
                    "type": "Stock",
                    "directionalEdge": "sbe",
                    "technicalRank": 2,
                    "ivRank": 74,
                    "liquidityRank": 1,
                    "hasOptions": True,
                },
                {
                    "symbol": "SAP",
                    "type": "Depository Receipt",
                    "directionalEdge": "sb",
                    "technicalRank": 8,
                    "ivRank": 30,
                    "liquidityRank": 1,
                    "hasOptions": True,
                },
            ]
        },
    )
    _write(
        paths.expiry_iv_dir() / "2026-10-16.json",
        {"names": {"NFLX": {"2026-10-23": 0.85571, "2026-11-20": 0.5}}},
    )
    _write(
        paths.tastytrade_iv_rank(),
        {
            "days": {
                "2026-10-16": {
                    "NFLX": {"earnings_date": "2026-10-20", "earnings_estimated": False, "iv_rank": 70.38},
                    "LATE": {"earnings_date": "2026-12-30", "earnings_estimated": False},
                }
            }
        },
    )
    _write(paths.earnings_times_dir() / "2026-10-17.json", {"dates": {"2026-10-20": {"NFLX": "amc"}}})
    dolt = {
        "events": {
            "NFLX": (date(2026, 10, 20), AMC),
            "SAP": (date(2026, 10, 21), BMO),
            "EST": (date(2026, 10, 22), None),
        },
        "history": {"EST": [(date(2026, 7, 23), AMC)]},
        "names": {"NFLX": "Netflix, Inc.", "SAP": "SAP SE"},
        "closes": {"NFLX": 70.53, "SAP": 250.0, "EST": 50.0},
        "close_date": "2026-10-16",
    }
    ours = {"NFLX": {"edge": "sbe", "edge_label": "strong bear", "strength": 3, "session": "2026-10-16"}}
    out = ec.build("2026-10-16", dolt=dolt, ours=ours)
    est = {r["symbol"]: r for r in out["rows"]}["EST"]
    # No source times it: unknown, with last quarter's time beside it as an estimate that sets nothing.
    assert (est["timing"], est["usual_timing"], est["entry_session"]) == (None, "amc", None)
    assert out["inputs"]["nasdaq_times"] == "2026-10-17"
    out["rows"] = [r for r in out["rows"] if r["symbol"] != "EST"]
    rows = {r["symbol"]: r for r in out["rows"]}
    assert sorted(rows) == ["NFLX", "SAP"]  # LATE reports outside the 30 days
    n = rows["NFLX"]
    assert (n["date"], n["timing"], n["date_source"], n["date_confirmed"]) == (
        "2026-10-20",
        "amc",
        "broker",
        True,
    )
    assert n["timing_source"] == "nasdaq"  # the broker sent no time for it; Nasdaq comes before Dolt
    assert (n["entry_session"], n["reaction_day"], n["event_expiry"]) == (
        "2026-10-20",
        "2026-10-21",
        "2026-10-23",
    )
    assert n["sd_move_pct"] == 11.85  # the vendor's page: 11.0
    # Direction and strength are ours (3/10), IV rank the broker's (70.38); the vendor's (2/10, 74)
    # are kept aside, unused.
    assert (n["edge_label"], n["strength"], n["read_session"], n["iv_rank"], n["region"]) == (
        "strong bear",
        3,
        "2026-10-16",
        70.38,
        "US",
    )
    assert (n["vendor_edge"], n["vendor_strength"], n["vendor_iv_rank"]) == ("sbe", 2, 74)
    assert n["suggestion"]["kind"] == "credit" and n["suggestion"]["right"] == "C"
    assert n["suggestion"]["expiry"] == "2026-11-20"
    assert rows["SAP"]["region"] == "intl" and rows["SAP"]["sd_move"] is None  # no IVs held for it
    assert rows["SAP"]["edge"] is None  # we hold no bars for it, so it has no read
    assert out["inputs"]["security_list"] == "2026-10-16" and out["inputs"]["expiry_iv"] == "2026-10-16"
    assert out["inputs"]["security_source"] == "vendor"
    # The event expiry is two days past the reaction: a week's expiry, an earnings move.
    assert n["event_expiry_days"] == 2

    # Dolt a session behind (its newest close the 15th on the 16th's build): the price is the 15th's,
    # so the move's days count from the 15th -- 8 to the 23rd, not the 7 from the build day.
    behind = ec.build("2026-10-16", dolt=dict(dolt, close_date="2026-10-15"), ours=ours)
    late = {r["symbol"]: r for r in behind["rows"]}["NFLX"]
    assert late["sd_move"] == pytest.approx(70.53 * 0.85571 * math.sqrt(8 / 365), abs=1e-4)
    assert behind["inputs"]["close_date"] == "2026-10-15"


def test_an_expiry_past_the_reaction_week_is_counted_in_days():
    # FIX on the 2026-10-20 calendar: reacts 10-23, only monthlies listed, so its move is to 11-20.
    assert ec.expiry_days_past("2026-11-20", date(2026, 10, 23)) == 28
    assert ec.expiry_days_past("2026-10-23", date(2026, 10, 23)) == 0
    assert ec.EARNINGS_WEEK_DAYS == 7
