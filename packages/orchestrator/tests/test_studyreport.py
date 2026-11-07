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
