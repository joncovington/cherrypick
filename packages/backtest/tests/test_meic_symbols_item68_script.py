"""Hypothesis 68's study (scripts/meic_symbols_item68.py): the arms it declares, its coverage reads and
its verdict. Invented sessions and quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.backtest import store

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_symbols_item68", SCRIPTS / "meic_symbols_item68.py")
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


def test_each_arm_is_live_1400_on_its_symbol_at_its_width_with_every_gate():
    for symbol, arms in st.ARMS.items():
        p = st.study_registry(REGISTRY, symbol, natural=False)["profiles"]
        want = {k: v for k, v in PM200.items() if not k.startswith("_")}
        want.update(
            enabled=True, paper_entry_window_start="14:00", entry_window_end="14:30", symbols=[symbol]
        )
        for name, width in arms.items():
            assert p[name] == {**want, "wing_width_points": width}
            assert name + st.NATURAL_SUFFIX not in p
        assert p["pm-200"] == PM200  # the machine's arm is never changed
        assert p["control"] == {"enabled": True, "symbols": [symbol]}  # the runner's pair trades the symbol
    assert st.ARMS == {"QQQ": {"q1": 1, "q5": 5}, "NDX": {"n40": 40, "n10": 10}}


def test_the_natural_fill_twins_differ_only_by_their_fill_and_only_in_the_design_window():
    p = st.study_registry(REGISTRY, "NDX", natural=True)["profiles"]
    for name in st.ARMS["NDX"]:
        assert p[name + st.NATURAL_SUFFIX] == {**p[name], "slippage_frac_of_spread": 0.5}


def test_a_missing_base_arm_refuses():
    with pytest.raises(SystemExit):
        st.study_registry({"profiles": {"control": {}}}, "QQQ", natural=False)


def test_a_wing_is_inside_only_when_its_strike_is_quoted():
    P, C = store.SIDES["PUT"], store.SIDES["CALL"]
    quotes = [(P, 590.0, -0.16), (P, 589.0, -0.14), (C, 610.0, 0.16), (C, 615.0, 0.08)]
    assert st.wing_inside(quotes, 1.0) == {"put": True, "call": False}
    assert st.wing_inside(quotes, 5.0) == {"put": False, "call": True}
    assert st.wing_inside([(P, 590.0, None)], 1.0) == {"put": None, "call": None}
    assert st.spacing([(P, 590.0, None), (P, 591.0, None), (C, 600.0, None)], 595.0) == 1.0
    assert st.spacing(quotes, None) is None


def test_the_study_waits_for_a_coverage_read_of_both_symbols():
    assert "coverage first" in st.coverage_refusal(None, "design")
    doc = {"symbols": {"QQQ": {"design": {"sessions": 300}}, "NDX": {"design": {"sessions": 0}}}}
    assert "NDX" in st.coverage_refusal(doc, "design") and "QQQ" not in st.coverage_refusal(doc, "design")
    doc["symbols"]["NDX"]["design"]["sessions"] = 280
    assert st.coverage_refusal(doc, "design") is None


def _tests(nets_by_arm, fees=1.0):
    return {a: {"own": st.own(sessions(n, fees=fees), START, END)} for a, n in nets_by_arm.items()}


def test_a_steady_edge_passes_and_each_bar_fails_alone():
    good = [5.0 + (i % 7) for i in range(120)]
    t = _tests({"q1": good, "q5": good, "n40": good, "n10": good})
    st.judge(t, holdout=False)
    assert all(v["verdict"] == "pass" for v in t.values())

    few = _tests({"q1": good[:99]})
    st.judge(few, holdout=False)
    assert few["q1"]["verdict"] == "no verdict"

    noisy = _tests({"q1": [(-1) ** i * 50.0 + 1.0 for i in range(120)]})
    st.judge(noisy, holdout=False)
    assert not noisy["q1"]["checks"]["t_above_bar"]

    late = _tests({"q1": [0.0] * 60 + [9.0] * 60})  # all of it in the second half
    st.judge(late, holdout=False)
    assert not late["q1"]["checks"]["both_halves_positive"]

    thin = _tests({"q1": [1.0 + (i % 3) * 0.1 for i in range(120)]}, fees=4.0)  # gone at 1.5x fees
    st.judge(thin, holdout=False)
    assert not thin["q1"]["checks"]["survives_1_5x_fees"] and thin["q1"]["verdict"] == "fail"


def test_holm_spans_the_four_arms_and_the_holdout_reads_mean_and_t_only():
    marginal = [(1.0 if i % 2 else 0.0) + 0.05 for i in range(120)]
    t = _tests({"q1": marginal, "q5": [0.0] * 120, "n40": [0.0] * 120, "n10": [0.0] * 120})
    st.judge(t, holdout=False)
    assert set(t["q1"]["checks"]) >= {"holm", "both_halves_positive", "survives_1_5x_fees"}
    h = _tests({"q1": [0.0] * 60 + [9.0] * 60})
    st.judge(h, holdout=True)
    assert set(h["q1"]["checks"]) == {"mean_positive", "t_above_bar"}


def test_net_over_buying_power_and_correlation_read_only_shared_sessions():
    a, b = sessions([10.0, -5.0, 20.0, 0.0]), sessions([1.0, 2.0, 3.0, 4.0])
    risk = {d: 100.0 for d in a}
    assert st.per_risk(a, risk, sorted(a)) == round(25.0 / 400.0, 5)
    assert st.per_risk(a, {}, sorted(a)) is None
    assert st.correlation(a, b)["n"] == 4
    assert st.correlation(a, sessions([0.0] * 4))["r"] is None  # a flat series has none
