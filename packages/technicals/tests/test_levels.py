"""The level grid and the rank, each detail pinned by the case that found it."""

from __future__ import annotations

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
