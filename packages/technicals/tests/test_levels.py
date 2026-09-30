"""The level grid and the rank, each detail pinned by the case that found it."""

from __future__ import annotations

import math

import pytest

from cherrypick.technicals import levels


def test_the_step_is_the_nice_number_nearest_by_ratio():
    """ADI: range/100 is 2.24 -- nearer 2.00 by difference, 2.50 by ratio -- and its levels are 2.50
    apart."""
    assert levels.nice_step(2.24) == 2.5
    assert levels.nice_step(1.004) == 1.0  # ANET
    assert levels.nice_step(2.007) == 2.0  # MSFT
    assert levels.nice_step(15.678) == 20.0  # AZO: 15.7 is nearer 20 than 10 by ratio


def test_the_grid_is_anchored_at_the_window_low_and_its_extremes_are_unsnapped():
    """ANET 2026-09-25: low 114.52, high 214.89, levels 202.52 / 197.52 / 179.52 / 165.52 / 158.52."""
    highs = [150.0] * 248 + [214.89, 150.0]
    lows = [140.0] * 248 + [114.52, 140.0]
    g = levels.grid(highs, lows)
    assert (g.low, g.high, g.step) == (114.52, 214.89, 1.0)
    for level in (202.52, 197.52, 179.52, 165.52, 158.52, 114.52, 214.89):
        assert g.explains(level), level
    assert not g.explains(202.00)
    assert g.snap(203.76) == 203.52


def test_the_window_is_250_sessions_not_252():
    """ATI's, CSCO's and STLD's lows sat on the 252nd session; the grid fitted once they fell out."""
    highs = [100.0] * 260
    lows = [90.0] * 260
    lows[-251] = 80.0  # 251 sessions back: outside a 250 window
    assert levels.grid(highs, lows).low == 90.0
    lows[-250] = 85.0  # 250 sessions back: inside
    assert levels.grid(highs, lows).low == 85.0


def test_too_few_bars_is_no_grid():
    assert levels.grid([1.0] * 100, [0.5] * 100) is None


def test_the_rank_is_the_decile_of_the_return_percentile():
    returns = {f"S{i}": i / 100 for i in range(1, 101)}
    r = levels.rank(returns)
    assert r["S100"] == 10 and r["S1"] == 1 and r["S50"] == 5 and r["S51"] == 6


def test_a_gap_gives_its_top_edge_as_support_and_its_bottom_as_resistance_either_way():
    """AMD 2025-10-03/06: a gap up from a 170.68 high to a 203.01 low -- resistance 170.68 dated on the
    bar before, support 203.01 on the bar after. ARE 2025-10-27/28: a gap down from a 73.65 low to a
    66.82 high -- support 73.65 on the bar before, resistance 66.82 on the bar after."""
    up = levels.gap_edges(["d0", "d1"], [170.68, 226.71], [163.14, 203.01])
    assert up == [
        levels.GapEdge("gapResistance", 170.68, "d0"),
        levels.GapEdge("gapSupport", 203.01, "d1"),
    ]
    down = levels.gap_edges(["d0", "d1"], [74.69, 66.82], [73.65, 59.90])
    assert down == [
        levels.GapEdge("gapResistance", 66.82, "d1"),
        levels.GapEdge("gapSupport", 73.65, "d0"),
    ]
    assert levels.places_gap(down, "gapSupport", 73.65, "d0")
    assert not levels.places_gap(down, "gapSupport", 73.65, "d1"), "the date is part of the level"
    assert not levels.places_gap(down, "gapResistance", 73.65, "d0"), "the side is part of the level"


def test_touching_ranges_are_not_a_gap_and_the_window_bounds_the_gaps():
    assert levels.gap_edges(["a", "b"], [10.0, 11.0], [9.0, 10.0]) == []  # low equals prior high
    dates = [f"d{i}" for i in range(300)]
    highs = [10.0] * 300
    lows = [9.0] * 300
    lows[10], highs[10] = 12.0, 13.0  # a gap 290 sessions back: outside the 250 window
    lows[11], highs[11] = 12.0, 13.0
    lows[12], highs[12] = 9.0, 10.0  # and one gapping back down, also outside
    assert levels.gap_edges(dates, highs, lows) == []
    assert len(levels.gap_edges(dates, highs, lows, window=295)) == 4


def test_rank_cutoffs_give_exactly_the_decile_of_the_percentile():
    """Nine stored numbers must reproduce ceil(10 x #(scores <= x) / n) for ANY x, market name or not."""
    import random

    rng = random.Random(7)
    for n in (10, 11, 99, 1000, 8461):
        scores = [rng.gauss(0.1, 0.4) for _ in range(n)]
        cut = levels.rank_cutoffs(scores)
        for x in (
            scores[:300] + [rng.gauss(0.1, 0.5) for _ in range(300)] + [min(scores) - 1, max(scores) + 1]
        ):
            brute = min(10, max(1, math.ceil(10 * sum(v <= x for v in scores) / n)))
            assert levels.rank_from_cutoffs(x, cut) == brute, (n, x)


def test_rank_score_is_half_the_month_plus_the_half_year():
    closes = [100.0] * 105 + [80.0] * 21 + [88.0]  # 127 closes: 126 back is 100, 21 back is 80
    assert levels.rank_score(closes) == pytest.approx(0.5 * (88 / 80 - 1) + (88 / 100 - 1))
    assert levels.rank_score(closes[1:]) is None  # one short of the 126-session return
