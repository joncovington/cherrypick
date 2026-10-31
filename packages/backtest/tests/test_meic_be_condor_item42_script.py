"""Hypothesis 42's study (scripts/meic_be_condor_item42.py): the arms it declares, the coverage read and
its verdict. Invented sessions and quotes only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_be_condor_item42", SCRIPTS / "meic_be_condor_item42.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-01-03", "2023-12-29"
PUT, CALL = 0, 1
REGISTRY = {"profiles": {"control": {"enabled": True}}}


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


def test_be_is_the_documented_profile_at_its_own_widths():
    p = st.study_registry(REGISTRY, 0.6, st.gated_variants(None))["profiles"]
    assert (
        p["be"]["wing_widths_by_symbol"] == {"SPX": [25, 30, 35, 40]} and p["be"]["equalize_credit"] is True
    )
    assert {k: v for k, v in p["be"].items() if k not in ("wing_widths_by_symbol", "equalize_credit")} == {
        k: v for k, v in p["be10"].items() if k not in ("wing_widths_by_symbol", "equalize_credit")
    }, "be and be10 differ only in the wings"
    assert p["be-skip"] == {**p["be"], "stabilization_placebo_rate": 0.6, "stabilization_placebo_seed": 42}


def test_the_gated_arms_differ_from_be_only_by_the_gate():
    p = st.study_registry(REGISTRY, 0.6, st.gated_variants(None))["profiles"]
    for arm, oracle in (("be-multi", "multi"), ("be-perday", "perday")):
        extra = {k: v for k, v in p[arm].items() if k not in p["be"] or p["be"][k] != v}
        assert extra == {
            "stabilization_entry": True,
            "stabilization_threshold_mode": "atr",
            "stabilization_oracle": oracle,
            "stabilization_bars": 2,
        }


def test_a_passing_item_41_three_bar_variant_replaces_the_multi_arm_it_matches():
    three = {"arms": {"be10-multi": {"verdict": "fail"}, "be10-multi-3b": {"verdict": "pass"}}}
    assert st.gated_variants(three)["be-multi"] == ("multi", 3)
    both = {"arms": {"be10-multi": {"verdict": "pass"}, "be10-multi-3b": {"verdict": "pass"}}}
    assert st.gated_variants(both)["be-multi"] == ("multi", 2), "the two-bar arm passing keeps two bars"
    assert st.gated_variants({"arms": {"be10-multi-3b": {"verdict": "fail"}}})["be-multi"] == ("multi", 2)


def test_a_wing_is_inside_only_when_its_strike_is_quoted():
    # 12-delta put at 5900, call at 6100: wings at 5860 and 6140
    quotes = [(PUT, 5900.0, -0.12), (PUT, 5860.0, -0.04), (CALL, 6100.0, 0.12), (CALL, 6130.0, 0.05)]
    assert st.wing_inside(quotes) == {"put": True, "call": False}
    assert st.wing_inside([(PUT, 5900.0, None), (CALL, 6100.0, 0.12), (CALL, 6140.0, 0.03)]) == {
        "put": None,
        "call": True,
    }, "a side with no delta cannot pick a short, so it is unknown, not outside"


def _tests(be, gated):
    return {
        "be": {"own": st.i41.own(be, START, END)},
        **{
            a: {"base": "be", "own": st.i41.own(s, START, END), "vs_base": st.i41.paired(s, be, START, END)}
            for a, s in gated.items()
        },
    }


EARNS = sessions([25.0, 5.0, 22.0, 10.0] * 50)
FLAT = sessions([10.0, -12.0, 8.0, -6.0] * 50)


def test_be_passes_on_its_own_net_and_fails_when_it_does_not_earn():
    tests = _tests(EARNS, {"be-multi": EARNS, "be-perday": EARNS})
    st.judge(tests, False)
    assert tests["be"]["verdict"] == "pass", tests["be"]["checks"]
    assert tests["be-multi"]["verdict"] == "fail", "equal to be is no gain"
    tests = _tests(FLAT, {"be-multi": FLAT, "be-perday": FLAT})
    st.judge(tests, False)
    assert tests["be"]["verdict"] == "fail" and not tests["be"]["checks"]["own_t_above_bar"]


def test_a_gated_arm_passes_on_item_41s_rule_against_be():
    better = sessions([40.0, 22.0, 35.0, 27.0] * 50)
    tests = _tests(EARNS, {"be-multi": better, "be-perday": EARNS})
    st.judge(tests, False)
    assert tests["be-multi"]["verdict"] == "pass", tests["be-multi"]["checks"]
    assert set(tests["be-multi"]["checks"]) >= {"paired_t_above_bar", "holm", "own_survives_1_5x_fees"}


def test_holm_runs_across_all_three_arms():
    def at(p):
        return {"n": 300, "mean": 2.0, "t": 2.1, "p": p, "halves": {"first": 1.0, "second": 3.0}}

    own = {"n": 300, "mean_net": 5.0, "t": 2.1, "halves": {"first": 1.0, "second": 3.0}}
    own["cost_stress"] = {"survives": True}
    tests = {
        "be": {"own": own},
        "be-multi": {"own": own, "vs_base": at(0.02)},
        "be-perday": {"own": own, "vs_base": at(0.02)},
    }
    st.judge(tests, False)  # 0.02 clears 0.05 alone but not 0.05 / 3
    assert not tests["be-multi"]["holm_ok"] and tests["be-multi"]["verdict"] == "fail"
    tests["be-multi"]["vs_base"] = at(0.001)
    st.judge(tests, False)
    assert tests["be-multi"]["verdict"] == "pass"


def test_the_holdout_reads_be_s_own_mean_and_t_and_the_gated_arms_paired():
    tests = _tests(EARNS, {"be-multi": sessions([40.0, 22.0, 35.0, 27.0] * 50), "be-perday": EARNS})
    st.judge(tests, True)
    assert set(tests["be"]["checks"]) == {"own_mean_positive", "own_t_above_bar"}
    assert set(tests["be-multi"]["checks"]) == {"paired_mean_positive", "paired_t_above_bar"}
    assert tests["be"]["verdict"] == tests["be-multi"]["verdict"] == "pass"


def test_too_few_sessions_is_no_verdict():
    short = sessions([25.0, 5.0] * 20)
    tests = _tests(short, {"be-multi": short, "be-perday": short})
    st.judge(tests, False)
    assert {v["verdict"] for v in tests.values()} == {"no verdict"}


def test_the_study_waits_for_a_coverage_read_with_wide_sessions_in_its_window():
    assert "coverage first" in st.coverage_refusal(None, "design")
    empty = {"design": {"plain_sessions": 778, "wide_sessions": 0}}
    assert "no design session" in st.coverage_refusal(empty, "design")
    assert st.coverage_refusal({"design": {"wide_sessions": 778}}, "design") is None
    assert st.coverage_refusal({"design": {"wide_sessions": 778}}, "holdout") is not None
