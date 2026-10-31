"""Hypothesis 40's study (scripts/flies_item40.py): the arms it declares, its pass rule and its rivals.
Invented sessions only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("flies_item40", SCRIPTS / "flies_item40.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-05-16", "2025-06-30"
R = 400  # resamples: enough for the bars below, quick for a test
BASE = {
    "defaults": {"wing_width": 5},
    "arms": {
        "control": {"enabled": True, "entry_windows": [["10:00", "14:30"]], "_note": "x"},
        "gex": {"enabled": False},
    },
}


def days(n):
    d0 = date.fromisoformat(START)
    span = (date.fromisoformat(END) - d0).days
    return [(d0 + timedelta(days=i * span // max(n - 1, 1))).isoformat() for i in range(n)]


def rows(nets, fees=1.0, slippage=0.0, trades=1, start=0):
    return {
        d: {"net": n, "fees": fees, "slippage": slippage, "trades": trades}
        for d, n in zip(days(len(nets) + start)[start:], nets, strict=True)
    }


def window(arm_nets, control_nets, *, other=None, fees=1.0, slippage=0.0, trades=1):
    """Control and both arms over the same sessions; `other` is the second arm's nets."""
    per_arm = {
        "control": rows(control_nets, fees, slippage, trades),
        "strand-stop": rows(arm_nets, fees, slippage, trades),
        "whole-fly": rows(other if other is not None else arm_nets, fees, slippage, trades),
    }
    block = st.analyse(per_arm, START, END, resamples=R)
    return block


def judged(block, window_name="design"):
    st.judge(block, window_name)
    return block["arms"]


STEADY_UP = [30.0, 10.0, 20.0, 15.0] * 100  # 400 sessions
STEADY_CONTROL = [-10.0, -30.0, -20.0, -5.0] * 100  # a losing control: the arm beats it by 40 or more


# --------------------------------------------------------------------------- the arms
def test_the_two_arms_are_control_plus_their_one_lever_and_nothing_else():
    arms = st.study_config(BASE)["arms"]
    assert arms["gex"] == {"enabled": False} and arms["control"] == BASE["arms"]["control"]
    control = {k: v for k, v in BASE["arms"]["control"].items() if not k.startswith("_")}
    assert arms["strand-stop"] == {**control, "strand_stop": {"max_loss_fraction": 0.5}}
    assert arms["whole-fly"] == {**control, "whole_fly": True}
    assert st.STOP_FRACTION == 0.5 and st.ARMS == ("strand-stop", "whole-fly")


def test_a_missing_control_refuses():
    with pytest.raises(SystemExit):
        st.study_config({"arms": {"gex": {}}})


def test_the_config_keys_are_the_ones_flies_reads():
    engine = pytest.importorskip("cherrypick.flies.engine")
    arms = st.study_config(BASE)["arms"]
    params = {**BASE["defaults"], **arms["strand-stop"]}
    assert engine.strand_stop_settings(params) == {"max_loss_fraction": 0.5}
    assert engine.whole_fly_enabled({**BASE["defaults"], **arms["whole-fly"]})
    assert "strand-stop" in engine.ARMS and "whole-fly" in engine.ARMS


# --------------------------------------------------------------------------- the pass rule
def test_a_strong_steady_edge_passes_every_design_check():
    arms = judged(window(STEADY_UP, STEADY_CONTROL))
    for arm in st.ARMS:
        assert arms[arm]["verdict"] == "pass", arms[arm]["checks"]
        assert all(arms[arm]["checks"].values())


def test_beating_a_losing_control_is_not_enough():
    """The paired difference is large and positive; the arm's own mean net is negative."""
    losing = [-5.0, -15.0, -10.0, -2.0] * 100
    arms = judged(window(losing, [-50.0, -60.0, -45.0, -40.0] * 100))
    v = arms["strand-stop"]
    assert v["verdict"] == "fail"
    assert v["checks"]["diff_lower_positive"] and not v["checks"]["own_net_positive"]


def test_a_positive_net_that_does_not_beat_control_fails():
    arms = judged(window([20.0, 10.0, 15.0, 12.0] * 100, [40.0, 30.0, 35.0, 32.0] * 100))
    v = arms["strand-stop"]
    assert v["verdict"] == "fail"
    assert v["checks"]["own_net_positive"] and not v["checks"]["diff_lower_positive"]


