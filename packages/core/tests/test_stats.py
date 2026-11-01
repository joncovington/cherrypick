"""cherrypick.core.stats: known answers, so a change to any estimator shows up as a number that moved.

Each value below was checked against an independent source (published t tables, the binomial
distribution by hand, the textbook Holm/BH examples) rather than read back off this module.
"""

import math
import random

import pytest

from cherrypick.core import stats


# --------------------------------------------------------------------------- Student's t
@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.95, 1, 6.313752),
        (0.95, 3, 2.353363),
        (0.975, 10, 2.228139),
        (0.95, 30, 1.697261),
        (0.995, 5, 4.032143),
        (0.95, 120, 1.657651),
    ],
)
def test_t_quantile_matches_published_tables(p, df, expected):
    assert stats.t_quantile(p, df) == pytest.approx(expected, abs=1e-5)


def test_t_cdf_is_symmetric_and_inverts_the_quantile():
    assert stats.t_cdf(0.0, 7) == pytest.approx(0.5)
    for df in (2, 9, 40):
        q = stats.t_quantile(0.9, df)
        assert stats.t_cdf(q, df) == pytest.approx(0.9, abs=1e-9)
        assert stats.t_cdf(-q, df) == pytest.approx(0.1, abs=1e-9)
    assert stats.t_quantile(0.05, 12) == pytest.approx(-stats.t_quantile(0.95, 12))


def test_t_lower_bound_exact_and_with_a_declared_critical_value():
    diffs = [100.0, 300.0, 200.0, 200.0]  # mean 200, sd 81.6497
    assert stats.t_lower_bound(diffs) == pytest.approx(200 - 2.353363 * 81.649658 / 2, abs=1e-3)
    assert stats.t_lower_bound(diffs, critical=2.353) == pytest.approx(200 - 2.353 * 81.649658 / 2, abs=1e-6)
    assert stats.t_lower_bound([5.0]) is None


# --------------------------------------------------------------------------- sign test
def test_sign_test_exact_binomial():
    # 7 of 9: P(X >= 7) = (C(9,7) + C(9,8) + C(9,9)) / 512 = 46 / 512.
    assert stats.sign_test_p(7, 2, alternative="greater") == pytest.approx(46 / 512)
    assert stats.sign_test_p(7, 2) == pytest.approx(92 / 512)
    assert stats.sign_test_p(2, 7, alternative="less") == pytest.approx(46 / 512)
    assert stats.sign_test_p(3, 3) == 1.0
    assert stats.sign_test_p(0, 5) == pytest.approx(2 / 32)
    assert stats.sign_test_p(0, 0) is None


# --------------------------------------------------------------------------- power
def test_sessions_needed_and_minimum_detectable_effect_are_inverses():
    # z(0.95) + z(0.80) = 1.644854 + 0.841621 = 2.486475.
    assert stats.sessions_needed(100.0, 100.0) == math.ceil(2.486475**2)  # 7
    assert stats.sessions_needed(81.65, 100.0) == 5
    assert stats.sessions_needed(0.0, 100.0) is None
    assert stats.sessions_needed(50.0, None) is None
    mde = stats.minimum_detectable_effect(100.0, 7)
    assert mde == pytest.approx(2.486475 * 100 / math.sqrt(7), abs=1e-4)
    # The inverse lands on 7 exactly; a hair of float above it would ceil to 8, so nudge the edge.
    assert stats.sessions_needed(100.0, mde * (1 + 1e-9)) == 7


def test_sessions_to_exclude_zero_projects_from_the_observed_mean():
    # (1.959964 * 40 / 10)^2 = 61.46 -> 62 sessions in all, 42 more after 20.
    assert stats.sessions_to_exclude_zero(10.0, 40.0, 20) == 42
    assert stats.sessions_to_exclude_zero(-10.0, 40.0, 20) == 42
    assert stats.sessions_to_exclude_zero(10.0, 40.0, 100) == 0
    assert stats.sessions_to_exclude_zero(0.0, 40.0, 20) is None
    assert stats.sessions_to_exclude_zero(None, 40.0, 20) is None


# --------------------------------------------------------------------------- multiplicity
P = {"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5}


def test_holm_steps_down_and_stops_at_the_first_failure():
    # alpha/4 = .0125 rejects a; alpha/3 = .0167 does not reject c, and Holm stops there.
    assert stats.holm(P) == {"a": True, "c": False, "b": False, "d": False}
    # The stop is load-bearing: d's .04 is under its own alpha/1 = .05, and is still not rejected
    # because b failed first. (The first fixture alone let a stop-less Holm pass.)
    assert stats.holm({"a": 0.01, "b": 0.03, "c": 0.03, "d": 0.04})["d"] is False
    adj = stats.holm_adjusted(P)
    assert adj == pytest.approx({"a": 0.04, "c": 0.09, "b": 0.09, "d": 0.5})


def test_benjamini_hochberg_steps_up():
    # thresholds k*q/m at q=.10: .025 .05 .075 .10; the largest k with p_(k) under it is 3.
    assert stats.benjamini_hochberg(P, q=0.10) == {"a": True, "c": True, "b": True, "d": False}
    assert stats.benjamini_hochberg(P, q=0.05) == {"a": True, "c": False, "b": False, "d": False}
    adj = stats.bh_adjusted(P)
    assert adj == pytest.approx({"a": 0.04, "c": 4 * 0.04 / 3, "b": 4 * 0.04 / 3, "d": 0.5})


# --------------------------------------------------------------------------- bootstrap
def test_bootstrap_is_seeded_and_brackets_a_constant_exactly():
    assert stats.bootstrap_ci([7.0] * 10) == (7.0, 7.0)
    assert stats.bootstrap_ci([7.0] * 10, statistic="sum") == (70.0, 70.0)
    rng = random.Random(3)
    vals = [rng.gauss(5, 20) for _ in range(30)]
    assert stats.bootstrap_ci(vals) == stats.bootstrap_ci(vals)
    assert stats.bootstrap_ci(vals, seed=1) != stats.bootstrap_ci(vals, seed=2)
    assert stats.bootstrap_ci([]) is None
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1.0], statistic="median")


