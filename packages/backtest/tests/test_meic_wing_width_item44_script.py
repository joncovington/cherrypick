"""Hypothesis 44's study (scripts/meic_wing_width_item44.py): the arms it declares, its two verdicts and
the holdout's order after item 43. Invented sessions and quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_item44", SCRIPTS / "meic_wing_width_item44.py"
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
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}
GATE_KEYS = {
    "stabilization_entry",
    "stabilization_threshold_mode",
    "stabilization_oracle",
    "stabilization_bars",
}


def sessions(nets, fees=1.0, trades=1):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return {
        (d0 + timedelta(days=i * span // max(len(nets) - 1, 1))).isoformat(): {
            "net": n,
            "fees": fees,
            "trades": trades,
        }
        for i, n in enumerate(nets)
    }


def _diff(a, b):
    return {k: v for k, v in a.items() if k not in b or b[k] != v}


# ── the arms ─────────────────────────────────────────────────────────────────


def test_the_width_arms_are_item_43s_w10_at_each_width_ten_to_forty():
    p = st.study_registry(REGISTRY, 0.4)["profiles"]
    w10 = st.i43.study_registry(REGISTRY)["profiles"]["w10"]
    assert list(st.WIDTHS) == ["w10", "w15", "w20", "w25", "w30", "w35", "w40"]
    for name, width in st.WIDTHS.items():
        assert p[name] == {**w10, "wing_width_points": width}
    assert "w5" not in p and p["control"] == {"enabled": True}


def test_each_gated_arm_differs_from_its_own_width_only_by_item_41s_live_gate():
    p = st.study_registry(REGISTRY, 0.4)["profiles"]
    live_gate = st.i41.study_registry(REGISTRY, 0.4)["profiles"]["live-1400-multi"]
    want = _diff(live_gate, st.i41.study_registry(REGISTRY, 0.4)["profiles"]["live-1400"])
    assert set(st.GATED) == {f"{a}-multi" for a in st.WIDTHS}
    for gated, width_arm in st.GATED.items():
        extra = _diff(p[gated], p[width_arm])
        assert set(extra) == GATE_KEYS and extra == want


def test_the_placebo_is_w10_with_only_the_random_skip_at_seed_44():
    p = st.study_registry(REGISTRY, 0.37)["profiles"]
    assert p["w10-skip"] == {**p["w10"], "stabilization_placebo_rate": 0.37, "stabilization_placebo_seed": 44}


def test_coverage_reads_the_1400_sixteen_delta_short_and_its_forty_point_wing():
    quotes = [(0, 5000.0, -0.16), (0, 4960.0, -0.02), (1, 5100.0, 0.16)]  # the call's 5140 is missing
    assert st.i42.wing_inside(quotes, st.SHORT_DELTA, st.WIDEST) == {"put": True, "call": False}
    assert (st.HOURS, st.SHORT_DELTA, st.WIDEST) == ((14,), 0.16, 40)


# ── (a) the gate at each width ───────────────────────────────────────────────

W = sessions([10.0, -5.0, 8.0, -3.0] * 50)
GAINS = sessions([25.0, 5.0, 22.0, 10.0] * 50)
FLAT = sessions([0.0] * 200)


def _tests_a(gated, own_width, skip, base, n=1):
    return {
        f"w{10 + 5 * i}-multi": {
            "own": st.i41.own(gated, START, END),
            "vs_base": st.i41.paired(gated, own_width, START, END),
            "vs_placebo": st.placebo_margin(gated, own_width, skip, base, START, END),
        }
        for i in range(n)
    }


def test_a_gate_that_beats_its_width_and_its_placebo_passes():
    tests = _tests_a(GAINS, W, FLAT, FLAT)
    st.judge_a(tests, False)
    assert tests["w10-multi"]["verdict"] == "pass", tests["w10-multi"]["checks"]


def test_a_gate_no_better_than_its_placebo_fails():
    # the placebo gains as much over w10 as the gate does over its width: fewer entries, not timing
    tests = _tests_a(GAINS, W, GAINS, W)
    st.judge_a(tests, False)
    v = tests["w10-multi"]
    assert v["checks"]["paired_t_above_bar"] and not v["checks"]["beats_placebo"] and v["verdict"] == "fail"


def test_the_placebo_bar_also_holds_in_the_holdout():
    tests = _tests_a(GAINS, W, GAINS, W)
    st.judge_a(tests, True)
    v = tests["w10-multi"]
    assert set(v["checks"]) == {"paired_mean_positive", "paired_t_above_bar", "beats_placebo"}
    assert v["verdict"] == "fail"


def test_holm_runs_across_the_seven_gated_arms():
    def passing_but_for(p):
        return {
            "own": {"n": 300, "mean_net": 5.0, "cost_stress": {"survives": True}},
            "vs_base": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
            "vs_placebo": {"mean": 2.0, "t": 2.5},
        }

    one = {"w40-multi": passing_but_for(0.02)}
    st.judge_a(one, False)
    assert one["w40-multi"]["verdict"] == "pass"  # alone, 0.02 clears 0.05
    seven = {g: passing_but_for(0.02) for g in st.GATED}
    st.judge_a(seven, False)
    assert all(not v["checks"]["holm"] and v["verdict"] == "fail" for v in seven.values())


def test_too_few_sessions_is_no_verdict_for_a_gated_arm():
    short = sessions([25.0, 5.0] * 20)
    tests = _tests_a(short, sessions([1.0, 0.0] * 20), FLAT, FLAT)
    st.judge_a(tests, False)
    assert tests["w10-multi"]["verdict"] == "no verdict"


# ── (b) 35 and 40 against 10 ─────────────────────────────────────────────────


def _tests_b(arm, base, arm_risk=800.0, base_risk=800.0, names=("w35",)):
    risk = {"w10": {d: base_risk for d in base}, **{a: {d: arm_risk for d in arm} for a in names}}
    return {
        a: {
            "own": st.i43.own(arm, START, END),
            "vs_w10": st.i43.paired(arm, base, START, END),
            "per_risk": st.i43.per_risk(arm, base, risk, a),
        }
        for a in names
    }


def test_a_wide_wing_that_beats_w10_at_no_more_risk_passes():
    tests = _tests_b(GAINS, W)
    st.judge_b(tests, False)
    assert tests["w35"]["verdict"] == "pass", tests["w35"]["checks"]


def test_a_wide_wing_paying_more_by_risking_more_fails():
    tests = _tests_b(GAINS, W, arm_risk=8000.0)
    st.judge_b(tests, False)
    assert not tests["w35"]["checks"]["net_per_risk_at_least_w10"] and tests["w35"]["verdict"] == "fail"


def test_the_b_holdout_reads_the_paired_mean_and_t_only():
    tests = _tests_b(GAINS, W, arm_risk=8000.0)
    st.judge_b(tests, True)
    assert set(tests["w35"]["checks"]) == {"paired_mean_positive", "paired_t_above_bar"}
    assert tests["w35"]["verdict"] == "pass"
    tests = _tests_b(W, W)
    st.judge_b(tests, True)
    assert tests["w35"]["verdict"] == "fail"


def test_holm_runs_across_the_two_wide_arms():
    def passing_but_for(p):
        return {
            "own": {"mean_net": 5.0, "cost_stress": {"survives": True}},
            "vs_w10": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
            "per_risk": {"at_least_w10": True},
        }

    two = {a: passing_but_for(0.04) for a in st.JUDGED_B}  # 0.04 > 0.05 / 2
    st.judge_b(two, False)
    assert all(v["verdict"] == "fail" for v in two.values())
    two["w40"] = passing_but_for(0.001)
    st.judge_b(two, False)
    assert two["w40"]["verdict"] == "pass"


# ── the store check and the holdout's order ──────────────────────────────────


def test_the_store_check_flags_a_width_that_differs_beyond_noise(monkeypatch):
    theirs = {a: W for a in st.STORE_CHECK}
    monkeypatch.setattr(st.i43, "session_rows", lambda run_id: theirs)
    ours = {**theirs, "w30": GAINS}
    out = st.store_check(ours, {"run_id": "r43"}, START, END)
    assert out["w30"]["beyond_noise"] and not out["w10"]["beyond_noise"]
    assert "note" in st.store_check(ours, None, START, END)


def test_the_holdout_refuses_until_item_43s_holdout_has_run(tmp_path, monkeypatch):
    monkeypatch.setattr(st.paths, "results_dir", lambda: tmp_path)
    assert st.holdout_refusal(st.item43_result("holdout"))
    item43 = tmp_path / "research" / "item43"
    item43.mkdir(parents=True)
    (item43 / "study-holdout-2025-07-01-2026-10-12.json").write_text('{"run_id": "r43h"}', encoding="utf-8")
    assert st.holdout_refusal(st.item43_result("holdout")) is None


def test_the_study_waits_for_coverage_and_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(st.paths, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(st.i41, "require_gate", lambda: None)
    s = {"windows": {"design": [START, END]}, "store": {}}
    with pytest.raises(SystemExit, match="coverage"):
        st.study(s, "design", 1)
    (st.out_dir() / "coverage.json").write_text('{"design": {"wide_sessions": 300}}', encoding="utf-8")
    with pytest.raises(SystemExit, match="plan"):
        st.study(s, "design", 1)
    with pytest.raises(SystemExit, match="item 43"):
        (st.out_dir() / "plan.json").write_text('{"refusal_rate": 0.4}', encoding="utf-8")
        (st.out_dir() / "coverage.json").write_text('{"holdout": {"wide_sessions": 300}}', encoding="utf-8")
        st.study(s, "holdout", 1)
