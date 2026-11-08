"""The study report reads result files into arms, measures and plain words. Every figure here is
invented: real results are licensed and never leave the home."""

from __future__ import annotations

from cherrypick.orchestrator import studyreport as sr


def _meic_file(window: str, gated_both_t: float = 3.0) -> dict:
    # The MEIC study shape: a reference block and arms each with own figures and a paired difference.
    return {
        "window": window,
        "provisional": True,
        "reference": {"base": {"mean_net": 10.0, "t": 2.5, "trades": 90, "n": 100}},
        "arms": {
            "wide": {
                "own": {"mean_net": 12.0, "t": 2.1, "trades": 40, "n": 100},
                "vs_base": {"mean": 2.0, "t": 0.5, "n": 100, "p": 0.3},
                "vs_base_both_entered": {"mean": 20.0, "t": gated_both_t, "n": 40, "p": 0.001},
                "per_risk": {"arm": 0.02, "base": 0.03},
                "verdict": "fail",
            },
        },
        "coverage": {"wide": {"mean": 1.0}},  # a working block, never an arm
    }


def test_arms_reads_own_figures_paired_difference_and_works_out_the_rest():
    rows = {r["arm"]: r for r in sr.arms(_meic_file("design"), "study-design.json")}
    assert set(rows) == {"base", "wide"}
    wide = rows["wide"]
    assert wide["window"] == "design" and wide["mean"] == 12.0 and wide["n"] == 40
    assert wide["paired"]["mean"] == 2.0 and wide["paired_vs"] == "base"
    assert wide["paired_both"]["t"] == 3.0
    assert wide["entry_rate"] == 0.4
    # Net per session counts the 60 sessions with no entry at zero: 12 x 100 / 40 a trade.
    assert wide["net_per_trade"] == 30.0 and wide["net_per_trade_derived"] is True
    assert (wide["per_risk"], wide["per_risk_base"]) == (0.02, 0.03)


def test_arms_reads_design_and_holdout_inside_one_arm_and_a_bootstrap_cell():
    data = {
        "arms": {"a": {"design": {"mean": -5.0, "n": 50}, "holdout": {"mean": -7.0, "n": 20}}},
        "cells": {"c/gated": {"design": {"boot": {"mean": 3.0, "p": 0.4}}, "verdict": "FAIL"}},
    }
    rows = {(r["arm"], r["window"]): r for r in sr.arms(data, "results.json")}
    assert rows[("a", "design")]["mean"] == -5.0 and rows[("a", "holdout")]["mean"] == -7.0
    assert rows[("c/gated", "design")]["mean"] == 3.0 and rows[("c/gated", "design")]["verdict"] == "fail"


def test_an_unknown_shape_yields_no_rows_never_a_guess():
    assert sr.arms({"verdict": "pass", "summary": {"x": {"mean": 1.0}}, "rows": [1, 2]}, "x.json") == []
    assert sr.arms([], "x.json") == []


def test_final_words_keep_a_design_fail_and_let_the_holdout_confirm_a_pass():
    words = sr.final_words(
        [
            ("a", "design", "fail"),
            ("a", "holdout", "pass"),  # a holdout never rescues a design failure
            ("b", "design", "pass"),
            ("b", "holdout", "fail"),
            ("c", "design", "pass"),
            (None, "whole", "no verdict"),
        ]
    )
    assert words == {"a": "fail", "b": "fail", "c": "pass", None: "no verdict"}


def test_reading_names_an_arm_that_trades_better_but_trades_less():
    rows = sr.arms(_meic_file("design"), "study-design.json")
    kinds = [p["kind"] for p in sr.reading({}, rows, [], [])]
    assert "trades_less" in kinds and "entries" in kinds and "risk" in kinds
    # Not said when the both-entered difference is no stronger than the overall one.
    rows = sr.arms(_meic_file("design", gated_both_t=0.4), "study-design.json")
    assert "trades_less" not in [p["kind"] for p in sr.reading({}, rows, [], [])]


