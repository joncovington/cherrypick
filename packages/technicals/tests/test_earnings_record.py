"""The earnings calendar's forward record (earnings_record.py): which calendar speaks for an
announcement, how the move and the direction are scored, and the spread's money in the suite's layout.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from cherrypick.technicals import earnings_calendar, earnings_record, paths


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _q(bid, ask):
    return {"bid": bid, "ask": ask, "open_interest": 500, "volume": 50}


ROW = {
    "symbol": "NFLX",
    "company": "Netflix, Inc.",
    "date": "2026-10-20",
    "timing": "amc",
    "entry_session": "2026-10-20",
    "reaction_day": "2026-10-21",
    "edge": "sbe",
    "edge_label": "strong bear",
    "liquidity_rank": 1,
    "region": "US",
    "sd_move": 8.0,
    "sd_move_pct": 11.0,
    "em_move": 5.6,
    "em_move_pct": 8.0,
    "suggestion": {"kind": "credit", "right": "C", "short": 70.5, "long": 76.2, "expiry": "2026-11-20"},
}


def test_signed_cash_follows_the_side():
    assert earnings_record.cash("credit", 2.0, opening=True) == 200.0
    assert earnings_record.cash("credit", 0.5, opening=False) == -50.0
    assert earnings_record.cash("debit", 1.6, opening=True) == -160.0
    assert earnings_record.cash("debit", 3.0, opening=False) == 300.0


def test_the_calendar_that_speaks_is_the_last_one_before_the_report():
    days = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]
    assert earnings_record.calendar_before(days, "2026-10-20") == "2026-10-19"
    assert earnings_record.calendar_before(days, "2026-10-15") is None


def test_the_record_scores_the_move_the_direction_and_the_spread():
    cal_dir = earnings_calendar.out_dir()
    # The 10-19 calendar speaks for NFLX (the session before its 10-20 report); the 10-20 one,
    # with a different move, does not.
    _write(cal_dir / "2026-10-19.json", {"rows": [ROW]})
    _write(cal_dir / "2026-10-20.json", {"rows": [dict(ROW, em_move=99.0)]})
    greeks = paths.market_report_dir() / "screener-greeks"
    legs = [
        {"role": "long", "strike": 75.0, "option": "L", "quote": _q(1.0, 1.1)},
        {"role": "short", "strike": 70.0, "option": "S", "quote": _q(3.0, 3.1)},
    ]
    entry = {
        "symbol": "NFLX",
        "date": "2026-10-20",
        "kind": "credit",
        "right": "C",
        "expiry": "2026-11-20",
        "width": 5.0,
        "legs": legs,
        "mid": 2.0,
    }
    _write(greeks / "earnings-legs" / "2026-10-20" / "1130.json", {"rows": [dict(entry, mid=9.0)]})
    _write(greeks / "earnings-legs" / "2026-10-20" / "1430.json", {"rows": [entry]})
    mark_legs = [
        {"role": "long", "strike": 75.0, "option": "L", "quote": _q(0.2, 0.3)},
        {"role": "short", "strike": 70.0, "option": "S", "quote": _q(0.8, 0.9)},
    ]
    mark = {
        "symbol": "NFLX",
        "which": "react",
        "entry_reading": "2026-10-20/1430.json",
        "legs": mark_legs,
        "mid": 0.6,
    }
    _write(greeks / "earnings-marks" / "2026-10-21" / "1430.json", {"rows": [mark]})

    closes = {("NFLX", "2026-10-20"): 70.0, ("NFLX", "2026-10-21"): 63.0}
    out = earnings_record.build(date(2026, 10, 23), closes_fn=lambda wanted: closes)
    [r] = out["rows"]
    assert (r["calendar_day"], r["em_move"], r["move"], r["move_pct"]) == ("2026-10-19", 5.6, -7.0, -10.0)
    # A 7.00 fall against the 5.60 expected move (0.85 x straddle): 1.25 of it, outside; the one-SD
    # move (8.00) is kept beside it. A bear, and it fell.
    assert (r["move_over_em"], r["inside_em"], r["direction_right"], r["sd_move"]) == (1.25, False, True, 8.0)
    s = r["spread"]
    assert (s["state"], s["entry_reading"], s["entry_mid"]) == ("entered", "2026-10-20/1430.json", 2.0)
    t = s["react"]
    # Opened for a 2.00 credit (+200), closed for 0.60 (-60): gross +140, costs separate, net less them.
    assert (t["entry"], t["exit"], t["gross"]) == (200.0, -60.0, 140.0)
    assert t["fees"] > 0 and t["slippage"] > 0
    assert t["net"] == pytest.approx(140.0 - t["fees"] - t["slippage"])
    assert s["day2"] is None  # no day-2 mark on file
    assert out["summary"]["spreads_react"]["n"] == 1 and out["summary"]["direction_right"] == 1.0


def _scored(sym, move, em, expiry_days=2):
    return {
        "move": move,
        "move_over_em": None if em is None else round(abs(move) / em, 3),
        "inside_em": None if em is None else abs(move) <= em,
        "event_expiry_days": expiry_days,
        "direction_right": None,
        "spread": None,
        "symbol": sym,
    }


def test_the_summary_counts_every_measured_move_and_says_how_many_had_an_expected_move():
    rows = [
        _scored("A", 1.0, 2.0),
        _scored("B", 3.0, 2.0),
        _scored("C", 1.0, None),  # measured, but no expected move to compare it with
        dict(_scored("D", 1.0, None), move=None),  # not measured
    ]
    s = earnings_record.summary(rows)
    assert (s["announcements"], s["measured_moves"], s["compared"]) == (4, 3, 2)
    assert "measured" not in s  # the old word counted only the compared ones


def test_the_median_of_an_even_count_is_the_mean_of_the_middle_two():
    # 0.5 and 1.5: the median is 1.0, not the upper middle 1.5.
    s = earnings_record.summary([_scored("A", 1.0, 2.0), _scored("B", 3.0, 2.0)])
    assert s["median_move_over_em"] == 1.0


def test_the_summary_is_also_stated_over_moves_priced_within_a_week_of_the_reaction():
    rows = [
        _scored("WEEK", 3.0, 2.0, expiry_days=2),  # outside a one-week move
        _scored("MONTH", 3.0, 6.0, expiry_days=28),  # inside, but against a month's move
        _scored("EDGE", 1.0, 2.0, expiry_days=7),  # a week to the day still counts
    ]
    s = earnings_record.summary(rows)
    assert (s["compared"], s["inside_em"]) == (3, 0.667)
    wk = s["earnings_week"]
    assert (wk["max_days"], wk["n"], wk["inside_em"], wk["median_move_over_em"]) == (7, 2, 0.5, 1.0)


def test_a_record_row_carries_its_event_expiry_and_how_far_past_the_reaction_it_is():
    cal_dir = earnings_calendar.out_dir()
    _write(cal_dir / "2026-10-19.json", {"rows": [dict(ROW, event_expiry="2026-11-20")]})
    closes = {("NFLX", "2026-10-20"): 70.0, ("NFLX", "2026-10-21"): 63.0}
    [r] = earnings_record.build(date(2026, 10, 23), closes_fn=lambda wanted: closes)["rows"]
    assert (r["event_expiry"], r["event_expiry_days"]) == ("2026-11-20", 30)


def test_an_announcement_not_yet_reacted_or_without_an_entry_session_records_no_trade():
    cal_dir = earnings_calendar.out_dir()
    unknown = dict(ROW, symbol="UNK", timing=None, entry_session=None)
    later = dict(ROW, symbol="LATER", date="2026-10-27", reaction_day="2026-10-28")
    _write(cal_dir / "2026-10-19.json", {"rows": [unknown, later]})
    out = earnings_record.build(date(2026, 10, 23), closes_fn=lambda wanted: {})
    assert [r["symbol"] for r in out["rows"]] == ["UNK"]  # LATER has not reacted yet
    assert out["rows"][0]["spread"] is None and out["rows"][0]["move"] is None
