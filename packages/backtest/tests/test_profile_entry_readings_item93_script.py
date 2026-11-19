"""Item 93's script (profile_entry_readings_item93.py): invented sessions only, the store never read."""

from __future__ import annotations

import importlib.util
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "profile_entry_readings_item93",
    Path(__file__).resolve().parents[1] / "scripts" / "profile_entry_readings_item93.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ET = ZoneInfo("America/New_York")
RULE = m.FORECAST_RULE


def _session(day: date, prices: list[float]) -> list[tuple[float, float]]:
    start = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).timestamp()
    return [(start + 60 * i, p) for i, p in enumerate(prices)]


def _minutes(prices: list[float]) -> list[tuple[int, float]]:
    return [(9 * 60 + 31 + i, p) for i, p in enumerate(prices)]


def _sessions(n: int, seed: int = 1, start: date = date(2023, 1, 2)) -> dict:
    rng = random.Random(seed)
    out, d, px = {}, start, 4000.0
    while len(out) < n:
        if d.weekday() < 5:
            path = [px]
            for _ in range(388):
                path.append(path[-1] + rng.gauss(0, 1.2))
            out[d.isoformat()] = _session(d, path)
            px = path[-1] + rng.gauss(0, 8)
        d += timedelta(days=1)
    return out


def test_the_rule_is_complete_with_four_signed_tests_and_one_study_row():
    assert RULE["kind"] == "forecast" and RULE["halves"] < RULE["splits"][0]
    assert {k: v["sign"] for k, v in RULE["tests"].items()} == {
        "balance": -1,
        "acceptance": 1,
        "rejection": 1,
        "unfinished": 1,
    }
    assert RULE["t_bar"] >= 2.24  # Bonferroni across four at one-sided 0.05 never binds
    assert [s["step"] for s in m.CHAIN] == ["study"]
    # acceptance ends by 13:00 (G), rejection by 12:30 (F), as the plan declares
    assert RULE["tests"]["acceptance"]["last_period"] == 6 and RULE["tests"]["rejection"]["last_period"] == 5


def test_periods_are_thirty_minutes_from_the_open():
    p = m.periods_of(_minutes([1.0] * 389))
    assert len(p[0]) == 29 and len(p[1]) == 30  # A is 09:31..09:59, B 10:00..10:29
    assert max(p) == 12 and len(p[12]) == 30  # M ends at 15:59; the 16:00 stamp is in none


def test_period_ends_read_only_what_follows_the_entry():
    prices = [100.0] * 59 + [110.0] + [100.0] * 329  # 10:30 stamp is index 59, the end of B is 58
    ends = m.period_ends(_minutes(prices), m.periods_of(_minutes(prices)))
    assert math.isclose(ends[1]["up"], math.log(110 / 100))
    assert ends[2]["up"] == 0.0 and ends[1]["ret"] == 0.0


def _day(c_d: float, rest: float = 100.0) -> list[float]:
    """A and B at 100, C and D at c_d, the rest at `rest`."""
    return [100.0] * 59 + [c_d] * 60 + [rest] * 270


def test_acceptance_needs_two_periods_wholly_beyond_from_c():
    band = (95.0, 105.0)
    mins = _minutes(_day(110.0))
    assert m.first_acceptance(mins, m.periods_of(mins), band, 2, 6) == (3, 1)
    mins = _minutes(_day(90.0))
    assert m.first_acceptance(mins, m.periods_of(mins), band, 2, 6) == (3, -1)
    one = _minutes([100.0] * 59 + [110.0] * 30 + [100.0] * 300)  # only C beyond
    assert m.first_acceptance(one, m.periods_of(one), band, 2, 6) is None
    early = _minutes([110.0] * 59 + [100.0] * 330)  # A and B beyond: before the first period allowed
    assert m.first_acceptance(early, m.periods_of(early), band, 2, 6) is None


def test_rejection_needs_an_open_outside_then_two_periods_inside():
    band = (95.0, 105.0)
    mins = _minutes([110.0] + [100.0] * 388)  # A holds the 110 open, so B and C are the first pair
    assert m.first_rejection(mins, m.periods_of(mins), band, 110.0, 0, 5) == (2, -1)
    mins = _minutes([110.0] * 59 + [100.0] * 330)  # A and B outside, C and D inside
    assert m.first_rejection(mins, m.periods_of(mins), band, 110.0, 0, 5) == (3, -1)
    assert m.first_rejection(mins, m.periods_of(mins), band, 100.0, 0, 5) is None  # opened inside
    late = _minutes([110.0] * 179 + [100.0] * 210)  # back inside only from G: after F, too late
    assert m.first_rejection(late, m.periods_of(late), band, 110.0, 0, 5) is None


