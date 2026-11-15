"""Item 80's script (open_vs_prior_value_item80.py): invented sessions only, the store never read."""

from __future__ import annotations

import importlib.util
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "open_vs_prior_value_item80",
    Path(__file__).resolve().parents[1] / "scripts" / "open_vs_prior_value_item80.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ET = ZoneInfo("America/New_York")


def _session(day: date, prices: list[float]) -> list[tuple[float, float]]:
    start = datetime(day.year, day.month, day.day, 9, 31, tzinfo=ET).timestamp()
    return [(start + 60 * i, p) for i, p in enumerate(prices)]


def test_the_rule_is_complete_and_the_chain_is_one_study_row():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["splits"] == ["2025-07-01"] and r["halves"] < r["splits"][0]
    assert all(v is not None for v in r.values())
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_bars_run_from_the_previous_stamp_so_no_row_between_two_is_skipped():
    s = _session(date(2024, 3, 4), [5000.0, 5010.0, 5004.0])
    b = m.bars(s)
    assert (b[0]["o"], b[0]["h"], b[0]["l"]) == (5000.0, 5000.0, 5000.0)
    assert (b[1]["l"], b[1]["h"]) == (5000.0, 5010.0)
    assert (b[2]["l"], b[2]["h"]) == (5004.0, 5010.0)
    assert all(x["t"] % 60 == 0 for x in b)


def test_the_three_classes_against_a_value_band():
    band = (5000.0, 5020.0)
    assert m.classify(5010.0, 5000.0, 5030.0, band) == "inside"
    assert m.classify(5000.0, 4990.0, 5005.0, band) == "inside"  # the band's floor is in it
    assert m.classify(5020.0, 5020.0, 5040.0, band) == "held_outside"  # the band's top is not
    assert m.classify(5030.0, 5025.0, 5040.0, band) == "held_outside"
    assert m.classify(5030.0, 5015.0, 5040.0, band) == "returned"
    assert m.classify(4990.0, 4980.0, 4999.0, band) == "held_outside"
    assert m.classify(4990.0, 4980.0, 5000.0, band) == "returned"


def test_the_value_band_is_val_to_the_top_of_vahs_row():
    # A session that sits at 5000-5002.5 all day: one heavy row, VA that row, band its full height.
    s = _session(date(2024, 3, 4), [5001.0] * 390)
    assert m.value_band(s, 2.5, 0.7) == (5000.0, 5002.5)


def _history_sessions(n: int, seed: int = 1) -> dict:
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


def test_rows_need_22_prior_sessions_and_a_short_session_restarts_the_history():
    sessions = _history_sessions(40)
    rows, dropped = m.build_rows(sessions, m.FORECAST_RULE)
    assert len(rows) == 40 - 22  # the first row is the 23rd session, the first with 22 before it
    assert dropped["history"] == 21  # sessions 2-22 have a prior but too short a history
    short_day = sorted(sessions)[30]
    sessions[short_day] = sessions[short_day][:200]
    rows2, dropped2 = m.build_rows(sessions, m.FORECAST_RULE)
    assert dropped2["short"] == 1
    assert all(r["date"] != short_day for r in rows2)
    assert len(rows2) < len(rows)


def test_a_row_reads_only_the_prior_session_and_the_first_hour_for_its_features():
    sessions = _history_sessions(23)
    days = sorted(sessions)
    rows, _ = m.build_rows(sessions, m.FORECAST_RULE)
    (row,) = rows
    prior = sessions[days[-2]]
    today = sessions[days[-1]]
    assert math.isclose(row["gap"], abs(today[0][1] - prior[-1][1]) / prior[-1][1])
    # Changing the afternoon changes the outcome, never a feature.
    late = [(ts, p + (50.0 if m.minute_of(ts) >= 13 * 60 else 0.0)) for ts, p in today]
    sessions[days[-1]] = late
    (row2,) = m.build_rows(sessions, m.FORECAST_RULE)[0]
    assert row2["y"] != row["y"]
    assert all(row2[c] == row[c] for c in (*m.CONTROLS, "class"))


def _rows(effect: float, n: int = 700, seed: int = 3) -> list[dict]:
    rng = random.Random(seed)
    out = []
    d = date(2022, 6, 1)
    for _ in range(n):
        c = rng.choices(m.CLASSES, [0.6, 0.15, 0.25])[0]
        r = {k: rng.gauss(0, 1) for k in m.CONTROLS}
        r.update({"class": c, "side": "above", "date": d.isoformat()})
        r["y"] = (
            0.4 * r["ln_r22"]
            + 0.2 * r["ln_ib"]
            + (effect if c == "held_outside" else 0.0)
            + rng.gauss(0, 0.3)
        )
        out.append(r)
        d += timedelta(days=2)
    return out


def test_ols_recovers_a_planted_coefficient():
    coef = m.ols(_rows(0.2, n=2000))
    assert abs(coef["held_outside"] - 0.2) < 0.05
    assert abs(coef["ln_r22"] - 0.4) < 0.05


def test_a_planted_effect_passes_the_design_and_reads_the_holdout():
    block = m.judge(_rows(0.25, n=1100), m.FORECAST_RULE, resamples=300)
    assert block["design"]["verdict"] == "pass", block["design"]
    assert block["holdout"]["verdict"] == "pass"
    assert block["verdict"] == {"held_outside": "pass"}


def test_no_effect_fails_the_design_and_never_reads_the_holdout():
    block = m.judge(_rows(0.0, n=1100), m.FORECAST_RULE, resamples=300)
    assert block["design"]["verdict"] == "fail"
    assert block["holdout"]["verdict"] == "not read"
    assert "difference" not in block["holdout"]


def test_too_few_held_outside_days_is_no_verdict():
    rows = [r for r in _rows(0.3, n=1100) if not (r["class"] == "held_outside" and r["date"] < "2025-01-01")]
    block = m.judge(rows, m.FORECAST_RULE, resamples=50)
    assert block["verdict"] == {"held_outside": "no verdict"}


def test_the_fingerprint_holds_counts_and_a_hash_never_a_price():
    fp = m.fingerprint(_history_sessions(3))
    assert set(fp) == {"sessions", "stamps", "first", "last", "sha256"}
    assert fp["sessions"] == 3 and fp["stamps"] == 3 * 389
