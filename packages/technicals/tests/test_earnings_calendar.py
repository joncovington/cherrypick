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
    d, timing, src, confirmed, gap = ec.pick_date((date(2026, 10, 20), BMO), (date(2026, 10, 21), False))
    assert (d, timing, src, confirmed, gap) == (date(2026, 10, 21), "bmo", "broker", True, 1)


def test_a_dolt_date_far_from_the_confirmed_one_gives_no_time():
    """META: Dolt's Nov 4 is another announcement; Oct 21 is confirmed, its time unknown."""
    d, timing, _, _, gap = ec.pick_date((date(2026, 11, 4), AMC), (date(2026, 10, 21), False))
    assert (d, timing, gap) == (date(2026, 10, 21), None, 14)


def test_dolt_beats_an_estimated_broker_date_and_an_estimate_alone_says_so():
    assert ec.pick_date((date(2026, 11, 3), AMC), (date(2026, 11, 5), True))[:4] == (
        date(2026, 11, 3),
        "amc",
        "dolt",
        None,
    )
    assert ec.pick_date(None, (date(2026, 11, 5), True))[:4] == (date(2026, 11, 5), None, "broker", False)
    assert ec.pick_date(None, None) is None


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
                    "NFLX": {"earnings_date": "2026-10-20", "earnings_estimated": False},
                    "LATE": {"earnings_date": "2026-12-30", "earnings_estimated": False},
                }
            }
        },
    )
    dolt = {
        "events": {"NFLX": (date(2026, 10, 20), AMC), "SAP": (date(2026, 10, 21), BMO)},
        "names": {"NFLX": "Netflix, Inc.", "SAP": "SAP SE"},
        "closes": {"NFLX": 70.53, "SAP": 250.0},
        "close_date": "2026-10-16",
    }
    out = ec.build("2026-10-16", dolt=dolt)
    rows = {r["symbol"]: r for r in out["rows"]}
    assert sorted(rows) == ["NFLX", "SAP"]  # LATE reports outside the 30 days
    n = rows["NFLX"]
    assert (n["date"], n["timing"], n["date_source"], n["date_confirmed"]) == (
        "2026-10-20",
        "amc",
        "broker",
        True,
    )
    assert (n["entry_session"], n["reaction_day"], n["event_expiry"]) == (
        "2026-10-20",
        "2026-10-21",
        "2026-10-23",
    )
    assert n["sd_move_pct"] == 11.85  # the vendor's page: 11.0
    assert (n["edge_label"], n["strength"], n["iv_rank"], n["region"]) == ("strong bear", 2, 74, "US")
    assert n["suggestion"]["kind"] == "credit" and n["suggestion"]["right"] == "C"
    assert n["suggestion"]["expiry"] == "2026-11-20"
    assert rows["SAP"]["region"] == "intl" and rows["SAP"]["sd_move"] is None  # no IVs held for it
    assert out["inputs"]["security_list"] == "2026-10-16" and out["inputs"]["expiry_iv"] == "2026-10-16"
