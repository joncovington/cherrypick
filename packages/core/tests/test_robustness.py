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