def test_a_difference_the_bootstrap_cannot_separate_from_zero_fails():
    """A positive mean difference whose lower bound crosses zero: a coin-flip spread of 1 +/- 200."""
    noisy = [200.0, -198.0, 199.0, -197.0] * 100
    arms = judged(window([x + 20.0 for x in noisy], noisy))
    # exactly +20 every session: the bound is positive (the pairing removes the noise)
    assert arms["strand-stop"]["checks"]["diff_lower_positive"]
    jitter = [(-1) ** i * 400.0 + 1.0 for i in range(400)]
    wobble = [(-1) ** i * 150.0 + 2.0 for i in range(400)]
    arms = judged(window([a + b for a, b in zip(jitter, wobble, strict=True)], jitter))
    assert arms["strand-stop"]["mean_diff"] > 0 and not arms["strand-stop"]["checks"]["diff_lower_positive"]


def test_one_losing_half_fails():
    first = [40.0, 30.0, 35.0, 45.0] * 50
    second = [-5.0, -3.0, -4.0, -2.0] * 50
    arms = judged(window(first + second, STEADY_CONTROL))
    v = arms["strand-stop"]
    assert v["verdict"] == "fail" and not v["checks"]["both_halves"]
    assert v["checks"]["own_net_positive"] and v["checks"]["diff_lower_positive"]


def test_a_thin_edge_that_costs_one_and_a_half_times_eat_fails():
    """Positive and ahead of control at 1.0x; fees and slippage scaled by 1.5 turn the arm negative."""
    thin = [6.0, 4.0, 5.0, 5.0] * 100
    arms = judged(window(thin, STEADY_CONTROL, fees=14.0, slippage=0.0))
    v = arms["strand-stop"]
    assert v["checks"]["diff_lower_positive"] and v["checks"]["own_net_positive"]
    assert not v["checks"]["survives_1_5x"] and v["verdict"] == "fail"


def test_the_stress_charges_slippage_as_well_as_fees():
    """Break the invariant: with slippage left out of `stressed` the same arm would survive."""
    row = {"net": 5.0, "fees": 4.0, "slippage": 20.0}
    assert st.stressed(row) == pytest.approx(5.0 - 0.5 * 24.0)
    assert st.stressed({**row, "slippage": 0.0}) == pytest.approx(5.0 - 0.5 * 4.0)
    arms = judged(window([6.0, 4.0, 5.0, 5.0] * 100, STEADY_CONTROL, fees=2.0, slippage=14.0))
    assert not arms["strand-stop"]["checks"]["survives_1_5x"]  # 5 - 0.5 x 16 < 0
    arms = judged(window([6.0, 4.0, 5.0, 5.0] * 100, STEADY_CONTROL, fees=2.0, slippage=0.0))
    assert arms["strand-stop"]["checks"]["survives_1_5x"]  # 5 - 0.5 x 2 > 0


def test_holm_across_the_two_arms():
    """Shown firing and failing: both p at 0.03 fail Holm (the smaller must clear 0.025); a p of 0.01
    clears it, and then the other is judged at 0.05."""
    block = window(STEADY_UP, STEADY_CONTROL)
    block["arms"]["strand-stop"]["p"], block["arms"]["whole-fly"]["p"] = 0.03, 0.03
    arms = judged(block)
    assert not arms["strand-stop"]["checks"]["holm"] and not arms["whole-fly"]["checks"]["holm"]
    assert arms["strand-stop"]["verdict"] == "fail"
    block = window(STEADY_UP, STEADY_CONTROL)
    block["arms"]["strand-stop"]["p"], block["arms"]["whole-fly"]["p"] = 0.01, 0.04
    arms = judged(block)
    assert arms["strand-stop"]["checks"]["holm"] and arms["whole-fly"]["checks"]["holm"]
    block = window(STEADY_UP, STEADY_CONTROL)
    block["arms"]["strand-stop"]["p"], block["arms"]["whole-fly"]["p"] = 0.01, 0.06
    arms = judged(block)
    assert arms["strand-stop"]["checks"]["holm"] and not arms["whole-fly"]["checks"]["holm"]


def test_fewer_than_300_sessions_with_an_entry_is_no_verdict():
    for entries, verdict in ((299, "no verdict"), (300, "pass")):
        per_arm = {
            "control": rows(STEADY_CONTROL),
            "strand-stop": {
                d: {**r, "trades": 1 if i < entries else 0}
                for i, (d, r) in enumerate(rows(STEADY_UP).items())
            },
            "whole-fly": rows(STEADY_UP),
        }
        arms = judged(st.analyse(per_arm, START, END, resamples=R))
        assert arms["strand-stop"]["entry_sessions"] == entries
        assert arms["strand-stop"]["verdict"] == verdict, entries


