"""core.marketprofile: one session's TPO profile.

The hand-worked case is the guard. Its value area (99-103) is what the CBOT two-row expansion gives;
growing one row at a time gives 100-103 instead, and the test fails (checked by breaking
`build_profile` that way on purpose). The fixture file is shared with the console's TypeScript port,
which pins itself to the same answers.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.core import marketprofile as mp

ET = ZoneInfo("America/New_York")
CASES = json.loads((Path(__file__).parent / "fixtures" / "marketprofile_cases.json").read_text())


def _t(d: dt.date, hh: int, mm: int) -> int:
    return int(dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).timestamp())


def _case(case: dict) -> mp.Profile | None:
    session = dt.date.fromisoformat(case["session"]) if case["session"] else None
    return mp.build_profile(
        case["bars"], row_size=case["row_size"], bar_seconds=case["bar_seconds"], session=session
    )


def test_hand_worked_profile():
    p = _case(CASES["cases"][0])
    # A 100.2-103.5, B 101.0-104.9, C 102.5-102.9, D 99.5-101.2, rows of 1
    assert [(r.price, r.letters) for r in p.rows] == [
        (99.0, "D"),
        (100.0, "AD"),
        (101.0, "ABD"),
        (102.0, "ABC"),
        (103.0, "AB"),
        (104.0, "B"),
    ]
    assert p.tpo_total == 12
    # 101 and 102 tie on 3; both sit half a row from the middle, so the lower wins
    assert p.poc == 101.0
    # need 8.4 of 12: POC 3, + rows 102-103 (5 vs 3 below) = 8, + rows 99-100 (3 vs 1 above) = 11
    assert (p.val, p.vah) == (99.0, 103.0)
    assert (p.ib_low, p.ib_high) == (100.2, 104.9)
    assert p.extension_up == 0.0
    assert p.extension_down == pytest.approx(0.7)
    assert (p.tail_low, p.tail_high, p.poor_low, p.poor_high) == (1, 1, False, False)
    assert p.single_prints == ()
    assert (p.open, p.close, p.high, p.low) == (101.0, 100.0, 104.9, 99.5)
    # A 400 over four rows, B 500 over four, C 100 on one, D 300 over three
    assert [r.volume for r in p.rows] == [100.0, 200.0, 325.0, 325.0, 225.0, 125.0]
    assert p.volume_poc == 101.0
    assert p.periods == "ABCD"


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_fixture_cases(case):
    got = _case(case).as_dict()
    want = case["expected"]
    assert got.keys() == want.keys()
    for key, value in want.items():
        if key == "rows":
            assert [(r["price"], r["letters"]) for r in got[key]] == [
                (r["price"], r["letters"]) for r in value
            ]
            assert [r["volume"] for r in got[key]] == pytest.approx([r["volume"] for r in value])
        elif isinstance(value, float):
            assert got[key] == pytest.approx(value), key
        else:
            assert got[key] == value, key


@pytest.mark.parametrize(("price", "size"), CASES["row_sizes"])
def test_default_row_size(price, size):
    assert mp.default_row_size(price) == size


def test_overnight_and_other_days_are_ignored():
    d = dt.date(2026, 11, 13)
    bars = [
        {"t": _t(d, 4, 0), "o": 50, "h": 60, "l": 40, "c": 50, "v": 1},  # overnight
        {"t": _t(d, 9, 30), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1},
        {"t": _t(d, 16, 0), "o": 100, "h": 200, "l": 100, "c": 150, "v": 1},  # after the close
        {"t": _t(d - dt.timedelta(days=1), 10, 0), "o": 10, "h": 10, "l": 10, "c": 10, "v": 1},
    ]
    p = mp.build_profile(bars, row_size=1, bar_seconds=1800)
    assert p.session == d
    assert (p.low, p.high) == (99, 101)
    assert p.periods == "A"
    assert p.ib_high is None and p.extension_up is None
    assert mp.session_dates(bars) == [d - dt.timedelta(days=1), d]


def test_index_without_volume_has_no_volume_poc():
    d = dt.date(2026, 11, 13)
    bars = [{"t": _t(d, 9, 30), "o": 100, "h": 101, "l": 99, "c": 100, "v": 0}]
    assert mp.build_profile(bars, row_size=1, bar_seconds=1800).volume_poc is None


def test_one_period_is_not_a_tail_at_both_ends():
    d = dt.date(2026, 11, 13)
    p = mp.build_profile(
        [{"t": _t(d, 9, 30), "o": 1, "h": 5.5, "l": 1, "c": 5, "v": 0}], row_size=1, bar_seconds=1800
    )
    assert (p.tail_high, p.tail_low) == (5, 0)


def test_refuses_rather_than_guesses():
    d = dt.date(2026, 11, 13)
    with pytest.raises(ValueError):
        mp.build_profile([], row_size=0, bar_seconds=60)
    with pytest.raises(ValueError):
        mp.build_profile([], row_size=1, bar_seconds=7 * 60)  # does not divide 30 minutes
    with pytest.raises(ValueError):  # a 15-minute bar starting at 09:35 would straddle A and B
        mp.build_profile([{"t": _t(d, 9, 35), "o": 1, "h": 1, "l": 1, "c": 1}], row_size=1, bar_seconds=900)
    with pytest.raises(ValueError):
        mp.default_row_size(0)
    assert mp.build_profile([], row_size=1, bar_seconds=60) is None
    bad = [{"t": _t(d, 9, 30), "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}]  # the feed's empty sentinel
    assert mp.build_profile(bad, row_size=1, bar_seconds=1800, session=d) is None