def test_reading_leads_with_the_registers_decision_and_labels_the_file_words():
    rows = sr.arms(_meic_file("holdout"), "study-holdout.json")
    paras = sr.reading({"outcome": "wide passes"}, rows, [], [])
    assert paras[0] == {"kind": "decision", "text": "**Decision (the register):** wide passes"}
    tally = next(p["text"] for p in paras if p["kind"] == "tally")
    assert tally.startswith("**As the study's files wrote them:**") and "the decision stands" in tally


def test_report_offers_only_the_measures_it_has_and_the_gates_it_uses():
    rep = sr.report(
        {"title": "the stabilization gate", "queue": {"tasks": []}},
        [{"name": "study-design.json", "data": _meic_file("design")}],
    )
    assert [m["key"] for m in rep["measures"]] == [
        "paired",
        "paired_both",
        "mean",
        "net_per_trade",
        "per_risk",
        "entry_rate",
    ]
    assert [g["key"] for g in rep["gates"]] == ["stabilization"]
    assert rep["provisional"] is True and rep["windows"] == ["design"]
    bare = sr.report({"title": "x"}, [{"name": "results.json", "data": {"arms": {"a": {"mean": 1.0}}}}])
    assert [m["key"] for m in bare["measures"]] == ["mean"] and bare["gates"] == []


def test_a_failed_step_counts_until_a_later_run_of_it_is_done():
    tasks = [
        {"step": "study", "status": "failed", "finished_at": "2026-01-02T10:00:00"},
        {"step": "fetch", "status": "failed", "finished_at": "2026-01-01T10:00:00"},
        {"step": "fetch", "status": "done", "finished_at": "2026-01-01T12:00:00"},
    ]
    assert sr.failed_steps(tasks) == ["study"]


def test_a_retry_named_with_a_number_clears_its_steps_failures():
    tasks = [
        {"step": "study", "status": "failed", "finished_at": "2026-01-02T10:00:00"},
        {"step": "study-2", "status": "failed", "finished_at": "2026-01-02T11:00:00"},
        {"step": "study-3", "status": "done", "finished_at": "2026-01-02T12:00:00"},
        {"step": "fetch-oi", "status": "failed", "finished_at": "2026-01-02T13:00:00"},
    ]
    assert sr.failed_steps(tasks) == ["fetch-oi"]
    assert sr.step_base("fetch-chains-12") == "fetch-chains" and sr.step_base("study") == "study"


def test_a_tests_estimate_is_read_as_its_figure():
    data = {"tests": {"call|rank": {"n": 40, "estimate": 0.06, "p": 0.001, "decision": "pass"}}}
    row = sr.arms(data, "design.json")[0]
    assert (row["arm"], row["window"], row["mean"], row["n"], row["verdict"]) == (
        "call|rank",
        "design",
        0.06,
        40,
        "pass",
    )


def test_digest_lists_recent_studies_with_counts_and_skips_data_checks():
    rows = [
        {
            "id": 2,
            "study": "item2",
            "title": "t",
            "status": "declared",
            "results": [
                {"name": "validation.json", "present": True, "modified": "2026-01-05T09:00:00",
                 "verdicts": [{"arm": None, "verdict": "pass"}]},
                {"name": "results.json", "present": True, "modified": "2026-01-05T10:00:00",
                 "verdicts": [{"arm": "verdict", "verdict": "no verdict"},
                              {"arm": "why", "verdict": "0 trades"}]},
            ],
            "queue": {"tasks": []},
        },
        {"id": 1, "study": "item1", "title": "old", "results": [
            {"name": "results.json", "present": True, "modified": "2025-12-01T10:00:00", "verdicts": []}]},
    ]  # fmt: skip
    d = sr.digest(rows, "2026-01-01")
    assert [s["id"] for s in d["studies"]] == [2]
    assert d["studies"][0]["tally"] == {"pass": 0, "fail": 0, "no_verdict": 1}
    assert d["totals"] == {"pass": 0, "fail": 0, "no_verdict": 1} and d["no_reading"] == []


def test_window_files_are_told_from_working_files():
    assert sr.is_window_file("study-design-2022-05-16-2025-06-30.json")
    assert sr.is_window_file("holdout.json")
    assert not sr.is_window_file("design.version.json")
    assert not sr.is_window_file("chain_requests_SPY.json")
    assert not sr.is_window_file("coverage.json")


