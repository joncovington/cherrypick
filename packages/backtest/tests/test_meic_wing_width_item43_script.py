"""Hypothesis 43's study (scripts/meic_wing_width_item43.py): the arms it declares and its verdict.
Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_wing_width_item43", SCRIPTS / "meic_wing_width_item43.py"
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


def test_live_1400_is_pm200_in_the_1400_window_with_its_gates():
    p = st.study_registry(REGISTRY)["profiles"]
    assert p["control"] == {"enabled": True} and p["pm-200"] == PM200
    want = {k: v for k, v in PM200.items() if not k.startswith("_")}
    want.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30")
    assert p["live-1400"] == want


def test_the_arms_differ_from_w10_only_by_their_width():
    p = st.study_registry(REGISTRY)["profiles"]
    assert p["w10"] == {**p["live-1400"], **st.UNGATED, "wing_width_points": 10}
    assert st.UNGATED["min_credit_pct_of_width"] == 0 and st.UNGATED["low_iv_min_credit_pct_of_width"] == 0
    assert all(st.UNGATED[k] >= 1 for k in st.UNGATED if k.startswith("max_call_delta"))
    for name, width in {**st.GRID, **st.REPORTED}.items():
        assert p[name] == {**p["w10"], "wing_width_points": width}
    assert st.GRID == {"w15": 15, "w20": 20, "w25": 25, "w30": 30} and st.REPORTED == {"w5": 5}


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}})


def _judged(arm, base, arm_risk=800.0, base_risk=800.0):
    risk = {"w20": {d: arm_risk for d in arm}, "w10": {d: base_risk for d in base}}
    tests = {
        "w20": {
            "own": st.own(arm, START, END),
            "vs_w10": st.paired(arm, base, START, END),
            "per_risk": st.per_risk(arm, base, risk, "w20"),
        }
    }
    st.judge(tests)
    return tests["w20"]


BASE = sessions([10.0, -5.0, 8.0, -3.0] * 50)
BETTER = sessions([25.0, 5.0, 22.0, 10.0] * 50)


def test_a_steady_gain_over_w10_at_no_more_risk_passes():
    v = _judged(BETTER, BASE)
    assert v["verdict"] == "pass", v["checks"]


def test_paying_more_by_risking_more_fails_on_net_per_risk():
    v = _judged(BETTER, BASE, arm_risk=8000.0, base_risk=800.0)
    assert v["checks"]["paired_t_above_bar"] and not v["checks"]["net_per_risk_at_least_w10"]
    assert v["verdict"] == "fail"


def test_an_arm_no_better_than_w10_fails_on_the_paired_mean():
    v = _judged(BASE, BASE)
    assert v["verdict"] == "fail" and not v["checks"]["paired_mean_positive"]


def test_beating_w10_while_losing_on_its_own_fails():
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
        "vs_w10": {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}},
        "per_risk": {"at_least_w10": True},
    }


def test_holm_across_the_four_arms_rejects_what_one_test_alone_would_pass():
    tests = {a: _passing_but_for(0.02) for a in st.GRID}  # t above 2, but 0.02 > 0.05 / 4
    st.judge(tests)
    assert all(not v["holm_ok"] and v["verdict"] == "fail" for v in tests.values())
    tests = {**{a: _passing_but_for(0.02) for a in st.GRID}, "w20": _passing_but_for(0.001)}
    st.judge(tests)
    assert tests["w20"]["verdict"] == "pass" and tests["w30"]["verdict"] == "fail"


def _ledger(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (arm TEXT, trade_date TEXT, wing_width REAL, net_credit REAL, "
        "quantity INTEGER, unmarked_iterations INTEGER)"
    )
    conn.executemany("INSERT INTO ic_trades VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_risk_is_width_less_credit_per_condor_summed_by_session(tmp_path):
    _ledger(
        tmp_path / "2022-01-03.db",
        [("w20", "2022-01-03", 20, 2.5, 1, 0), ("w20", "2022-01-03", 20, 1.5, 2, 0)],
    )
    risk = st.risk_by_session(st.ledger_rows(tmp_path))
    assert risk == {"w20": {"2022-01-03": 1750.0 + 3700.0}}


def test_coverage_counts_what_the_store_cost_each_arm(tmp_path):
    _ledger(tmp_path / "a.db", [("w30", "2022-01-03", 30, 2.0, 1, 4), ("w10", "2022-01-03", 10, 1.0, 1, 0)])
    per_arm = {
        "w10": {"2022-01-03": {"trades": 1}, "2022-01-04": {"trades": 1}},
        "w30": {"2022-01-03": {"trades": 1}, "2022-01-04": {"trades": 0}},
    }
    cov = st.coverage(st.ledger_rows(tmp_path), per_arm, ["w10", "w30"])
    assert cov["w30"] == {
        "sessions_entered": 1,
        "w10_entered_arm_did_not": 1,
        "entries_with_unquoted_ticks": 1,
    }
    assert cov["w10"] == {
        "sessions_entered": 2,
        "w10_entered_arm_did_not": 0,
        "entries_with_unquoted_ticks": 0,
    }