def test_the_floor_counts_sessions_with_an_entry_not_sessions_replayed():
    per_arm = {
        "control": rows(STEADY_CONTROL),
        "strand-stop": rows(STEADY_UP, trades=0),
        "whole-fly": rows(STEADY_UP),
    }
    arms = judged(st.analyse(per_arm, START, END, resamples=R))
    assert arms["strand-stop"]["sessions"] == 400 and arms["strand-stop"]["entry_sessions"] == 0
    assert arms["strand-stop"]["verdict"] == "no verdict" and "why" in arms["strand-stop"]


def test_the_holdout_asks_only_for_the_sign_of_the_difference_and_of_the_own_mean():
    block = window(STEADY_UP, STEADY_CONTROL)
    assert judged(block, "holdout")["strand-stop"]["verdict"] == "pass"
    behind = window([20.0, 10.0, 15.0, 12.0] * 100, [40.0, 30.0, 35.0, 32.0] * 100)
    assert judged(behind, "holdout")["strand-stop"]["verdict"] == "fail"
    losing = window([-5.0, -15.0, -10.0, -2.0] * 100, [-50.0, -60.0, -45.0, -40.0] * 100)
    assert judged(losing, "holdout")["strand-stop"]["verdict"] == "fail"


# --------------------------------------------------------------------------- the verdict words
def _block(word):
    return {"arms": {a: {"verdict": word} for a in st.ARMS}}


def test_a_design_fail_stays_a_fail_whatever_the_holdout_shows():
    out = st.final_verdicts({"design": _block("fail"), "holdout": _block("pass")})
    assert out == {"strand-stop": "fail", "whole-fly": "fail"}


def test_a_design_pass_waits_for_its_holdout_then_stands_or_falls_on_it():
    assert st.final_verdicts({"design": _block("pass")}) == {a: "no verdict" for a in st.ARMS}
    assert st.final_verdicts({"design": _block("pass"), "holdout": _block("pass")}) == {
        a: "pass" for a in st.ARMS
    }
    assert st.final_verdicts({"design": _block("pass"), "holdout": _block("fail")}) == {
        a: "fail" for a in st.ARMS
    }
    assert st.final_verdicts({"design": _block("no verdict")}) == {a: "no verdict" for a in st.ARMS}
    assert st.final_verdicts({}) == {a: "no verdict" for a in st.ARMS}


def test_the_holdout_is_read_only_for_arms_that_passed_the_design_window():
    doc = {
        "windows": {
            "design": {"arms": {"strand-stop": {"verdict": "pass"}, "whole-fly": {"verdict": "fail"}}}
        }
    }
    assert st.holdout_arms(doc) == ["strand-stop"]
    assert st.holdout_arms({"windows": {"design": _block("fail")}}) == []
    assert st.holdout_arms({"windows": {"design": _block("no verdict")}}) == []
    assert st.holdout_arms(None) == []


# --------------------------------------------------------------------------- the bootstrap
def test_the_bootstrap_is_seeded_and_series_of_one_length_share_resamples():
    series = {"a": [1.0, -2.0, 3.0, 0.5] * 25, "b": [1.0, -2.0, 3.0, 0.5] * 25}
    one = st.bootstrap(series, resamples=300)
    assert one == st.bootstrap(series, resamples=300)
    assert one["a"] == one["b"]
    shifted = st.bootstrap({"a": [x + 5 for x in series["a"]]}, resamples=300)
    assert shifted["a"]["lower"] > one["a"]["lower"] + 4.9
    shut = st.bootstrap({"a": [x - 5 for x in series["a"]]}, resamples=300)
    assert shut["a"]["p"] == 1.0 and shut["a"]["lower"] < 0


def test_a_series_of_one_session_has_no_bound_and_a_p_of_one():
    assert st.bootstrap({"a": [3.0]}, resamples=50) == {"a": {"p": 1.0, "lower": None}}


# --------------------------------------------------------------------------- the rivals
def test_the_result_without_controls_five_worst_sessions_is_reported():
    control = [-10.0] * 395 + [-1000.0] * 5
    arm = [0.0] * 395 + [-1000.0] * 5
    per_arm = {"control": rows(control), "strand-stop": rows(arm), "whole-fly": rows(arm)}
    v = st.analyse(per_arm, START, END, resamples=R)["arms"]["strand-stop"]
    assert v["mean_diff"] == pytest.approx(9.875, abs=0.01)
    assert v["without_worst_control"]["mean_diff"] == pytest.approx(10.0)
    assert len(v["without_worst_control"]["dropped"]) == 5


