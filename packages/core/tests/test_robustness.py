"""B9/B12/B13: the lines a declaration states before a test and reports beside a pass."""

import math

import pytest

from cherrypick.core import robustness as rb


def test_name_half_is_fixed_and_case_blind():
    assert rb.name_half("SPY") == rb.name_half(" spy ")
    # Pinned literals: a hash or salt change would silently reshuffle every declared name split.
    syms = ("SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA")
    assert [rb.name_half(s) for s in syms] == [0, 1, 0, 1, 0, 1]


def test_split_names_covers_every_symbol_once():
    syms = [f"S{i}" for i in range(200)]
    a, b = rb.split_names(syms + ["S1"])
    assert sorted(a + b) == sorted(syms) and not set(a) & set(b)
    assert 70 < len(a) < 130  # roughly even


def test_planned_mde_paired_and_unpaired():
    vals = [1.0, -1.0] * 50  # sd ~1.005
    p = rb.planned_mde(vals, 100)
    z = 1.6449 + 0.8416
    assert p["mde"] == pytest.approx(z * p["sd"] / 10, abs=1e-3)
    u = rb.planned_mde(vals, 100, paired=False)
    assert u["mde"] == pytest.approx(p["mde"] * math.sqrt(2), abs=1e-3)
    assert rb.planned_mde([1.0], 100)["mde"] is None


def test_neighbourhood_flags_a_lone_peak_and_a_plateau():
    assert rb.neighbourhood({10: 1.0, 20: 5.0, 30: 0.5}, 20)["verdict"] == "lone_peak"
    assert rb.neighbourhood({10: 3.0, 20: 5.0, 30: 4.0}, 20)["verdict"] == "plateau"
    assert rb.neighbourhood({10: 3.0, 20: 5.0, 30: -1.0}, 20)["verdict"] == "lone_peak"
    assert rb.neighbourhood({20: 5.0}, 20)["verdict"] == "no_neighbours"
    assert rb.neighbourhood({10: 1.0}, 20)["verdict"] == "unmeasured"


def test_best_sessions_sees_a_result_that_survives_losing_its_best_day_but_not_its_best_five():
    # 5 big days and 95 small losers: drop any one big day and the total stays positive.
    nets = {f"2024-01-{d:02d}": 1000.0 for d in range(1, 6)}
    nets.update({f"2024-{m:02d}-{d:02d}": -40.0 for m in range(2, 7) for d in range(1, 20)})
    b = rb.best_sessions(nets)
    assert b["n"] == 100 and b["k"] == 5
    assert b["sessions"] == [f"2024-01-{d:02d}" for d in range(1, 6)]
    assert b["total_net"] == 5000 - 95 * 40 and b["best_net"] == 5000
    assert b["share"] == round(5000 / 1200, 4)  # over 1: the rest lose money
    assert b["rest_mean"] == -40 and b["rests_on_best"] is True


def test_best_sessions_reads_a_broad_result_as_not_resting_on_its_best():
    nets = {f"s{i:03d}": 10.0 + (i % 7) for i in range(60)}
    b = rb.best_sessions(nets, k=3)
    assert b["rests_on_best"] is False and 0 < b["share"] < 0.1


def test_best_sessions_ties_go_to_the_earlier_session_and_short_results_are_insufficient():
    assert rb.best_sessions({"b": 5.0, "a": 5.0, "c": 1.0, "d": 0.0}, k=1)["sessions"] == ["a"]
    assert rb.best_sessions({"a": 1.0, "b": 2.0}, k=2) == {"k": 2, "n": 2, "insufficient": True}
    assert rb.best_sessions({"a": -1.0, "b": -2.0, "c": -3.0}, k=1)["share"] is None
    with pytest.raises(ValueError):
        rb.best_sessions({"a": 1.0}, k=0)


def test_cost_stress_must_survive_the_declared_multiplier():
    nets, costs = [10.0, 10.0], [8.0, 8.0]
    out = rb.cost_stress(nets, costs)
    assert out["mean_by_multiplier"] == {1.0: 10.0, 1.5: 6.0, 2.0: 2.0}
    assert out["survives"]
    assert not rb.cost_stress(nets, costs, pass_stress=2.5)["survives"]
    with pytest.raises(ValueError):
        rb.cost_stress([1.0], [])


def test_max_size_keeps_the_p99_drawdown_inside_the_share():
    assert rb.max_size(400.0, 14_000, 0.10) == 3
    assert rb.max_size(2000.0, 14_000, 0.10) == 0
    assert rb.max_size(None, 14_000, 0.10) == 0


def test_report_assembles_every_part():
    nets = [30.0] * 80 + [-90.0] * 20
    out = rb.report(nets, [2.0] * 100, means_by_setting={1: 5, 2: 6, 3: 5}, declared=2, sessions=100,
                    account=14_000, account_share=0.25)  # fmt: skip
    assert out["neighbourhood"]["verdict"] == "plateau"
    assert out["cost_stress"]["survives"]
    assert out["drawdown"]["drawdown_block"] > 0 and out["max_size"] >= 1