def _kinds(advice: list[dict]) -> list[str]:
    assert all(a["kind"] in sr.ADVICE_KINDS and a["text"] and a["why"] for a in advice)
    return [a["kind"] for a in advice]


def test_advice_proposes_a_tick_by_tick_fallback_for_an_arm_that_trades_better_but_less():
    own = sr.arms(_meic_file("design"), "study-design.json")
    adv = sr.advice({}, own, [], [])
    fallback = [a for a in adv if a["kind"] == "new_study"]
    assert len(fallback) == 1 and "**wide**" in fallback[0]["text"] and "tick by tick" in fallback[0]["text"]
    # More dollars, less per dollar of buying power: never adopted on dollars alone.
    assert any(a["kind"] == "ruleset" and "equal buying power" in a["text"] for a in adv)
    weak = sr.arms(_meic_file("design", gated_both_t=0.4), "study-design.json")
    assert "new_study" not in _kinds(sr.advice({}, weak, [], []))


def test_advice_rereads_a_design_pass_that_missed_and_defers_to_the_register():
    verdicts = [
        {"arm": "a", "verdict": "pass", "file": "study-design.json"},
        {"arm": "a", "verdict": "fail", "file": "study-holdout.json"},
    ]
    adv = sr.advice({"outcome": "a passes"}, [], verdicts, [])
    reread = next(a for a in adv if a["kind"] == "reread")
    assert "**a**" in reread["text"] and "the register's outcome decides" in reread["why"]
    assert "ruleset" not in _kinds(adv)  # a holdout miss is no candidate


def test_advice_on_a_module_rule_that_fails_everywhere_names_the_retirement_call():
    verdicts = [{"arm": a, "verdict": "fail", "file": "results.json"} for a in ("x", "y")]
    adv = sr.advice({"title": "The calendars module's own rule at real quotes"}, [], verdicts, [])
    assert any("retiring or redesigning" in a["text"] for a in adv)
    other = sr.advice({"title": "A new entry filter"}, [], verdicts, [])
    assert not any("retiring" in a["text"] for a in other)


def test_advice_treats_one_pass_in_a_big_grid_as_a_lead_and_asks_to_record_a_written_verdict():
    verdicts = [{"arm": f"c{i}", "verdict": "fail", "file": "results.json"} for i in range(11)]
    verdicts.append({"arm": "c99", "verdict": "pass", "file": "results.json"})
    adv = sr.advice({"status": "declared"}, [], verdicts, [])
    assert _kinds(adv) == ["new_study", "register"]
    assert "**c99**" in adv[0]["text"]


def test_advice_for_a_study_with_nothing_to_read_or_a_failed_step():
    assert _kinds(sr.advice({}, [], [], [])) == ["data"]
    assert _kinds(sr.advice({}, [], [], ["study"])) == ["rerun"]
    nothing = [{"arm": None, "verdict": "no verdict", "file": "results.json", "why": "0 trades"}]
    assert _kinds(sr.advice({}, [], nothing, [])) == ["data"]


def test_an_arms_scorecard_rides_through_on_its_row():
    card = {
        "stats": {"total_pl": 120.0, "max_drawdown": 40.0, "return_on_dd": 3.0},
        "insights": [{"key": "longest_drawdown", "text": "Longest drawdown lasted 9 days.", "flag": False}],
    }
    data = {
        "arms": {
            "a": {"design": {"mean": 2.0, "n": 60, "scorecard": card}},
            "b": {"mean": 1.0, "n": 30, "scorecard": card},
            "c": {"mean": 1.0, "n": 30, "scorecard": {"stats": "not a table"}},
        }
    }
    rows = {r["arm"]: r for r in sr.arms(data, "results.json")}
    assert rows["a"]["scorecard"] == card and rows["b"]["scorecard"] == card
    assert "scorecard" not in rows["c"], "another shape is left out, never guessed at"
    # The scorecard's own numbers are never read as the arm's figures.
    assert rows["a"]["mean"] == 2.0 and rows["b"]["n"] == 30
