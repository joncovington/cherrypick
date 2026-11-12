"""Hypothesis 72's study (packages/backtest/scripts/meic_xsp_item72.py): the grid it declares, its pairing
with the live rule, its verdict, the sizing read and the kill block. Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.backtest import store

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_xsp_item72", SCRIPTS / "meic_xsp_item72.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-01-03", "2023-12-29"
PM200 = {
    "_note": "x",
    "enabled": False,
    "symbols": ["SPX"],
    "short_delta_target": 0.16,
    "paper_entry_window_start": "12:00",
    "entry_window_end": "14:30",
    "min_credit_pct_of_width": 0.15,
    "per_side_stop_management": True,
    "stop_trigger_ratio": 2.0,
    "wing_width_points": 10,
}
REGISTRY = {"profiles": {"pm-200": PM200, "control": {"enabled": True}}}


def days(n):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return [(d0 + timedelta(days=i * span // max(n - 1, 1))).isoformat() for i in range(n)]


def sessions(nets, fees=1.0, trades=1):
    return {d: {"net": x, "fees": fees, "trades": trades} for d, x in zip(days(len(nets)), nets, strict=True)}


def noisy(mean, n=150, sd=10.0, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, sd) for _ in range(n)]


# ── the grid ─────────────────────────────────────────────────────────────────


def test_the_grid_is_four_stops_by_four_widths_paired_with_the_live_rule():
    assert st.STOPS == {"none": None, "s10": 1.0, "s15": 1.5, "s20": 2.0}
    assert st.WIDTHS == (1, 2, 3, 4)
    assert len(st.ARMS) == 16 and st.REFERENCE == "s20-w1" and st.ARMS[st.REFERENCE] == ("s20", 1)
    assert len(st.CHALLENGERS) == 15 and st.REFERENCE not in st.CHALLENGERS


def test_each_arm_is_live_on_xsp_at_its_width_with_its_stop_and_every_gate():
    p = st.study_registry(REGISTRY, stress=False)["profiles"]
    base = {k: v for k, v in PM200.items() if not k.startswith("_")}
    base.update(enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30", symbols=["XSP"])
    for name, (stop, width) in st.ARMS.items():
        arm = p[name]
        assert arm["wing_width_points"] == width and arm["symbols"] == ["XSP"]
        assert arm["min_credit_pct_of_width"] == 0.15  # the live gates stay on
        if stop == "none":
            assert arm["per_side_stop_management"] is False
            assert "stop_resting" not in arm
        else:
            assert arm["per_side_stop_management"] is True
            assert arm["stop_trigger_ratio"] == st.STOPS[stop]
            assert arm["stop_resting"] is True and arm["stop_resting_limit_frac"] == 0.1
    assert p["pm-200"] == PM200  # the machine's arm is never changed
    assert p["control"] == {"enabled": True, "symbols": ["XSP"]}
    assert st.REFERENCE + st.HALFWAY_SUFFIX not in p


def test_the_halfway_twin_is_the_reference_alone_and_only_in_the_design_window():
    p = st.study_registry(REGISTRY, stress=True)["profiles"]
    assert p[st.REFERENCE + st.HALFWAY_SUFFIX] == {**p[st.REFERENCE], "slippage_frac_of_spread": 0.25}
    assert [k for k in p if k.endswith(st.HALFWAY_SUFFIX)] == [st.REFERENCE + st.HALFWAY_SUFFIX]


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}}, stress=False)


def test_a_wing_is_inside_only_when_its_strike_is_quoted():
    P, C = store.SIDES["PUT"], store.SIDES["CALL"]
    quotes = [(P, 590.0, -0.16), (P, 589.0, -0.14), (C, 610.0, 0.16), (C, 613.0, 0.08)]
    assert st.wing_inside(quotes, 1.0) == {"put": True, "call": False}
    assert st.wing_inside(quotes, 3.0) == {"put": False, "call": True}


def test_coverage_refuses_before_a_read_and_on_an_empty_window():
    assert "coverage first" in st.coverage_refusal(None, "design")
    assert "no design session" in st.coverage_refusal({"windows": {"design": {"sessions": 0}}}, "design")
    assert st.coverage_refusal({"windows": {"design": {"sessions": 5}}}, "design") is None


# ── pairing and the verdict ──────────────────────────────────────────────────


def test_pairing_counts_a_session_one_arm_has_no_row_for_at_zero():
    a = {"2022-01-03": {"net": 5.0}, "2022-01-04": {"net": 1.0}}
    ref = {"2022-01-03": {"net": 2.0}, "2022-01-05": {"net": 4.0}}
    pr = st.paired(a, ref, START, END)
    assert pr["n"] == 3
    assert pr["mean_diff"] == round((3.0 + 1.0 - 4.0) / 3, 2)


def _tests(arm_nets, ref_nets, *, width=1, bp=(0.02, 0.01)):
    mine, ref = sessions(arm_nets), sessions(ref_nets)
    return {
        "x": {
            "width": width,
            "own": st.own(mine, START, END),
            "paired": st.paired(mine, ref, START, END),
            "net_over_bp": bp[0],
            "reference_net_over_bp": bp[1],
        }
    }


def test_the_reference_passes_on_a_clear_edge_and_fails_without_one():
    good = {"own": st.own(sessions(noisy(8.0)), START, END)}
    st.judge(good, {}, holdout=False)
    assert good["verdict"] == "pass"
    bad = {"own": st.own(sessions(noisy(-3.0)), START, END)}
    st.judge(bad, {}, holdout=False)
    assert bad["verdict"] == "fail" and not bad["checks"]["mean_positive"]


def test_the_reference_needs_both_halves_positive():
    nets = [20.0] * 75 + [-5.0] * 75  # positive overall, negative in the second half
    ref = {"own": st.own(sessions(nets), START, END)}
    st.judge(ref, {}, holdout=False)
    assert not ref["checks"]["both_halves_positive"] and ref["verdict"] == "fail"


def test_a_challenger_passes_only_on_a_paired_lead():
    ref = noisy(2.0, seed=3)
    lead = [x + 6.0 + random.Random(i).gauss(0, 1) for i, x in enumerate(ref)]
    t = _tests(lead, ref)
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert t["x"]["verdict"] == "pass"
    t = _tests([x - 1.0 for x in ref], ref)
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert t["x"]["verdict"] == "fail" and not t["x"]["checks"]["diff_positive"]


def test_a_wider_challenger_must_earn_at_least_the_reference_per_dollar_at_risk():
    ref = noisy(2.0, seed=4)
    lead = [x + 6.0 for x in ref]
    t = _tests(lead, ref, width=3, bp=(0.005, 0.01))
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert t["x"]["checks"]["net_over_bp_at_least_reference"] is False and t["x"]["verdict"] == "fail"
    t = _tests(lead, ref, width=3, bp=(0.02, 0.01))
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert t["x"]["checks"]["net_over_bp_at_least_reference"] is True
    t = _tests(lead, ref, width=1, bp=(0.005, 0.01))
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert "net_over_bp_at_least_reference" not in t["x"]["checks"]  # the same width risks the same


def test_holm_runs_across_the_challengers():
    ref = noisy(2.0, n=150, seed=5)
    weak = [x + 0.9 + random.Random(i).gauss(0, 6) for i, x in enumerate(ref)]
    t = {f"a{i}": _tests(weak, ref)["x"] for i in range(15)}
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=False)
    assert all(v["holm_ok"] is False for v in t.values())


def test_under_a_hundred_sessions_is_no_verdict():
    ref = {"own": st.own(sessions(noisy(8.0, n=60)), START, END)}
    st.judge(ref, {}, holdout=False)
    assert ref["verdict"] == "no verdict"


def test_the_holdout_judges_the_mean_and_t_alone():
    ref = noisy(2.0, seed=6)
    t = _tests([x + 6.0 for x in ref], ref, width=4, bp=(0.0, 1.0))
    st.judge({"own": st.own(sessions(ref), START, END)}, t, holdout=True)
    assert set(t["x"]["checks"]) == {"diff_positive", "t_above_bar"} and t["x"]["verdict"] == "pass"


# ── sizing, stops and the kill block ─────────────────────────────────────────


def _trades(nets):
    return [{"net_pnl": x, "session": d} for d, x in zip(days(len(nets)), nets, strict=True)]


def test_sizing_reads_shares_of_the_capital_given():
    nets = noisy(3.0, n=150, sd=20.0, seed=7)
    s = st.sizing(_trades(nets), sessions(nets), capital=1000.0)
    assert s["capital"] == 1000.0
    assert s["worst_session"] == min(nets)
    assert s["worst_session_share"] == round(-min(nets) / 1000.0, 4)
    assert s["drawdown_p99"] and s["drawdown_p99_share"] == round(s["drawdown_p99"] / 1000.0, 4)
    assert s["max_condors"] == int(1000.0 * st.SIZING_SHARE // s["drawdown_p99"])


def test_sizing_on_too_few_trades_says_so():
    s = st.sizing(_trades([1.0, -2.0]), sessions([1.0, -2.0]), capital=1000.0)
    assert s["insufficient"] is True and s["drawdown_p99"] is None and s["max_condors"] == 0


def test_a_side_counts_as_stopped_by_its_stop_cost():
    rows = [
        {"arm": "a", "put_stop_cost": 1.2, "call_stop_cost": None},
        {"arm": "a", "put_stop_cost": None, "call_stop_cost": None},
        {"arm": "b", "put_stop_cost": None, "call_stop_cost": 0.9},
    ]
    assert st.stopped_share(rows, "a") == 0.5
    assert st.stopped_share(rows, "b") == 1.0
    assert st.stopped_share(rows, "c") is None


def test_the_kill_block_has_the_live_config_shape():
    nets = noisy(3.0, n=150, sd=20.0, seed=8)
    block = st.kill_block(_trades(nets), ("2022-05-16", "2026-10-12"), ["r1", "r2"])
    for key in ("written", "evidence", "drawdown_reduce", "drawdown_block", "streak_reduce", "streak_block"):
        assert key in block
    assert "s20-w1" in block["evidence"] and "r1, r2" in block["evidence"]
    assert block["drawdown_block"] >= block["drawdown_reduce"]


def test_the_study_step_needs_a_capital(capsys):
    with pytest.raises(SystemExit):
        st.main(["--step", "study", "--window", "design"])
    assert "--capital" in capsys.readouterr().err
