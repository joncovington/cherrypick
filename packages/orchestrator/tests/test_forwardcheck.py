"""#1080: a passing item's paper arm against its backtest. Invented trades and an invented
reference; no ledger or result file is read."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import forwardcheck as fc
from cherrypick.orchestrator import hypotheses as hy

SPEC = {
    "module": "weeklyput",
    "schema": "weeklyput_put",
    "arm": "unhedged",
    "measure": "cycle_mean",
    "field": "net_bp",
    "since": "2026-11-09",
    "min_obs": 10,
    "reference": {"file": "{home}/r.json", "mean": "arms.u.mean", "t": "arms.u.t", "n": "arms.u.n"},
    "kill_z": -2.0,
    "drift_z": -1.0,
}
# A reference of mean 4 bp a cycle, t 4 on 100 cycles: sd = 4 x 10 / 4 = 10 bp.
REF = {"arms": {"u": {"mean": 4.0, "t": 4.0, "n": 100}}}


def _read(path):
    return REF if path == "{home}/r.json" else None


def _trades(values, arm="unhedged", start=10):
    out = []
    for i, v in enumerate(values):
        day = f"2026-11-{start + i:02d}" if start + i <= 30 else f"2026-12-{start + i - 30:02d}"
        # Two names a cycle, averaged: each carries the cycle's value.
        out += [{"arm": arm, "session": day, "net_bp": v}, {"arm": arm, "session": day, "net_bp": v}]
    return out


def test_the_reference_spread_comes_from_the_studys_own_t():
    assert fc.reference(SPEC, _read) == {"mean": 4.0, "sd": 10.0, "n": 100}


def test_too_few_observations_is_collecting():
    out = fc.judge(SPEC, fc.observations(_trades([4.0] * 9), SPEC), fc.reference(SPEC, _read))
    assert out["state"] == "collecting" and out["n"] == 9


def test_paper_near_the_backtest_confirms_well_below_kills_and_in_between_drifts():
    ref = fc.reference(SPEC, _read)
    # 16 cycles: the standard error is 10 / 4 = 2.5 bp.
    assert fc.judge(SPEC, {str(i): 4.0 for i in range(16)}, ref)["state"] == "confirm"
    assert fc.judge(SPEC, {str(i): -2.0 for i in range(16)}, ref)["state"] == "kill"  # z = -2.4
    assert fc.judge(SPEC, {str(i): 1.0 for i in range(16)}, ref)["state"] == "drift"  # z = -1.2
    # Above the drift bar but losing money is still drift.
    low = {**ref, "mean": 0.5}
    assert fc.judge(SPEC, {str(i): -0.1 for i in range(16)}, low)["state"] == "drift"


def test_a_cycle_is_the_mean_over_its_names_and_a_session_sum_adds_them():
    trades = [
        {"arm": "a", "session": "2026-11-20", "net_bp": 2.0},
        {"arm": "a", "session": "2026-11-20", "net_bp": 4.0},
    ]
    assert fc.observations(trades, {**SPEC, "arm": "a"}) == {"2026-11-20": 3.0}
    assert fc.observations(trades, {**SPEC, "arm": "a", "measure": "session_sum"}) == {"2026-11-20": 6.0}


def test_trades_before_since_and_other_arms_are_not_read():
    trades = _trades([4.0], start=1) + _trades([9.0], arm="other")
    assert fc.observations(trades, SPEC) == {}


def test_an_unreadable_reference_keeps_collecting_with_why():
    out = fc.judge(SPEC, {str(i): 4.0 for i in range(16)}, None)
    assert out["state"] == "collecting" and "reference" in out["why"]


def test_run_reads_each_declared_row_and_reports_beside_arms():
    reg = {"hypotheses": [{"id": 57, "study": "item57", "forward": {**SPEC, "beside": ["other"]}},
                          {"id": 58, "study": "item58"}]}  # fmt: skip
    trades = _trades([4.0] * 12) + _trades([1.0] * 3, arm="other")
    [out] = fc.run(reg, lambda module, schema: trades, _read)
    assert out["id"] == 57 and out["state"] == "confirm" and out["beside_obs"] == {"other": 3}


@pytest.mark.parametrize(
    "change, why",
    [
        ({"measure": "median"}, "measure"),
        ({"min_obs": 0}, "min_obs"),
        ({"kill_z": -1.0, "drift_z": -2.0}, "kill_z < drift_z"),
        ({"reference": {"file": "x"}}, "reference needs"),
    ],
)
def test_a_malformed_forward_check_is_a_register_problem(change, why):
    assert any(why in p for p in fc.problems({**SPEC, **change}))


def test_the_repo_registers_forward_checks_are_sound():
    for row in hy.load()["hypotheses"]:
        if "forward" in row:
            assert fc.problems(row["forward"]) == [], row["id"]
