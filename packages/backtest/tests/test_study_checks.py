"""The study kit's three verdict bars and the verdict block, on invented trades."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from cherrypick.backtest.study import checks as ck

_spec = importlib.util.spec_from_file_location(
    "super_bull_item74", Path(__file__).resolve().parents[1] / "scripts" / "super_bull_item74.py"
)
i74 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(i74)

BOOT = {"resamples": 2000, "seed": 7}


def steady(n, mean, sd, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_financed_delta_is_item_74s():
    for args in ((0.42, 100.0, 103.5, 900.0, 0.05, 35), (-0.3, 50.0, 48.0, 400.0, 0.0, 7)):
        assert ck.financed_delta(*args) == i74.financed(*args)


def test_the_capital_bar_charges_the_bill_on_the_buying_power_held():
    # Each trade nets $10 on $2,000 held 73 days: at a 5% bill that is $20 forgone, so it fails.
    nets = steady(200, 10.0, 1.0)
    bp, days = [2000.0] * 200, [73.0] * 200
    assert ck.capital_check(nets, bp, days, [0.0] * 200, t_bar=2.0, min_n=100)["verdict"] == ck.PASS
    out = ck.capital_check(nets, bp, days, [0.05] * 200, t_bar=2.0, min_n=100)
    assert out["verdict"] == ck.FAIL and out["mean"] < 0


def test_the_capital_bar_needs_its_floor_and_its_t():
    assert ck.capital_check([5.0] * 50, [1.0] * 50, [1.0] * 50, [0.0] * 50, t_bar=2.0, min_n=100)[
        "verdict"
    ] == (ck.NO_VERDICT)
    noisy = steady(150, 0.5, 40.0, seed=3)
    assert ck.capital_check(noisy, [1.0] * 150, [1.0] * 150, [0.0] * 150, t_bar=2.0, min_n=100)[
        "verdict"
    ] == (ck.FAIL)
    with pytest.raises(ValueError):
        ck.capital_check([1.0], [1.0, 2.0], [1.0], [0.0], t_bar=2.0, min_n=1)


def test_the_tail_bar_passes_a_steady_earner_and_fails_one_with_rare_large_losses():
    # 250 trades a year at +$10 with small noise: a year earns $2,500, its worst likely drawdown is small.
    calm = steady(500, 10.0, 15.0)
    assert ck.tail_check(calm, per_year=250, min_n=200, **BOOT)["verdict"] == ck.PASS
    # Same mean, but the premium-seller's shape: +$30 most days, -$2,000 about one day in a hundred.
    rng = random.Random(5)
    tail = [(-2000.0 if rng.random() < 0.01 else 30.0) for _ in range(500)]
    out = ck.tail_check(tail, per_year=250, min_n=200, **BOOT)
    assert sum(tail) > 0 and out["verdict"] == ck.FAIL and out["drawdown_p99_year"] > out["year_net"]


def test_the_tail_bar_fails_a_loser_and_needs_its_floor():
    assert ck.tail_check(steady(300, -1.0, 5.0), per_year=250, min_n=200, **BOOT)["verdict"] == ck.FAIL
    assert ck.tail_check(steady(150, 10.0, 5.0), per_year=250, min_n=200, **BOOT)["verdict"] == ck.NO_VERDICT


def test_the_delta_bar_takes_out_what_the_delta_alone_made():
    # A long-delta trade nets $40 in a sample where its delta alone made $45: no edge beyond direction.
    nets = steady(200, 40.0, 5.0)
    assert ck.delta_check(nets, [45.0] * 200, t_bar=2.0, min_n=100)["verdict"] == ck.FAIL
    assert ck.delta_check(nets, [30.0] * 200, t_bar=2.0, min_n=100)["verdict"] == ck.PASS


def test_the_verdict_needs_every_applicable_bar():
    p, f, nv = {"verdict": ck.PASS}, {"verdict": ck.FAIL}, {"verdict": ck.NO_VERDICT}
    assert ck.verdict(p, p, None) == {"verdict": ck.PASS, "failed": [], "bars": {
        "capital": p, "tail": p, "delta": {"verdict": ck.NOT_APPLICABLE}}}  # fmt: skip
    assert ck.verdict(p, f, p)["verdict"] == ck.FAIL and ck.verdict(p, f, p)["failed"] == ["tail"]
    assert ck.verdict(p, nv, p)["verdict"] == ck.NO_VERDICT
    assert ck.verdict(f, nv, None)["verdict"] == ck.FAIL


def test_no_bar_has_a_default_a_declaration_must_fix():
    import inspect

    for fn, required in (
        (ck.capital_check, {"t_bar", "min_n"}),
        (ck.tail_check, {"per_year", "min_n", "resamples", "seed"}),
        (ck.delta_check, {"t_bar", "min_n"}),
    ):
        params = inspect.signature(fn).parameters
        assert {k for k in required if params[k].default is inspect.Parameter.empty} == required