def make_ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE fly_positions (arm TEXT, status TEXT, entry_time TEXT, center REAL, "
        "slippage_dollars REAL, close_tag_source TEXT, completed_at TEXT, void_reason TEXT)"
    )
    return conn


def test_the_ledger_facts_count_stops_completions_and_slippage_per_arm():
    conn = make_ledger()
    conn.executemany(
        "INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?,?)",
        [
            ("strand-stop", "settled", "t1", 6000.0, 2.5, "stop", None, None),
            ("strand-stop", "settled", "t2", 6005.0, 1.0, None, "t3", None),
            ("strand-stop", "settled", "t4", 6010.0, 9.0, "stop", None, "voided"),  # voided: out
            ("strand-stop", "open", "t5", 6015.0, 9.0, None, None, None),  # not settled: out
            ("control", "settled", "t1", 6000.0, 2.5, None, "t2", None),
        ],
    )
    facts = st.ledger_facts(conn)
    assert facts["strand-stop"] == {"slippage": 3.5, "stops": [("t1", 6000.0)], "completed": [("t2", 6005.0)]}
    assert facts["control"] == {"slippage": 2.5, "stops": [], "completed": [("t1", 6000.0)]}


def test_the_stop_reading_matches_stopped_verticals_to_what_control_completed():
    completed = {("2024-01-02", "t1", 6000.0), ("2024-01-03", "t9", 6010.0)}
    stops = [("2024-01-02", "t1", 6000.0), ("2024-01-04", "t1", 6000.0)]
    assert st.stop_reading(stops, completed) == {
        "fires": 2,
        "control_completed": 1,
        "share_control_completed": 0.5,
    }
    assert st.stop_reading([], completed) == {
        "fires": 0,
        "control_completed": 0,
        "share_control_completed": None,
    }
    # break the key: a different entry tick on the same centre is a different vertical
    assert st.stop_reading([("2024-01-02", "t2", 6000.0)], completed)["control_completed"] == 0


# --------------------------------------------------------------------------- the output
def results_doc(with_holdout=False):
    design = window(STEADY_UP, STEADY_CONTROL)
    st.judge(design, "design")
    stop = {"fires": 12, "control_completed": 3, "share_control_completed": 0.25}
    design.update(
        {
            "span": [START, END],
            "run_id": "r1",
            "refused_sessions": 2,
            "rivals": {"stop": stop, "ledgers_missing": 0},
        }
    )
    doc = {"item": 40, "windows": {"design": design}}
    if with_holdout:
        hold = window(STEADY_UP, STEADY_CONTROL)
        st.judge(hold, "holdout")
        hold.update(
            {"span": ["2025-07-01", "2026-10-12"], "run_id": "r2", "refused_sessions": 0, "rivals": {}}
        )
        doc["windows"]["holdout"] = hold
    verdict = st.final_verdicts(doc["windows"])
    doc.update({"provisional": True, "verdict": verdict, "window": f"design {START}..{END}", "run_id": "r1"})
    return doc


def test_the_report_states_the_verdict_the_checks_and_the_rivals():
    text = st.report(results_doc(with_holdout=True))
    assert text.startswith("# Item 40")
    assert "**Verdict:** strand-stop pass; whole-fly pass." in text
    assert "### strand-stop: the checks" in text and "Holm across the two arms" in text
    assert "fired 12 times" in text and "control completed 3 of those verticals (25%)" in text
    assert "without worst 5" in text and "## Holdout" in text


def test_the_report_of_a_design_pass_without_its_holdout_says_no_verdict():
    text = st.report(results_doc())
    assert "**Verdict:** strand-stop no verdict; whole-fly no verdict." in text
    assert "## Holdout" not in text


def test_the_results_file_reads_as_a_verdict_per_arm_on_the_studies_board():
    hypotheses = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    doc = json.loads(json.dumps(results_doc(with_holdout=True), default=str))
    assert doc["provisional"] is True and isinstance(doc["window"], str)
    assert hypotheses.verdicts(doc) == [
        {"arm": "strand-stop", "verdict": "pass"},
        {"arm": "whole-fly", "verdict": "pass"},
    ]
