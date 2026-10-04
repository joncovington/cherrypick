"""Our own support and resistance: confirmed swing points, nearest first, no vendor data."""

from __future__ import annotations

from cherrypick.technicals import swings


def _bars(highs, lows):
    return [f"2026-01-{i + 1:02d}" if i < 31 else f"d{i}" for i in range(len(highs))], highs, lows


def test_a_swing_needs_the_bars_on_both_sides_so_the_newest_cannot_be_one():
    highs = [10.0] * 30
    highs[12] = 15.0  # a peak with ten lower bars either side
    highs[25] = 16.0  # higher, but only four bars after it: not yet a swing
    assert swings.swing_highs(highs, 10) == [12]
    lows = [10.0] * 30
    lows[14] = 5.0
    assert swings.swing_lows(lows, 10) == [14]


def test_the_two_nearest_each_side_with_near_duplicates_merged():
    n = 120
    highs, lows = [100.0] * n, [99.0] * n
    for i, v in ((15, 130.0), (40, 112.0), (65, 112.5), (90, 120.0)):  # 112.5 is within 1% of 112
        highs[i] = v
    for i, v in ((20, 80.0), (50, 90.0), (75, 95.0)):
        lows[i] = v
    dates, h, lo = _bars(highs, lows)
    got = swings.levels(dates, h, lo, close=100.0, window=250)
    assert [(x["kind"], x["value"]) for x in got] == [
        ("resistance", 112.0),
        ("resistance", 120.0),
        ("support", 95.0),
        ("support", 90.0),
    ]
    assert got[0]["date"] == dates[40]


def test_only_the_window_counts_and_a_level_on_the_wrong_side_is_not_turned_round():
    n = 300
    highs, lows = [100.0] * n, [99.0] * n
    highs[20] = 140.0  # outside the last 250 sessions
    for i in range(185, 216):  # a real swing low at 104 ABOVE the close, its neighbours higher
        lows[i], highs[i] = 106.0, 107.0
    lows[200] = 104.0  # not resistance: a broken support is not turned into its opposite
    lows[150] = 101.0
    highs[180] = 103.0
    lows[100] = 98.0
    dates, h, lo = _bars(highs, lows)
    got = swings.levels(dates, h, lo, close=102.0, window=250)
    assert ("resistance", 140.0) not in [(x["kind"], x["value"]) for x in got]
    assert 104.0 not in [x["value"] for x in got]
    assert all(x["kind"] == "support" for x in got if x["value"] < 102.0)
    assert all(x["kind"] == "resistance" for x in got if x["value"] > 102.0)