def test_levels_are_poor_extremes_and_the_middle_of_each_single_print_run():
    prof = SimpleNamespace(
        poor_high=True, high=210.0, poor_low=False, low=90.0, single_prints=(100.0, 102.5, 105.0, 200.0)
    )
    assert m.levels_of(prof, 2.5) == [
        ("poor_high", 210.0),
        ("single_prints", 103.75),
        ("single_prints", 201.25),
    ]


def test_an_unfinished_level_counts_against_the_same_distance_on_other_days():
    wins = [
        {"date": "2023-01-02", "hi": 0.02, "lo": -0.01, "levels": [{"kind": "poor_high", "x": 0.015}]},
        {"date": "2023-01-03", "hi": 0.005, "lo": -0.01, "levels": []},
        {"date": "2023-01-04", "hi": 0.03, "lo": -0.01, "levels": []},
    ]
    (row,) = m.unfinished_diffs(wins)
    assert math.isclose(row["diff"], 1.0 - 0.5)  # touched; one of the two others reached 1.5%


def test_matched_baselines_come_from_non_event_days_only():
    ends = {3: {"p": 100.0, "up": 0.0, "dn": 0.0, "ret": 0.01}}
    pool = [
        {"date": "a", "ends": ends, "event": True},
        {"date": "b", "ends": {3: {**ends[3], "ret": -0.02}}, "event": False},
    ]
    (row,) = m.acceptance_diffs([{"date": "a", "k": 3, "side": 1, "outcome": 0.01}], pool)
    assert math.isclose(row["diff"], 0.01 - (-0.02))


def _diffs(mean: float, n: int = 300, seed: int = 3, start: date = date(2022, 6, 1)) -> list[dict]:
    rng = random.Random(seed)
    return [
        {"date": (start + timedelta(days=4 * i)).isoformat(), "diff": mean + rng.gauss(0, 0.2)}
        for i in range(n)
    ]


def test_a_planted_matched_effect_passes_and_the_wrong_sign_fails_unread():
    diffs = m.by_part(_diffs(0.15), RULE)
    assert m.judge_matched(diffs, RULE, "rejection", resamples=300)["verdict"] == "pass"
    bad = m.judge_matched(m.by_part(_diffs(-0.15), RULE), RULE, "rejection", resamples=300)
    assert bad["verdict"] == "fail" and bad["holdout"]["verdict"] == "not read"
    few = m.by_part(_diffs(0.15, n=30), RULE)
    assert m.judge_matched(few, RULE, "rejection", resamples=50)["verdict"] == "no verdict"


def _balance_rows(b: float, n: int = 1100, seed: int = 4) -> list[dict]:
    rng = random.Random(seed)
    out, d = [], date(2022, 6, 1)
    for _ in range(n):
        r = {k: rng.gauss(0, 1) for k in m.E1_CONTROLS}
        r["date"] = d.isoformat()
        r["signal"] = 1.0 if rng.random() < 0.15 else 0.0
        r["y"] = 0.4 * r["ln_r22"] + 0.3 * r["w"] + b * r["signal"] + rng.gauss(0, 0.3)
        out.append(r)
        d += timedelta(days=2)
    return out


def test_balance_passes_a_planted_narrowing_and_fails_none():
    assert m.judge_balance(_balance_rows(-0.3), RULE, resamples=200)["verdict"] == "pass"
    assert m.judge_balance(_balance_rows(0.0), RULE, resamples=200)["verdict"] == "fail"


def test_a_gap_unlinks_the_next_session_and_restarts_its_histories():
    s = _sessions(40)
    keys = sorted(s)
    for k in keys[10:17]:  # seven weekdays gone: nine calendar days between the sessions either side
        del s[k]
    days, dropped = m.build_days(s, RULE)
    assert dropped["gap"] == 1
    after = [d for d in days if d["date"] == keys[17]][0]
    assert after["linked"] is False and "har" not in after


def test_the_whole_run_judges_four_tests_on_invented_sessions():
    days, _ = m.build_days(_sessions(60, start=date(2025, 4, 1)), RULE)
    block = m.judge(days, RULE, resamples=20)
    assert set(block["verdict"]) == set(RULE["tests"])
    assert all(v in ("pass", "fail", "no verdict") for v in block["verdict"].values())


def test_the_fingerprint_holds_counts_and_a_hash_never_a_price():
    fp = m.fingerprint(_sessions(3))
    assert set(fp) == {"sessions", "stamps", "first", "last", "sha256"}
    assert fp["stamps"] == 3 * 389


def test_the_holdout_report_reads_only_the_holdout_and_judges_nothing():
    days, _ = m.build_days(_sessions(80, start=date(2025, 3, 3)), RULE)
    rep = m.holdout_report(days, RULE, resamples=20)
    assert set(rep) == set(RULE["tests"])
    assert all("verdict" not in t for t in rep.values())
    design_only = [d for d in days if d["date"] < RULE["splits"][0]]
    empty = m.holdout_report(design_only, RULE, resamples=20)
    assert all(t["events"] == 0 and t["effect"] is None for t in empty.values())
