"""The statistics, against their definitions: PSR is 0.5 at the benchmark, deflation grows with the
trial count, a longer record is needed for a weaker edge, and the bootstrap is reproducible."""

from __future__ import annotations

import random

import pytest

from cherrypick.backtest import stats


def _series(mean, sd=1.0, n=250, seed=3):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_psr_is_a_half_when_the_sharpe_equals_the_benchmark():
    values = _series(0.1)
    m = stats.moments(values)
    assert stats.psr(values, benchmark=m["sharpe"]) == pytest.approx(0.5)


def test_more_trials_deflate_more():
    values = _series(0.15)
    one, ten, thousand = (stats.dsr(values, n) for n in (1, 10, 1000))
    assert one == pytest.approx(stats.psr(values)), "one pre-registered trial is not deflated"
    assert one > ten > thousand


def test_the_luck_hurdle_grows_with_trials_and_is_zero_for_one():
    assert stats.expected_max_sharpe(1, 0.01) == 0.0
    assert 0 < stats.expected_max_sharpe(10, 0.01) < stats.expected_max_sharpe(100, 0.01)


def test_a_weaker_edge_needs_a_longer_record_and_none_cannot_pass():
    strong, weak = stats.min_track_record(_series(0.3)), stats.min_track_record(_series(0.05, seed=5))
    assert strong is not None and weak is not None and weak > strong
    assert stats.min_track_record(_series(-0.2)) is None


def test_the_bootstrap_is_reproducible_and_brackets_the_mean():
    values = _series(0.2)
    a, b = stats.block_bootstrap_ci(values), stats.block_bootstrap_ci(values)
    assert a == b
    assert a[0] < sum(values) / len(values) < a[1]


def test_degenerate_series_report_nothing_rather_than_raise():
    assert stats.moments([1.0]) is None and stats.moments([2.0, 2.0]) is None
    s = stats.summary([5.0], trials=3)
    assert s["sessions"] == 1 and s["psr"] is None and s["dsr"] is None
