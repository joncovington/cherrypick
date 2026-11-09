"""Hypothesis 50's study (packages/backtest/scripts/meic_wing_width_gated_item50.py): the arms it declares and
its verdict. Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_gated_item50", SCRIPTS / "meic_wing_width_gated_item50.py"
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
    "low_iv_min_credit_pct_of_width": 0.10,
    "max_call_delta_entry": 0.2,
    "max_call_delta_entry_late": 0.19,
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}


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


def test_live_1400_is_item_43s_and_the_arms_keep_its_gates():
    p = st.study_registry(REGISTRY)["profiles"]
    assert p["control"] == {"enabled": True} and p["pm-200"] == PM200
    want = {k: v for k, v in PM200.items() if not k.startswith("_")}
    want.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30")
    assert p["live-1400"] == want
    assert (
        st.study_registry(REGISTRY)["profiles"]["live-1400"]
        == st.i43.study_registry(REGISTRY)["profiles"]["live-1400"]
    )
    for name, width in st.GRID.items():
        assert p[name] == {**want, "wing_width_points": width}
        assert p[name]["min_credit_pct_of_width"] == 0.15 and p[name]["max_call_delta_entry"] == 0.2
    assert st.GRID == {"g15": 15, "g20": 20} and st.UNGATED_TWIN == {"g15": "w15", "g20": "w20"}


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}})


def _judged(arm, base, arm_risk=800.0, base_risk=800.0):
    risk = {"g20": {d: arm_risk for d in arm}, "live-1400": {d: base_risk for d in base}}
    tests = {
        "g20": {
            "own": st.i43.own(arm, START, END),
            "vs_base": st.i43.paired(arm, base, START, END),
            "per_risk": st.per_risk(arm, base, risk, "g20"),
        }
    }
    st.judge(tests)
    return tests["g20"]


BASE = sessions([10.0, -5.0, 8.0, -3.0] * 50)
BETTER = sessions([25.0, 5.0, 22.0, 10.0] * 50)


def test_a_steady_gain_over_live_at_no_more_risk_passes():
    v = _judged(BETTER, BASE)
    assert v["verdict"] == "pass", v["checks"]


def test_paying_more_by_risking_more_fails_on_net_per_risk():
    v = _judged(BETTER, BASE, arm_risk=8000.0, base_risk=800.0)
    assert v["checks"]["paired_t_above_bar"] and not v["checks"]["net_per_risk_at_least_base"]
    assert v["verdict"] == "fail"


def test_an_arm_no_better_than_live_fails_on_the_paired_mean():
    v = _judged(BASE, BASE)
    assert v["verdict"] == "fail" and not v["checks"]["paired_mean_positive"]


def test_beating_live_while_losing_on_its_own_fails():
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


def test_a_noisy_gain_fails_on_the_t_bar():
    arm = sessions([60.0, -50.0, 55.0, -45.0] * 50)
    v = _judged(arm, BASE)
    assert v["checks"]["paired_mean_positive"] and not v["checks"]["paired_t_above_bar"]
    assert v["verdict"] == "fail"


def test_too_few_sessions_is_no_verdict():
    v = _judged(sessions([25.0, 5.0] * 20), sessions([1.0, 0.0] * 20))
    assert v["verdict"] == "no verdict"


def _passing_but_for(p):
    return {
        "own": {"mean_net": 5.0, "cost_stress": {"survives": True}},
        "vs_base": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
        "per_risk": {"at_least_base": True},
    }


def test_holm_across_the_two_arms_rejects_what_one_test_alone_would_pass():
    tests = {"g15": _passing_but_for(0.03), "g20": _passing_but_for(0.04)}
    st.judge(tests)
    assert [tests[a]["verdict"] for a in ("g15", "g20")] == ["fail", "fail"]
    tests = {"g15": _passing_but_for(0.01), "g20": _passing_but_for(0.02)}
    st.judge(tests)
    assert [tests[a]["verdict"] for a in ("g15", "g20")] == ["pass", "pass"]


def test_the_holdout_run_leaves_out_live_1400_whose_holdout_item_23_spent():
    assert st.run_arms(False) == ["live-1400", "g15", "g20"]
    assert st.run_arms(True) == ["g15", "g20"]


def test_the_ungated_twins_are_read_from_item_43s_own_run(tmp_path, monkeypatch):
    item43 = tmp_path / "research" / "item43"
    item43.mkdir(parents=True)
    (item43 / "study-design-2022-05-16-2025-06-30.json").write_text('{"run_id": "r43"}', encoding="utf-8")
    (item43 / "study-design-2022-05-16-2025-06-30.version.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(st.paths, "results_dir", lambda: tmp_path)
    w15 = {"2022-06-01": {"net": 5.0, "fees": 1.0, "trades": 1}}
    rows = {"r43": {"w15": w15, "w25": {}}}
    monkeypatch.setattr(st.i43, "session_rows", lambda run_id: rows.get(run_id, {}))
    assert st.item43_sessions("design") == ({"w15": w15, "w20": {}}, "r43")
    assert st.item43_sessions("holdout") == ({}, None)


def test_coverage_counts_sessions_the_floor_or_the_store_cost_each_arm():
    base = {"d1": {"trades": 1}, "d2": {"trades": 1}, "d3": {"trades": 0}}
    per_arm = {"g15": {"d1": {"trades": 1}, "d2": {"trades": 0}, "d3": {"trades": 0}}, "g20": {}}
    rows = [{"arm": "g15", "unmarked_iterations": 2}, {"arm": "g15", "unmarked_iterations": 0}]
    got = st.coverage(rows, per_arm, base, ["g15", "g20"])
    keys = ("sessions_entered", "base_entered_arm_did_not", "entries_with_unquoted_ticks")
    assert got["g15"] == dict(zip(keys, (1, 1, 1), strict=True))
    assert got["g20"] == dict(zip(keys, (0, 2, 0), strict=True))


def test_the_grid_block_has_live_1400_as_the_declared_width():
    doc = {
        "arms": {a: {"own": {"mean_net": 1.0, "n": 100}, "verdict": "fail"} for a in st.GRID},
        "reference": {st.BASE: {"mean_net": 2.0}},
    }
    g = st.grid_block(doc)
    assert [r["setting"] for r in g["rows"]] == [10, 15, 20]
    assert g["declared"] == "live-1400" and all(r["mean"] is not None for r in g["rows"])
