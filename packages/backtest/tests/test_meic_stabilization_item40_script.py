"""Hypothesis 40's study (scripts/meic_stabilization_item40.py): the arms it declares and its verdict.
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
    "meic_stabilization_item40", SCRIPTS / "meic_stabilization_item40.py"
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
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}
GATE_KEYS = {
    "stabilization_entry",
    "stabilization_threshold_mode",
    "stabilization_oracle",
    "stabilization_bars",
}


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


def test_be10_is_the_documented_arm_at_ten_point_wings():
    p = st.study_registry(REGISTRY, 0.6)["profiles"]
    assert p["be10"]["wing_widths_by_symbol"] == {"SPX": [10]} and p["be10"]["equalize_credit"] is False
    assert p["be10"]["stop_trigger_ratio"] == 1.0 and p["be10"]["delta_target"] == 0.12
    assert (p["be10"]["paper_entry_window_start"], p["be10"]["entry_window_end"]) == ("09:45", "15:30")
    assert p["be10"]["min_minutes_between_entries"] == 55 and p["be10"]["daily_ic_trade_target"] == 7


def test_the_gated_arms_differ_from_their_base_only_by_the_gate_keys():
    p = st.study_registry(REGISTRY, 0.6)["profiles"]
    for arm, (oracle, bars) in {
        "be10-multi": ("multi", 2),
        "be10-perday": ("perday", 2),
        "be10-multi-3b": ("multi", 3),
    }.items():
        extra = {k: v for k, v in p[arm].items() if k not in p["be10"] or p["be10"][k] != v}
        assert set(extra) == GATE_KEYS
        assert (extra["stabilization_oracle"], extra["stabilization_bars"]) == (oracle, bars)
        assert extra["stabilization_threshold_mode"] == "atr"
    live = {k: v for k, v in PM200.items() if not k.startswith("_")}
    live.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30")
    assert p["live-1400"] == live
    extra = {k: v for k, v in p["live-1400-multi"].items() if k not in live}
    assert set(extra) == GATE_KEYS and extra["stabilization_oracle"] == "multi"


def test_the_placebo_is_be10_with_only_the_random_skip():
    p = st.study_registry(REGISTRY, 0.61)["profiles"]
    assert p["be10-skip"] == {
        **p["be10"],
        "stabilization_placebo_rate": 0.61,
        "stabilization_placebo_seed": 40,
    }
    assert not any(k in p["be10-skip"] for k in ("stabilization_entry",))


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}}, 0.5)


def test_the_study_refuses_without_the_gate_wiring(monkeypatch):
    """Before PR #728 the module is missing; with it, a missing key refuses the same way."""
    try:
        from cherrypick.meic import stabilization_gate
    except ImportError:
        stabilization_gate = None
    if stabilization_gate is not None:
        monkeypatch.delattr(stabilization_gate, "placebo_skips", raising=False)
    with pytest.raises(SystemExit):
        st.require_gate()


def _judged(arm, base, holdout=False):
    tests = {"be10-multi": {"own": st.own(arm, START, END), "vs_base": st.paired(arm, base, START, END)}}
    st.judge(tests, holdout)
    return tests["be10-multi"]


BASE = sessions([10.0, -5.0, 8.0, -3.0] * 50)


def test_a_steady_gain_over_the_base_passes():
    v = _judged(sessions([25.0, 5.0, 22.0, 10.0] * 50), BASE)
    assert v["verdict"] == "pass", v["checks"]


def test_an_arm_no_better_than_its_base_fails_on_the_paired_mean():
    v = _judged(BASE, BASE)
    assert v["verdict"] == "fail" and not v["checks"]["paired_mean_positive"]


def test_beating_the_base_while_losing_on_its_own_fails():
    base = sessions([-40.0, -30.0, -35.0, -45.0] * 50)
    v = _judged(sessions([-5.0, -2.0, -3.0, 1.0] * 50), base)
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


def test_the_holdout_judges_the_paired_difference_only():
    v = _judged(sessions([25.0, 5.0, 22.0, 10.0] * 50), BASE, holdout=True)
    assert set(v["checks"]) == {"paired_mean_positive", "paired_t_above_bar"} and v["verdict"] == "pass"


def _passing_but_for(p):
    return {
        "own": {"mean_net": 5.0, "cost_stress": {"survives": True}},
        "vs_base": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
    }


def test_holm_across_the_four_judged_arms():
    judged = [a for arms in st.JUDGED.values() for a in arms]
    assert len(judged) == 4
    tests = {a: _passing_but_for(0.02) for a in judged}  # t above 2, but 0.02 > 0.05 / 4
    st.judge(tests, False)
    assert all(not v["holm_ok"] and v["verdict"] == "fail" for v in tests.values())
    tests = {**{a: _passing_but_for(0.02) for a in judged}, "live-1400-multi": _passing_but_for(0.001)}
    st.judge(tests, False)
    assert tests["live-1400-multi"]["verdict"] == "pass" and tests["be10-multi"]["verdict"] == "fail"
