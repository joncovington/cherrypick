"""Hypothesis 23's study (scripts/meic_delta_grid_item23.py): the arms it declares and its verdict.
Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_delta_grid_item23", SCRIPTS / "meic_delta_grid_item23.py"
)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-01-03", "2023-12-29"
PM200 = {
    "_note": "x",
    "enabled": False,
    "short_delta_target": 0.16,
    "paper_entry_window_start": "12:00",
    "entry_window_end": "14:30",
    "min_credit_pct_of_width": 0.15,
    "max_call_delta_entry": 0.2,
    "max_call_delta_entry_late": 0.19,
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}


def sessions(nets, fees=1.0):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return {
        (d0 + timedelta(days=i * span // max(len(nets) - 1, 1))).isoformat(): {
            "net": n,
            "fees": fees,
            "trades": 1,
        }
        for i, n in enumerate(nets)
    }


def test_live_1400_is_pm200_in_the_1400_window_with_its_gates():
    p = st.study_registry(REGISTRY)["profiles"]
    assert p["control"] == {"enabled": True} and p["pm-200"] == PM200
    want = {k: v for k, v in PM200.items() if not k.startswith("_")}
    want.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30")
    assert p["live-1400"] == want


def test_the_judged_arms_differ_from_live_only_by_the_gates_and_their_delta():
    p = st.study_registry(REGISTRY)["profiles"]
    assert p["d16"] == {**p["live-1400"], **st.UNGATED}
    assert st.UNGATED["min_credit_pct_of_width"] == 0 and st.UNGATED["low_iv_min_credit_pct_of_width"] == 0
    assert all(st.UNGATED[k] >= 1 for k in st.UNGATED if k.startswith("max_call_delta"))
    for name, target in st.GRID.items():
        assert p[name] == {**p["d16"], "short_delta_target": target}
    assert st.GRID == {"d12": 0.12, "d20": 0.20, "d25": 0.25, "d30": 0.30}


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}})


def _judged(arm, base):
    tests = {"d20": {"own": st.own(arm, START, END), "vs_d16": st.paired(arm, base, START, END)}}
    st.judge(tests)
    return tests["d20"]


BASE = sessions([10.0, -5.0, 8.0, -3.0] * 50)


def test_a_steady_gain_over_d16_passes():
    arm = sessions([25.0, 5.0, 22.0, 10.0] * 50)
    v = _judged(arm, BASE)
    assert v["verdict"] == "pass", v["checks"]


def test_an_arm_no_better_than_d16_fails_on_the_paired_mean():
    v = _judged(BASE, BASE)
    assert v["verdict"] == "fail" and not v["checks"]["paired_mean_positive"]


def test_beating_d16_while_losing_on_its_own_fails():
    base = sessions([-40.0, -30.0, -35.0, -45.0] * 50)
    arm = sessions([-5.0, -2.0, -3.0, 1.0] * 50)
    v = _judged(arm, base)
    assert v["checks"]["paired_t_above_bar"] and not v["checks"]["own_mean_positive"]
    assert v["verdict"] == "fail"


def test_one_losing_half_of_the_difference_fails():
    arm = sessions([40.0, 30.0, 35.0, 45.0] * 50 + [6.0, -9.0, 4.0, -7.0] * 50)
    base = sessions([10.0, -5.0, 8.0, -3.0] * 100)
    v = _judged(arm, base)
    assert v["checks"]["paired_mean_positive"] and not v["checks"]["paired_both_halves_positive"]
    assert v["verdict"] == "fail"


def test_too_few_sessions_is_no_verdict():
    v = _judged(sessions([25.0, 5.0] * 20), sessions([1.0, 0.0] * 20))
    assert v["verdict"] == "no verdict"


def _passing_but_for(p):
    return {
        "own": {"mean_net": 5.0, "cost_stress": {"survives": True}},
        "vs_d16": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
    }


def test_holm_across_the_four_arms_rejects_what_one_test_alone_would_pass():
    tests = {a: _passing_but_for(0.02) for a in st.GRID}  # t above 2, but 0.02 > 0.05 / 4
    st.judge(tests)
    assert all(not v["holm_ok"] and v["verdict"] == "fail" for v in tests.values())
    tests = {**{a: _passing_but_for(0.02) for a in st.GRID}, "d20": _passing_but_for(0.001)}
    st.judge(tests)
    assert tests["d20"]["verdict"] == "pass" and tests["d30"]["verdict"] == "fail"
