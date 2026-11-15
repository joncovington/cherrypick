"""Item 81's script (first_hour_width_item81.py): invented sessions only, the store never read."""

from __future__ import annotations

import importlib.util
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "first_hour_width_item81", Path(__file__).resolve().parents[1] / "scripts" / "first_hour_width_item81.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ET = ZoneInfo("America/New_York")


def _session(day: date, prices: list[float]) -> list[tuple[float, float]]:
    start = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).timestamp()
    return [(start + 60 * i, p) for i, p in enumerate(prices)]


def _sessions(n: int, seed: int = 1) -> dict:
    rng = random.Random(seed)
    out, d, px = {}, date(2023, 1, 2), 4000.0
    while len(out) < n:
        if d.weekday() < 5:
            path = [px]
            for _ in range(388):
                path.append(path[-1] + rng.gauss(0, 1.2))
            out[d.isoformat()] = _session(d, path)
            px = path[-1] + rng.gauss(0, 8)
        d += timedelta(days=1)
    return out


def test_the_rule_is_complete_with_two_signed_tests_and_one_study_row():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["halves"] < r["splits"][0]
    assert {k: v["sign"] for k, v in r["tests"].items()} == {"persistence": 1, "extension": -1}
    assert r["t_bar"] >= 2.24  # Holm across the two at one-sided 0.025 never binds
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_the_first_hour_is_every_stamp_before_1030():
    s = _session(date(2024, 3, 4), [5000.0 + i for i in range(389)])
    assert m.ib_of(s, m.hhmm("10:30")) == (5000.0, 5058.0)  # 09:31..10:29 is 59 stamps


def test_rows_need_22_sessions_of_history_and_20_first_hours():
    rows, dropped = m.build_rows(_sessions(40), m.FORECAST_RULE)
    assert len(rows) == 40 - 22
    assert dropped["history"] == 21


def test_extension_is_a_row_beyond_the_first_hour_after_1030():
    day = date(2024, 3, 4)
    flat = [5000.0] * 59 + [5001.0] * 330  # never leaves 5000-5001 + a row
    rows = m.day_row(
        day.isoformat(), _session(day, flat), 5000.0, [0.01] * 22, [0.002] * 20,
        ib_end=m.hhmm("10:30"), outcome_from=m.hhmm("10:30"), extension_points=2.5, median_n=20,
    )  # fmt: skip
    assert rows is None  # a zero-width first hour has no log width
    move = [5000.0 + (i % 3) for i in range(59)] + [5000.0] * 300 + [5005.0] * 30
    row = m.day_row(
        day.isoformat(), _session(day, move), 5000.0, [0.01] * 22, [0.002] * 20,
        ib_end=m.hhmm("10:30"), outcome_from=m.hhmm("10:30"), extension_points=2.5, median_n=20,
    )  # fmt: skip
    assert row["ext"] == 1.0  # 5005 >= 5002 + 2.5
    assert math.isclose(row["w"], math.log((2.0 / 5000.0) / 0.002))
    row2 = m.day_row(
        day.isoformat(), _session(day, move), 5000.0, [0.01] * 22, [0.002] * 20,
        ib_end=m.hhmm("10:30"), outcome_from=m.hhmm("10:30"), extension_points=5.0, median_n=20,
    )  # fmt: skip
    assert row2["ext"] == 0.0  # 5005 < 5002 + 5


def _rows(b_y: float, b_ext: float, n: int = 1100, seed: int = 4) -> list[dict]:
    rng = random.Random(seed)
    out, d = [], date(2022, 6, 1)
    for _ in range(n):
        r = {k: rng.gauss(0, 1) for k in m.CONTROLS}
        r["w"] = rng.gauss(0, 0.4)
        r["date"] = d.isoformat()
        r["y"] = 0.4 * r["ln_r22"] + b_y * r["w"] + rng.gauss(0, 0.3)
        p = min(0.95, max(0.05, 0.6 + b_ext * r["w"]))
        r["ext"] = 1.0 if rng.random() < p else 0.0
        out.append(r)
        d += timedelta(days=2)
    return out


def test_planted_effects_pass_both_tests_with_their_signs():
    block = m.judge(_rows(0.3, -0.25), m.FORECAST_RULE, resamples=300)
    assert block["verdict"] == {"persistence": "pass", "extension": "pass"}, block["tests"]


def test_the_wrong_sign_fails_and_never_reads_the_holdout():
    block = m.judge(_rows(-0.3, 0.25), m.FORECAST_RULE, resamples=300)
    assert block["verdict"] == {"persistence": "fail", "extension": "fail"}
    assert all(t["holdout"]["verdict"] == "not read" for t in block["tests"].values())


def test_too_few_design_days_is_no_verdict():
    rows = [r for r in _rows(0.3, -0.25) if r["date"] >= "2024-06-01"]
    block = m.judge(rows, m.FORECAST_RULE, resamples=50)
    assert block["verdict"]["persistence"] == "no verdict"


def test_the_fingerprint_holds_counts_and_a_hash_never_a_price():
    fp = m.fingerprint(_sessions(3))
    assert set(fp) == {"sessions", "stamps", "first", "last", "sha256"}
    assert fp["stamps"] == 3 * 389