def test_bootstrap_interval_is_pinned_to_the_regimecuts_stamps():
    """regimecuts' cell intervals were stamped by this exact draw before it moved here; a change to
    the draw or the index rule would make tonight's artifact disagree with last week's."""
    nets = [120.0, -40.5, 33.25, -310.0, 75.0, 12.0, -8.0, 260.0, -99.0, 41.0]
    lo, hi = stats.bootstrap_ci(nets, statistic="sum", level=0.90, resamples=1000, seed=20260928)
    assert (round(lo, 2), round(hi, 2)) == (-618.5, 806.0)


def test_day_placebo_one_good_day_of_four_is_matched_by_a_quarter_of_random_days():
    # Keeping 1 session of 4 where only that one made money: a random 1-of-4 pick matches it 1/4 of
    # the time (the binomial by hand).
    out = stats.day_placebo({"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0}, ["a"], draws=20000)
    assert out["sessions"] == 1 and out["of"] == 4 and out["mean"] == 1.0
    assert abs(out["beaten_by"] - 0.25) < 0.01


def test_day_placebo_reads_the_best_days_near_zero_and_the_worst_at_one():
    values = {f"s{i:03d}": float(i % 17) - 8.0 for i in range(120)}
    ranked = sorted(values, key=values.get)
    assert stats.day_placebo(values, ranked[-12:])["beaten_by"] < 0.01
    assert stats.day_placebo(values, ranked[:12])["beaten_by"] == 1.0


def test_day_placebo_counts_an_identical_mean_as_at_least_and_is_seeded():
    flat = {f"s{i}": 0.1 for i in range(30)}
    assert stats.day_placebo(flat, ["s1", "s2", "s3"])["beaten_by"] == 1.0
    values = {f"s{i}": math.sin(i) for i in range(50)}
    kept = [f"s{i}" for i in range(0, 50, 5)]
    assert stats.day_placebo(values, kept) == stats.day_placebo(values, kept)
    assert stats.day_placebo(values, kept, seed=1) != stats.day_placebo(values, kept, seed=2)


def test_day_placebo_has_nothing_to_say_when_a_split_keeps_no_day_or_every_day():
    values = {"a": 1.0, "b": 2.0}
    assert stats.day_placebo(values, []) is None
    assert stats.day_placebo(values, ["zz"]) is None  # sessions outside the arm are ignored
    assert stats.day_placebo(values, ["a", "b"]) is None


def test_stationary_indices_wrap_and_are_seeded():
    a = stats.stationary_indices(50, 10, random.Random(1))
    assert a == stats.stationary_indices(50, 10, random.Random(1))
    assert len(a) == 50 and all(0 <= i < 50 for i in a)


# --------------------------------------------------------------------------- pairing
def test_paired_diffs_intersection_and_fill():
    arm = {"d1": 10.0, "d2": 5.0, "d4": 1.0}
    ctl = {"d1": 4.0, "d2": 5.0, "d3": 2.0}
    assert stats.paired_diffs(arm, ctl) == (["d1", "d2"], [6.0, 0.0])
    assert stats.paired_diffs(arm, ctl, fill=0.0) == (["d1", "d2", "d3", "d4"], [6.0, 0.0, -2.0, 1.0])
    assert stats.paired_diffs(arm, ctl, fill=0.0, sessions=["d1", "d3"]) == (["d1", "d3"], [6.0, -2.0])


def test_session_values_pools_by_day_and_drops_undated_rows():
    recs = [
        {"session": "d2", "net_pnl": 3.0},
        {"session": "d1", "net_pnl": 1.0},
        {"session": "d1", "net_pnl": -4.0},
        {"session": "", "net_pnl": 100.0},
    ]
    assert stats.session_values(recs) == {"d1": -3.0, "d2": 3.0}


def test_paired_comparison_block():
    arm = {f"d{i:02d}": 50.0 + (10 if i % 2 else -10) for i in range(20)}
    ctl = {f"d{i:02d}": 0.0 for i in range(20)}
    out = stats.paired_comparison(arm, ctl, edge=25.0)
    assert out["sessions"] == 20 and out["mean_diff"] == 50.0
    assert out["sd_diff"] == pytest.approx(10.26, abs=0.01)
    assert out["ci_excludes_zero"] is True and out["ci"][0] > 0
    assert out["arm_better"] == 20 and out["sign_test_p"] == pytest.approx(2 / 2**20, abs=1e-4)
    assert out["sessions_to_exclude_zero"] == 0
    bound = 50 - stats.t_quantile(0.95, 19) * 10.2598 / math.sqrt(20)
    assert out["t_lower"] == pytest.approx(bound, abs=0.01)
    assert out["sessions_needed"] == stats.sessions_needed(out["sd_diff"], 25.0)
    assert out["total_diff"] == 1000.0


def test_paired_comparison_of_nothing_says_nothing():
    out = stats.paired_comparison({}, {"d1": 1.0})
    assert out["sessions"] == 0 and out["mean_diff"] is None and out["ci"] is None
    assert out["sign_test_p"] is None and out["sessions_to_exclude_zero"] is None
