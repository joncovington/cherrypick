"""The opening range: bucketing, frozen feature definitions, refusals and the study's floors.

Every assertion was confirmed to fail before the module existed, or against the specific mistake it
names. The two that carry the most weight are the refusal of an incomplete window (a range measured
over four of six buckets is a different measure wearing the same name) and the study's session
floor (a cell under it must report `not_yet_readable`, never a difference).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import time
from zoneinfo import ZoneInfo

from cherrypick.core import openingrange as orange
from cherrypick.core import streamcache

ET = ZoneInfo("America/New_York")


def _ts(session: str, hh: int, mm: int, ss: int = 0) -> float:
    day = _dt.date.fromisoformat(session)
    return _dt.datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=ET).timestamp()


def _trail(session: str, prices_by_minute: dict[int, list[float]]) -> list[tuple[float, float]]:
    out = []
    for minute, prices in sorted(prices_by_minute.items()):
        for index, price in enumerate(prices):
            out.append((_ts(session, minute // 60, minute % 60, index * 5), price))
    return out


def _full_window(session: str, prices: list[float], *, per_bucket: int = 2) -> list[tuple[float, float]]:
    """Six 5-minute buckets from 09:30, `per_bucket` ticks each, walking `prices`."""
    rows = []
    for bucket in range(6):
        minute = orange.OPEN_MIN + bucket * 5
        for tick in range(per_bucket):
            price = prices[(bucket * per_bucket + tick) % len(prices)]
            rows.append((_ts(session, minute // 60, minute % 60, tick * 10), price))
    return rows


# --------------------------------------------------------------------------- bucketing
def test_bars_are_five_minute_buckets_on_et_boundaries():
    bars = orange.bars_from_trail(_full_window("2026-09-21", [100.0, 101.0]))
    assert [b["bucket"] for b in bars] == [0, 1, 2, 3, 4, 5]
    assert [b["minute"] for b in bars] == [570, 575, 580, 585, 590, 595]


def test_ticks_outside_the_window_are_ignored():
    session = "2026-09-21"
    rows = _full_window(session, [100.0])
    rows.append((_ts(session, 10, 5), 999.0))  # after the entry window opens
    rows.append((_ts(session, 9, 15), 1.0))  # before the bell
    bars = orange.bars_from_trail(rows)
    assert len(bars) == 6
    assert max(b["high"] for b in bars) == 100.0


def test_a_missing_bucket_is_absent_and_then_refused_not_interpolated():
    """Shown to fail against a reader that filled the hole: a range over five of six buckets is a
    different measure, and reporting it as the opening range would be silently wrong."""
    session = "2026-09-21"
    rows = [r for r in _full_window(session, [100.0, 102.0]) if orange._et_minute(r[0]) < 590]
    bars = orange.bars_from_trail(rows)
    assert len(bars) == 4
    got = orange.features(bars, atr=10.0)
    assert got["status"] == "unmeasured" and "4 of 6" in got["reason"]
    assert "or_points" not in got


# --------------------------------------------------------------------------- features
def test_features_are_the_frozen_definitions():
    session = "2026-09-21"
    # a clean staircase up: every bucket closes higher than the last
    rows = []
    for bucket in range(6):
        minute = orange.OPEN_MIN + bucket * 5
        rows.append((_ts(session, minute // 60, minute % 60, 0), 100.0 + bucket))
    got = orange.features(orange.bars_from_trail(rows), atr=10.0, prior_close=99.0, prior_range=20.0)
    assert got["status"] == "measured"
    assert got["high"] == 105.0 and got["low"] == 100.0
    assert got["or_points"] == 5.0
    assert got["or_atr"] == 0.5  # 5 points / ATR 10
    assert got["position"] == 1.0  # finished the window on its high
    assert got["efficiency"] == 1.0  # a straight line: net == path
    assert got["gap_atr"] == 0.1  # opened 1.0 above the prior close, / ATR 10
    assert got["or_vs_prior"] == 0.25  # 5 points against a 20-point prior range


def test_a_round_trip_is_inefficient_even_though_its_range_is_wide():
    """Efficiency separates travel from displacement -- the thing that tells a chop day from a
    trend day when both have the same range."""
    session = "2026-09-21"
    rows = []
    for bucket, price in enumerate([100.0, 103.0, 106.0, 103.0, 100.0, 100.0]):
        minute = orange.OPEN_MIN + bucket * 5
        rows.append((_ts(session, minute // 60, minute % 60, 0), price))
    got = orange.features(orange.bars_from_trail(rows), atr=10.0)
    assert got["or_points"] == 6.0
    assert got["efficiency"] == 0.0  # ends where it started
    assert got["position"] == 0.0


def test_a_missing_atr_costs_the_normalised_features_only():
    got = orange.features(orange.bars_from_trail(_full_window("2026-09-21", [100.0, 104.0])), atr=None)
    assert got["status"] == "measured"
    assert got["or_points"] == 4.0
    assert got["or_atr"] is None and got["gap_atr"] is None


# --------------------------------------------------------------------------- outcome
def test_the_outcome_measures_only_what_happened_after_the_entry_window_opened():
    """No look-ahead in reverse: the opening window must not leak into its own outcome."""
    session = "2026-09-21"
    rows = _full_window(session, [100.0, 130.0])  # a violent open
    rows += [(_ts(session, 11, 0), 100.0), (_ts(session, 14, 0), 110.0)]
    got = orange.outcome(rows, atr=10.0)
    assert got["rest_of_day_points"] == 10.0, "the 30-point opening swing is not part of the outcome"
    assert got["rest_of_day_range_atr"] == 1.0


def test_no_ticks_after_ten_is_unmeasured_not_zero_travel():
    got = orange.outcome(_full_window("2026-09-21", [100.0]), atr=10.0)
    assert got["status"] == "unmeasured"
    assert "rest_of_day_points" not in got


# --------------------------------------------------------------------------- against the stores
def _stores(tmp_path, sessions):
    cache = streamcache.connect(tmp_path / "cache.db")
    day = _dt.date(2026, 6, 1)
    close = 100.0
    while day < _dt.date.fromisoformat(min(sessions)):
        if day.weekday() < 5:
            cache.execute(
                "INSERT INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
                "day_close, prev_day_close, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                ("SPX", day.isoformat(), close, close + 5, close - 5, close, close - 1, time.time()),
            )
        day += _dt.timedelta(days=1)
    cache.commit()
    spot = sqlite3.connect(tmp_path / "gex.db")
    spot.execute("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)")
    for session in sessions:
        rows = _full_window(session, [100.0, 103.0])
        rows.append((_ts(session, 13, 0), 107.0))
        spot.executemany(
            "INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)",
            [(session, ts, price) for ts, price in rows],
        )
    spot.commit()
    return spot, cache


def test_build_joins_the_trail_the_daily_context_and_the_regime(tmp_path):
    spot, cache = _stores(tmp_path, ["2026-09-21"])
    got = orange.build(spot, cache, session="2026-09-21")
    assert got["status"] == "measured"
    assert got["opening"]["or_points"] == 3.0
    assert got["outcome"]["status"] == "measured"
    assert got["atr"] is not None, "ATR comes from bars strictly before the session"


def test_a_session_the_recorder_missed_is_unmeasured_and_kept(tmp_path):
    """A session with no trail is a fact about coverage. Dropping it would make the sample look
    more complete than it is -- the 2026-07-23 and 2026-08-17 gaps are real."""
    spot, cache = _stores(tmp_path, ["2026-09-21"])
    rows = orange.series(spot, cache, sessions=["2026-09-21", "2026-09-22"])
    assert [r["status"] for r in rows] == ["measured", "unmeasured"]
    assert "no spot trail" in rows[1]["reason"]


# --------------------------------------------------------------------------- the study's floors
def _row(session, value, result, regime="uptrend/low", status="measured"):
    return {
        "session": session,
        "status": status,
        "opening": {"or_atr": value},
        "outcome": {"rest_of_day_range_atr": result},
        "regime": regime,
    }


def test_a_cell_under_the_session_floor_reports_not_yet_readable_never_a_difference():
    """The whole point of the floor. Shown to fail against a study that reported the split anyway:
    eight sessions a side looks like a finding and is not."""
    rows = [_row(f"2026-10-{d:02d}", float(d), float(d) * 2) for d in range(1, 11)]
    got = orange.study(rows)
    assert got["headline"]["readable"] is False
    assert got["headline"]["not_yet_readable"] is True
    assert "observed_diff" not in got["headline"]


def test_a_readable_split_reports_the_difference_and_the_paired_sessions():
    rows = [_row(f"2026-10-{d:02d}", float(d), 1.0 if d <= 15 else 2.0) for d in range(1, 31)]
    got = orange.study(rows)
    assert got["headline"]["readable"] is True
    assert got["headline"]["observed_diff"] == 1.0
    assert len(got["headline"]["paired"]["high"]) >= orange.MIN_SESSIONS
    assert got["checkpoint_ready"] is True


def test_in_sample_sessions_are_reported_apart_and_never_pooled():
    """The declared holdout. The 38 sessions that chose these features cannot also test them."""
    rows = [_row(f"2026-09-{d:02d}", float(d), float(d)) for d in range(1, 21)]
    rows += [_row(f"2026-10-{d:02d}", float(d), float(d)) for d in range(1, 21)]
    got = orange.study(rows, in_sample_through="2026-09-30")
    assert got["coverage"]["in_sample"] == 20
    assert got["coverage"]["out_of_sample"] == 20
    assert got["headline"]["sessions"] == 20, "the headline reads out-of-sample only"


def test_unmeasured_sessions_are_counted_not_silently_dropped():
    rows = [_row(f"2026-10-{d:02d}", float(d), float(d)) for d in range(1, 6)]
    rows.append({"session": "2026-10-06", "status": "unmeasured", "reason": "no spot trail"})
    got = orange.study(rows)
    assert got["coverage"]["unmeasured"] == 1
    assert got["coverage"]["usable_sessions"] == 5


def test_the_conditioner_splits_each_regime_on_its_own_floor():
    rows = [_row(f"2026-10-{d:02d}", float(d), float(d), regime="uptrend/low") for d in range(1, 11)]
    rows += [_row(f"2026-11-{d:02d}", float(d), float(d), regime="range/high") for d in range(1, 11)]
    got = orange.study(rows)
    assert set(got["by_regime"]) == {"uptrend/low", "range/high"}
    assert all(cell["readable"] is False for cell in got["by_regime"].values())
