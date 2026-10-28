"""Hypothesis 24's study (scripts/meic_entry_slot_item24.py): the arms it declares and its verdict.
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
    "meic_entry_slot_item24", SCRIPTS / "meic_entry_slot_item24.py"
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
    "min_minutes_between_entries": 30,
    "min_credit_pct_of_width": 0.15,
    "max_call_delta_entry": 0.2,
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


def test_every_arm_is_ungated_pm200_in_its_own_30_minute_slot():
    p = st.study_registry(REGISTRY, 0.16)["profiles"]
    assert p["control"] == {"enabled": True} and p["pm-200"] == PM200
    base = {k: v for k, v in PM200.items() if not k.startswith("_")}
    base.update(enabled=True, short_delta_target=0.16, **st.UNGATED)
    for name, start in {"s1400": "14:00", **st.SLOTS, **st.MORNING}.items():
        assert p[name] == {**base, "paper_entry_window_start": start, "entry_window_end": st.slot_end(start)}
    assert st.slot_end("13:30") == "14:00" and st.slot_end("14:00") == "14:30"
    assert set(st.SLOTS) == {"s1200", "s1230", "s1300", "s1330"}


def test_the_delta_is_item_23s_or_refused():
    assert st.study_registry(REGISTRY, 0.25)["profiles"]["s1200"]["short_delta_target"] == 0.25
    with pytest.raises(SystemExit):
        st.study_registry(REGISTRY, 0.18)


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {}}, 0.16)


def _judged(arm, base):
    tests = {"s1230": {"own": st.own(arm, START, END), "vs_s1400": st.paired(arm, base, START, END)}}
    st.judge(tests)
    return tests["s1230"]


BASE = sessions([10.0, -5.0, 8.0, -3.0] * 50)


def test_a_steady_gain_over_1400_passes():
    v = _judged(sessions([25.0, 5.0, 22.0, 10.0] * 50), BASE)
    assert v["verdict"] == "pass", v["checks"]


def test_a_slot_no_better_than_1400_fails_on_the_paired_mean():
    v = _judged(BASE, BASE)
    assert v["verdict"] == "fail" and not v["checks"]["paired_mean_positive"]


def test_beating_1400_while_losing_on_its_own_fails():
    v = _judged(sessions([-5.0, -2.0, -3.0, 1.0] * 50), sessions([-40.0, -30.0, -35.0, -45.0] * 50))
    assert v["checks"]["paired_t_above_bar"] and not v["checks"]["own_mean_positive"]
    assert v["verdict"] == "fail"


def test_one_losing_half_of_the_difference_fails():
    arm = sessions([40.0, 30.0, 35.0, 45.0] * 50 + [6.0, -9.0, 4.0, -7.0] * 50)
    v = _judged(arm, sessions([10.0, -5.0, 8.0, -3.0] * 100))
    assert v["checks"]["paired_mean_positive"] and not v["checks"]["paired_both_halves_positive"]
    assert v["verdict"] == "fail"


def test_too_few_sessions_is_no_verdict():
    v = _judged(sessions([25.0, 5.0] * 20), sessions([1.0, 0.0] * 20))
    assert v["verdict"] == "no verdict"


def _passing_but_for(p):
    return {
        "own": {"mean_net": 5.0, "cost_stress": {"survives": True}},
        "vs_s1400": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
    }


def test_holm_across_the_four_slots_rejects_what_one_test_alone_would_pass():
    tests = {a: _passing_but_for(0.02) for a in st.SLOTS}  # t above 2, but 0.02 > 0.05 / 4
    st.judge(tests)
    assert all(not v["holm_ok"] and v["verdict"] == "fail" for v in tests.values())
    tests = {**{a: _passing_but_for(0.02) for a in st.SLOTS}, "s1230": _passing_but_for(0.001)}
    st.judge(tests)
    assert tests["s1230"]["verdict"] == "pass" and tests["s1300"]["verdict"] == "fail"


def test_own_reports_years_and_the_worst_session():
    o = st.own(sessions([5.0, -50.0, 3.0, 4.0]), START, END)
    assert set(o["years"]) == {"2022", "2023"} and o["worst"]["net"] == -50.0
