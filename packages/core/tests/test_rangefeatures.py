"""cherrypick.core.rangefeatures: the declared study (docs/range-features.md).

The first test is the guard the declaration requires before any statistic is read: every feature
computed on the full history must equal the same feature computed on bars cut off at that session.
"""

import math
import random
from datetime import date, timedelta

import pytest

from cherrypick.core import calendar as cal
from cherrypick.core import rangefeatures as rf


def _sessions(start: str, end: str) -> list[str]:
    out, d = [], date.fromisoformat(start)
    while d <= date.fromisoformat(end):
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _walk(sessions, *, seed=7, start=3800.0, plant_nr7=None):
    """A random walk of daily bars. `plant_nr7` multiplies the range of the session after every
    NR7 day, so a test can check the study finds an effect that is really there."""
    rng = random.Random(seed)
    bars, close = [], start
    for s in sessions:
        width = close * rng.uniform(0.004, 0.02)
        if plant_nr7 and len(bars) >= 7:
            prior = [b["high"] - b["low"] for b in bars[-7:-1]]
            last = bars[-1]["high"] - bars[-1]["low"]
            if last < min(prior):
                width *= plant_nr7
        low = close * (1 + rng.gauss(0, 0.004)) - width * rng.uniform(0.2, 0.8)
        high = low + width
        new_close = rng.uniform(low, high)
        bars.append({"session": s, "open": close, "high": high, "low": low, "close": new_close})
        close = new_close
    return bars


def test_features_are_truncation_invariant():
    """The declaration's guard: no feature may read a bar after its own session. A rolling window
    that includes the next bar, or a condition that reads one ahead, makes the full-history value
    differ from the value computed on bars cut off at that session."""
    bars = _walk(_sessions("2024-01-02", "2024-08-30"), seed=3)
    bars[50]["high"] = bars[50]["low"]  # a zero-width bar: undefined features must agree too
    full = rf.feature_series(bars)
    r = rf.ranges(bars)
    for k in range(len(bars)):
        assert rf.feature_series(bars[: k + 1])[k] == full[k], bars[k]["session"]
        assert rf.har_regressors(rf.ranges(bars[: k + 1]), k) == rf.har_regressors(r, k)


def test_feature_definitions_on_hand_built_bars():
    base = [{"session": f"d{i}", "open": 100, "high": 102, "low": 98, "close": 100} for i in range(25)]
    narrow = {"session": "d25", "open": 100, "high": 100.5, "low": 99.5, "close": 100.5}
    tie = {"session": "d25", "open": 100, "high": 102, "low": 98, "close": 101}
    flat = {"session": "d25", "open": 100, "high": 100, "low": 100, "close": 100}

    got = rf.feature_series([*base, narrow])[-1]
    assert got["nr7"] == 1
    assert got["close_extremity"] == 1.0  # closed on its high
    assert rf.feature_series([*base, tie])[-1]["nr7"] == 0  # equal width is not narrower
    assert rf.feature_series([*base, tie])[-1]["close_extremity"] == 0.5
    assert rf.feature_series([*base, flat])[-1]["close_extremity"] is None
    # Channel: 20 sessions spanning 98..102, close 100.5 -> |2 * 2.5 / 4 - 1| = 0.25.
    assert rf.feature_series([*base, narrow])[-1]["channel_extremity"] == pytest.approx(0.25)
    # Round distance: close 100.5 is 0.5 from 100; ATR20 over the window = (19 * 4 + 1) / 20.
    assert rf.feature_series([*base, narrow])[-1]["round_distance"] == pytest.approx(0.5 / (77 / 20))
    assert rf.feature_series(base[:5])[-1]["nr7"] is None  # the window is not full yet


def test_outcome_reads_exactly_the_next_h_sessions():
    bars = [{"session": str(i), "open": 1, "high": 10 + i, "low": 10 - i, "close": 10} for i in range(6)]
    assert rf.outcome(bars, 1, 2) == pytest.approx(math.log((13 - 7) / 10))
    assert rf.outcome(bars, 4, 2) is None


def test_ols_recovers_a_noiseless_fit():
    rows = [[x, x * x % 7] for x in range(1, 30)]
    ys = [1.5 + 2.0 * a - 0.5 * b for a, b in rows]
    assert rf.ols(rows, ys) == pytest.approx([1.5, 2.0, -0.5])


def test_the_baseline_never_reads_an_outcome_past_calibration():
    """Changing every bar after CALIBRATION_THROUGH must not move the fitted baseline."""
    bars = _walk(_sessions("2021-02-16", "2023-03-31"))
    before = rf.fit_baseline(bars, 7)
    for b in bars:
        if b["session"] > rf.CALIBRATION_THROUGH:
            b["high"] *= 1.5
    assert rf.fit_baseline(bars, 7)["coefficients"] == before["coefficients"]


def test_groups_follow_the_tercile_cuts():
    cuts = rf.tercile_cuts([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert rf.group("close_extremity", cuts[0], cuts) == 0
    assert rf.group("close_extremity", cuts[1], cuts) is None
    assert rf.group("close_extremity", cuts[1] + 0.01, cuts) == 1
    assert rf.group("nr7", 1, None) == 1


def test_bootstrap_is_deterministic(monkeypatch):
    monkeypatch.setattr(rf, "RESAMPLES", 300)
    rng = random.Random(1)
    values = [rng.gauss(0, 1) for _ in range(200)]
    groups = [rng.choice([0, 1, None]) for _ in range(200)]
    assert rf.bootstrap(values, groups, 5) == rf.bootstrap(values, groups, 5)


def test_study_finds_a_planted_effect_and_ignores_later_bars(monkeypatch):
    """The machinery must be able to fire: double the next session's range after every NR7 day and
    nr7 at one session has to pass. Bars after the evaluation end must not move any evaluation
    figure."""
    monkeypatch.setattr(rf, "RESAMPLES", 500)
    sessions = _sessions("2021-02-16", "2026-10-02")
    planted = _walk(sessions, seed=11, plant_nr7=2.0)
    result = rf.study(planted)
    nr7_1 = next(t for t in result["tests"] if t["feature"] == "nr7" and t["horizon"] == 1)
    assert nr7_1["passes_evaluation"], nr7_1
    assert len(result["tests"]) == 12
    assert result["bars"]["last_used_for_evaluation"] <= rf.EVALUATION_THROUGH

    extended = planted + _walk(_sessions("2026-10-05", "2027-01-29"), seed=12, start=planted[-1]["close"])
    again = rf.study(extended)

    # `forward` and the `finding` it decides are the only fields more bars are allowed to change.
    def strip(doc):
        return [{k: v for k, v in t.items() if k not in ("forward", "finding")} for t in doc["tests"]]

    assert strip(again) == strip(result)
    assert next(t for t in again["tests"] if t["feature"] == "nr7")["forward"]["sessions"] >= 60
