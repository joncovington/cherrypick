"""Hypothesis 21's study (scripts/flies_entry_limit_item21.py): the arms it declares and its verdict.
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
    "flies_entry_limit_item21", SCRIPTS / "flies_entry_limit_item21.py"
)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-01-03", "2023-12-29"
BASE = {
    "defaults": {"wing_width": 5},
    "arms": {
        "control": {"enabled": True, "entry_windows": [["10:00", "14:30"]], "_note": "x"},
        "range-high": {"enabled": True, "entry_windows": [["10:00", "14:30"]], "range_high_pct": 0.9},
        "gex": {"enabled": False},
    },
}


def sessions(nets, fees=1.0):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return {
        (d0 + timedelta(days=i * span // max(len(nets) - 1, 1))).isoformat(): {
            "net": n,
            "fees": fees,
            "trades": 2,
        }
        for i, n in enumerate(nets)
    }


def test_the_six_arms_are_their_base_plus_the_declared_limit_and_nothing_else():
    cfg = st.study_config(BASE)
    arms = cfg["arms"]
    assert arms["gex"] == {"enabled": False} and arms["control"] == BASE["arms"]["control"]
    for name, source in st.limit_arms().items():
        limit = arms[name].pop("entry_limit")
        assert limit == {"offset": st.OFFSETS[name.split("-")[1]], "window_seconds": 120, "basis": "mid"}
        assert arms[name] == {k: v for k, v in BASE["arms"][source].items() if not k.startswith("_")}
    assert len(st.limit_arms()) == 6
    assert st.OFFSETS == {"000": 0.0, "005": 0.05, "010": 0.10}


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_config({"arms": {"control": {}}})


def _judged(**arms):
    tests = {k: st.score(v, START, END) for k, v in arms.items()}
    st.judge(tests)
    return tests


def test_a_strong_steady_edge_passes():
    (v,) = _judged(a=sessions([30.0, 10.0, 20.0, 15.0] * 50)).values()
    assert v["verdict"] == "pass", v["checks"]


def test_a_losing_arm_fails_on_its_mean():
    (v,) = _judged(a=sessions([-30.0, 10.0, -20.0, 15.0] * 50)).values()
    assert v["verdict"] == "fail" and not v["checks"]["mean_positive"]


def test_one_losing_half_fails():
    (v,) = _judged(a=sessions([40.0, 30.0, 35.0, 45.0] * 50 + [-5.0, -3.0, -4.0, -2.0] * 50)).values()
    assert v["checks"]["t_above_bar"] and not v["checks"]["both_halves_positive"]
    assert v["verdict"] == "fail"


def test_fees_that_eat_the_edge_at_one_and_a_half_times_fail():
    (v,) = _judged(a=sessions([12.0, 8.0, 10.0, 11.0] * 50, fees=30.0)).values()
    assert v["checks"]["t_above_bar"] and not v["checks"]["survives_1_5x_fees"]
    assert v["verdict"] == "fail"


def test_holm_over_six_arms_refuses_what_one_t_above_two_would_pass():
    weak = sessions([3.4, -2.5, 3.4, -2.5] * 50, fees=0.1)  # t about 2.2, one-sided p about 0.016 > 0.05 / 6
    (alone,) = _judged(a=weak).values()
    assert alone["verdict"] == "pass"
    tests = _judged(**{f"a{i}": weak for i in range(6)})
    for v in tests.values():
        assert v["checks"]["t_above_bar"] and not v["holm_ok"] and v["verdict"] == "fail"


def test_too_few_sessions_is_no_verdict():
    (v,) = _judged(a=sessions([30.0, 10.0] * 40)).values()
    assert v["verdict"] == "no verdict"


def test_the_paired_difference_is_over_shared_sessions():
    a = {"d1": {"net": 5.0}, "d2": {"net": 7.0}, "d3": {"net": 1.0}}
    b = {"d1": {"net": 1.0}, "d2": {"net": 2.0}}
    assert st.paired(a, b) == {"n": 2, "mean": 4.5, "t": pytest.approx(9.0, abs=1e-3)}
