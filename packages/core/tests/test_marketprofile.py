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


def _composite(case: dict) -> mp.Composite | None:
    by_name = {c["name"]: c for c in CASES["cases"]}
    bars = case["bars"] if "bars" in case else by_name[case["bars_from"]]["bars"]
    return mp.build_composite(
        bars,
        sessions=[dt.date.fromisoformat(s) for s in case["sessions"]],
        row_size=case["row_size"],
        bar_seconds=case["bar_seconds"],
    )


def test_hand_worked_composite():
    # 11-12: A 100.0-102.5 (rows 100-102, 30 shares); 11-13: A 101.0-103.2 (rows 101-103, 30), B 101.1-101.9
    # (row 101, 5); 11-11 is asked for and has no bars
    c = _composite(CASES["composites"][0])
    assert c.sessions == (dt.date(2026, 11, 12), dt.date(2026, 11, 13))
    assert [(r.price, r.tpo, r.volume) for r in c.rows] == [
        (100.0, 1, 10.0),
        (101.0, 3, 25.0),
        (102.0, 2, 20.0),
        (103.0, 1, 10.0),
    ]
    # need 4.9 of 7: POC 101 holds 3, + rows 102-103 (3 vs 1 below) = 6
    assert (c.poc, c.val, c.vah, c.tpo_total) == (101.0, 101.0, 103.0, 7)
    assert (c.low, c.high, c.volume_poc) == (100.0, 103.2, 101.0)


@pytest.mark.parametrize("case", CASES["composites"], ids=[c["name"] for c in CASES["composites"]])
def test_fixture_composites(case):
    got = _composite(case).as_dict()
    want = case["expected"]
    assert got.keys() == want.keys()
    for key, value in want.items():
        if key == "rows":
            assert [(r["price"], r["tpo"]) for r in got[key]] == [(r["price"], r["tpo"]) for r in value]
            assert [r["volume"] for r in got[key]] == pytest.approx([r["volume"] for r in value])
        elif isinstance(value, float):
            assert got[key] == pytest.approx(value), key
        else:
            assert got[key] == value, key


def _untested(case: dict) -> list[tuple[dt.date, float]]:
    by_name = {c["name"]: c for c in CASES["cases"]}
    bars = case["bars"] if "bars" in case else by_name[case["bars_from"]]["bars"]
    return mp.untested_pocs(
        bars,
        sessions=[dt.date.fromisoformat(s) for s in case["sessions"]],
        row_size=case["row_size"],
        bar_seconds=case["bar_seconds"],
    )


def test_hand_worked_untested_pocs():
    # POCs: 11-10 101, 11-11 106, 11-12 103. An overnight bar on 11-13 at 101.0 reaches row 101; a bar at
    # 11-11's 16:00 close tops out at 105.99, under row 106, and one at 21:00 on 11-12 bottoms at 107.0,
    # row 106's top (both edges shown to fail when widened); 11-12's own bars never count against 11-12.
    got = _untested(CASES["untested"][0])
    assert got == [(dt.date(2026, 11, 11), 106.0), (dt.date(2026, 11, 12), 103.0)]


@pytest.mark.parametrize("case", CASES["untested"], ids=[c["name"] for c in CASES["untested"]])
def test_fixture_untested_pocs(case):
    assert [[d.isoformat(), poc] for d, poc in _untested(case)] == case["expected"]


def _overnight(case: dict) -> mp.Composite | None:
    by_name = {c["name"]: c for c in CASES["cases"]}
    bars = case["bars"] if "bars" in case else by_name[case["bars_from"]]["bars"]
    return mp.overnight_profile(
        bars,
        session=dt.date.fromisoformat(case["session"]),
        row_size=case["row_size"],
        bar_seconds=case["bar_seconds"],
    )


def test_hand_worked_overnight():
    # Thu 18:00 100.2-102.5, 18:30 101.0-101.8, Fri 03:00 101.5-103.1, 09:00 102.0-102.4; a 17:30 bar and the
    # 09:30 open are outside the window
    o = _overnight(CASES["overnight"][0])
    assert [(r.price, r.tpo) for r in o.rows] == [(100.0, 1), (101.0, 3), (102.0, 3), (103.0, 1)]
    # 101 and 102 tie on 3, half a row each side of the middle: the lower. Need 5.6 of 8: 3 + rows 102-103 (4)
    assert (o.poc, o.val, o.vah, o.tpo_total) == (101.0, 101.0, 103.0, 8)
    assert (o.low, o.high) == (100.2, 103.1)
    assert o.sessions == (dt.date(2026, 11, 13),)
    assert (
        mp.overnight_profile(
            CASES["overnight"][0]["bars"], session=dt.date(2026, 11, 20), row_size=1, bar_seconds=1800
        )
        is None
    )


@pytest.mark.parametrize("case", CASES["overnight"], ids=[c["name"] for c in CASES["overnight"]])
def test_fixture_overnight(case):
    got = _overnight(case).as_dict()
    want = case["expected"]
    assert got.keys() == want.keys()
    for key, value in want.items():
        if key == "rows":
            assert [(r["price"], r["tpo"]) for r in got[key]] == [(r["price"], r["tpo"]) for r in value]
            assert [r["volume"] for r in got[key]] == pytest.approx([r["volume"] for r in value])
        elif isinstance(value, float):
            assert got[key] == pytest.approx(value), key
        else:
            assert got[key] == value, key


def test_a_composite_of_one_session_is_that_sessions_profile():
    case = next(c for c in CASES["cases"] if c["name"].startswith("/NQ"))
    p = _case(case)
    c = mp.build_composite(case["bars"], sessions=[p.session], row_size=case["row_size"], bar_seconds=900)
    assert [(r.price, r.tpo) for r in c.rows] == [(r.price, r.tpo) for r in p.rows]
    assert (c.poc, c.val, c.vah) == (p.poc, p.val, p.vah)
    assert (
        mp.build_composite(case["bars"], sessions=[dt.date(2026, 1, 2)], row_size=10, bar_seconds=900) is None
    )


@pytest.mark.parametrize(("symbol", "price", "size"), CASES["symbol_row_sizes"])
def test_row_size_for(symbol, price, size):
    assert mp.row_size_for(symbol, price) == size


def test_a_recorded_symbol_keeps_its_row_size_whatever_the_price():
    # ES around 7,450 sits on default_row_size's step between 2 and 2.5; the table holds it still
    assert {mp.default_row_size(7400.0), mp.default_row_size(7500.0)} == {2.0, 2.5}
    assert mp.row_size_for("/ES", 7400.0) == mp.row_size_for("/ES", 7500.0) == 2.5


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


def test_the_fixture_is_what_its_generator_builds():
    """scripts/make_marketprofile_fixture.py rebuilt in memory equals the committed file: a module change
    that moves any expected answer, or a hand edit of the fixture, fails here first."""
    import importlib.util

    script = Path(__file__).resolve().parents[3] / "scripts" / "make_marketprofile_fixture.py"
    spec = importlib.util.spec_from_file_location("make_marketprofile_fixture", script)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert json.loads(gen.build_text()) == CASES
