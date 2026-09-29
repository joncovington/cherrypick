"""The level-selection measurements, each on bars built so the answer is known by hand."""

from __future__ import annotations

from cherrypick.technicals import level_selection as ls
from cherrypick.technicals.levels import Grid


def test_crossings_count_the_bars_whose_range_holds_the_price():
    highs, lows = [12.0, 11.0, 15.0], [10.0, 10.0, 11.0]
    assert ls.crossings(highs, lows, [10.0, 11.0, 12.0, 14.0]) == [2, 3, 2, 1]


def test_a_local_min_is_judged_within_the_width_only():
    profile = [5, 1, 5, 0, 5]
    assert ls.is_local_min(profile, 1, 1)
    assert not ls.is_local_min(profile, 1, 2)  # the 0 two steps away is inside the wider window


def test_swing_highs_beat_two_bars_either_side():
    assert ls.swing_highs([1, 2, 5, 3, 2, 4, 6, 9, 1, 1]) == [2, 7]
    assert ls.swing_highs([1, 5, 5, 1, 1]) == []  # an equal neighbour is not beaten


def test_percentile_splits_ties_and_places_extremes_at_the_ends():
    assert ls.percentile([1, 2, 3], 1) == 0.0
    assert ls.percentile([1, 2, 3], 3) == 1.0
    assert ls.percentile([2, 2, 2], 2) == 0.5


def _bars():
    """Ten sessions on a grid 100..108.5, step 1. Price lives in 100-104, then one session (index 5,
    a swing high at 108.5) gaps up through 105, and price stays in 106-108 after. So 105 is crossed by
    that bar alone -- the least-crossed point on the grid."""
    highs = [104.0, 104.0, 104.0, 103.0, 104.0, 108.5, 108.0, 107.0, 108.0, 108.0]
    lows = [100.0, 100.0, 100.0, 100.0, 100.0, 104.5, 106.0, 106.0, 106.0, 106.0]
    closes = [(h + lo) / 2 for h, lo in zip(highs, lows, strict=True)]
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    return dates, highs, lows, closes, [1.0] * 10


GRID = Grid(100.0, 108.5, 1.0)


def test_a_level_in_the_gap_reads_as_sparse_and_the_least_crossed_rule_finds_it():
    dates, highs, lows, closes, vols = _bars()
    t = ls.Tally()
    ls.measure(dates, highs, lows, closes, vols, GRID, [(105.0, "2026-01-06")], t)
    s = ls.summary(t)
    assert s["interior_levels"] == 1 and s["dated_levels"] == 1
    assert s["crossing_percentile"]["levels"] == 0.0  # the least-crossed point on the grid
    assert s["local_min_of_crossings"]["+-5"]["levels"] == 1.0
    assert s["dated_bar_is_swing_high"] == 1.0
    rules = s["price_given_date"]
    assert rules["least-crossed grid point near the bar"]["exact"] == 1.0
    assert rules["bar high, nearest grid point"]["exact"] == 0.0  # 108.5 rounds to 108
    assert rules["bar high, next grid point up"]["exact"] == 0.0  # and up to 109
    # chance is one over the candidates near the bar, not a constant
    assert rules["chance_exact"] == round(1 / rules["candidates_per_level"], 3)


def test_the_window_extremes_are_not_interior_levels():
    dates, highs, lows, closes, vols = _bars()
    t = ls.Tally()
    ls.measure(dates, highs, lows, closes, vols, GRID, [(100.0, "2026-01-01"), (108.5, "2026-01-06")], t)
    assert t.interior == 0 and t.dated == 0


def test_a_level_dated_outside_the_window_counts_for_the_profile_but_not_the_price_rules():
    dates, highs, lows, closes, vols = _bars()
    t = ls.Tally()
    ls.measure(dates, highs, lows, closes, vols, GRID, [(105.0, "2025-06-01")], t)
    assert t.interior == 1 and t.dated == 0 and t.price_rules == {}
