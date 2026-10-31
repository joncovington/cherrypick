"""B7/B14: the hypothesis register is well formed, finds rejected look-alikes, and plans re-tests."""

from __future__ import annotations

import copy

from cherrypick.orchestrator import hypotheses as hy

NEW = {
    "id": 18,
    "study": "item18",
    "title": "x",
    "status": "declared",
    "setup": "down-day",
    "side": "bullish",
    "structure": "put-bwb",
    "window": "weekly",
    "measure": "net per trade at q25",
    "expected": "> 0.05R",
    "sample": "2022-2025, >= 300 trades",
    "rule": "lower bound > 0 in both halves",
    "mechanism": "fear premium after a down day",
    "rivals": [{"explanation": "market drift", "check": "same entries on up days"}],
    "pointer": "docs/edge-hypotheses-plan.md#the-rule-this-plan-follows",
}


def test_the_repo_register_is_sound():
    # Driven off the file itself: every row, every pointer, as checked in.
    assert hy.check(hy.load()) == []


def test_a_new_row_must_state_its_parts_and_rivals():
    reg = {"hypotheses": [NEW]}
    assert hy.check(reg) == []
    bare = {k: v for k, v in NEW.items() if k not in ("rule", "rivals")}
    problems = hy.check({"hypotheses": [bare]})
    assert any("must state rule" in p for p in problems)
    assert any("rival explanations" in p for p in problems)


def test_bad_status_pointer_and_duplicates_are_caught():
    rows = [copy.deepcopy(NEW), {**NEW, "status": "maybe", "pointer": "docs/edge-hypotheses-plan.md#nope"}]
    problems = hy.check({"hypotheses": rows})
    assert any("unique integer" in p for p in problems)
    assert any("not one of" in p for p in problems)
    assert any("names no heading" in p for p in problems)


def test_a_verdict_names_its_results_under_the_home():
    done = {**NEW, "status": "failed", "results": ["{home}/data/backtest/results/research/item18/study.json"]}
    assert hy.check({"hypotheses": [done]}) == []
    # Broken on purpose: a verdict with no result, and a result outside the home.
    assert any("names its result files" in p for p in hy.check({"hypotheses": [{**done, "results": None}]}))
    assert any("{home}/" in p for p in hy.check({"hypotheses": [{**done, "results": ["docs/x.json"]}]}))
    # A declared row has no result yet, and a legacy verdict may have none.
    assert hy.check({"hypotheses": [NEW]}) == []
    assert hy.check({"hypotheses": [{**NEW, "legacy": True, "status": "failed", "rule": None}]}) == []


def test_every_queue_item_study_has_a_row():
    reg = {"hypotheses": [NEW]}
    assert hy.unregistered(reg, {"item18", "validation", "fair-value"}) == []
    assert hy.unregistered(reg, {"item18", "item22", "item99"}) == ["item22", "item99"]


def test_verdicts_reads_the_three_shapes_and_never_guesses():
    assert hy.verdicts({"verdict": "fail"}) == [{"arm": None, "verdict": "fail"}]
    assert hy.verdicts({"verdict": {"a": "fail", "b": "no_verdict"}}) == [
        {"arm": "a", "verdict": "fail"},
        {"arm": "b", "verdict": "no verdict"},
    ]
    assert hy.verdicts({"verdict": {"a": {"verdict": "pass", "t": 2.4}}}) == [{"arm": "a", "verdict": "pass"}]
    # No word to read: figures alone are never turned into a verdict.
    assert hy.verdicts({"verdict": {"a": {"t": 2.4}}}) is None
    assert hy.verdicts({"tests": []}) is None


def test_board_joins_results_and_queue_and_groups_rows():
    done = {
        **NEW,
        "id": 21,
        "study": "item21",
        "status": "failed",
        "results": ["{home}/r/design.json", "{home}/r/gone.json"],
    }
    declared = {**NEW, "id": 26, "study": "item26"}
    idle = {**NEW, "id": 28, "study": "item28"}
    files = {
        "{home}/r/design.json": (
            {"window": "design", "run_id": "20261027-183407-be19ad", "verdict": {"x": "fail", "y": "pass"}},
            "2026-10-28T00:53:17",
        ),
    }
    queue = [
        {
            "id": 5,
            "study": "item26",
            "step": "study",
            "status": "queued",
            "created_at": "2026-10-30T00:38:40",
        },
        {
            "id": 4,
            "study": "item21",
            "step": "design",
            "status": "done",
            "finished_at": "2026-10-27T20:00:00",
        },
        {"id": 3, "study": "item22", "step": "study", "status": "done"},
    ]
    out = hy.board({"hypotheses": [idle, done, declared]}, queue, lambda p: files.get(p, (None, None)))
    assert [r["study"] for r in out["rows"]] == ["item26", "item28", "item21"]
    assert [r["group"] for r in out["rows"]] == ["in_progress", "declared", "finished"]
    d21 = out["rows"][2]
    assert d21["results"][0]["summary"] == "mixed" and d21["results"][0]["run_id"] == "20261027-183407-be19ad"
    assert d21["results"][1]["present"] is False and d21["results"][1]["summary"] is None
    assert d21["last_activity"] == "2026-10-28T00:53:17"
    # The register decides the status: a queued task moves the group, never the status.
    assert out["rows"][0]["status"] == "declared" and out["rows"][0]["queue"]["active"] is True
    assert out["unregistered"] == ["item22"]
    # A study's Markdown beside its result file rides along; none is None.
    assert d21["results"][0]["notes"] is None
    notes = {"{home}/r/design.json": "| arm | P&L |"}
    out = hy.board({"hypotheses": [done]}, [], lambda p: files.get(p, (None, None)), notes.get)
    assert [x["notes"] for x in out["rows"][0]["results"]] == ["| arm | P&L |", None]
    # A retired row closed without a run: finished, no results, never re-tested.
    retired = {**NEW, "id": 11, "study": "item11", "status": "retired", "legacy": True}
    out = hy.board({"hypotheses": [retired]}, [], lambda p: (None, None))
    assert out["rows"][0]["group"] == "finished" and out["rows"][0]["results"] == []
    assert not [p for p in hy.check({"hypotheses": [retired]}) if "status" in p]
    assert "retired" not in hy.RETESTABLE


def test_similar_lists_only_rejected_matches_on_stated_tags():
    reg = hy.load()
    hits = hy.similar(reg, {"setup": "down-day", "structure": "put-bwb"})
    assert [r["study"] for r in hits] == ["item6"]
    assert hy.similar(reg, {}) == []
    # A declared (not rejected) look-alike is not listed.
    assert hy.similar(reg, {"setup": "reversion"}) == []  # item 18, declared


def test_rule_changes_are_due_a_reread_after_100_trades():
    reg = {
        "rule_changes": [
            {"date": "2026-10-01", "module": "meic", "arm": "control", "change": "c", "evidence": "e"},
            {
                "date": "2026-10-01",
                "module": "bwb",
                "arm": "bounce",
                "change": "c",
                "evidence": "e",
                "reread": "2026-10-20",
            },
        ]
    }
    out = hy.rereads(reg, lambda m, a, d: 120)
    assert [r["due"] for r in out] == [True, False]
    assert hy.rereads(reg, lambda m, a, d: 99)[0]["due"] is False


def test_retest_plan_fills_the_year_and_skips_recorded_ones():
    recipe = {
        "kind": "script",
        "args": {"path": "scripts/pead_history.py", "args": ["--start", "{start}", "--end", "{end}"]},
        "output": "~/.cherrypick/data/research/item1-{year}.json",
    }
    reg = {
        "hypotheses": [
            {**NEW, "id": 1, "study": "item1", "status": "failed", "retest": recipe},
            {**NEW, "id": 2, "study": "item2", "status": "passed", "retest": None},
            {**NEW, "id": 3, "study": "item3", "status": "observation"},
            {
                **NEW,
                "id": 4,
                "study": "item4",
                "status": "failed",
                "retest": recipe,
                "retests": [{"year": 2026}],
            },
        ]
    }
    plan = hy.retest_plan(reg, 2026)
    [row] = plan["queue"]
    assert row["study"] == "item1" and row["step"] == "retest-2026"
    assert row["args"]["args"] == ["--start", "2026-01-01", "--end", "2026-12-31"]
    assert row["output"].endswith("item1-2026.json")
    assert plan["needs_recipe"] == ["item2"] and plan["already_recorded"] == ["item4"]


def test_home_fills_a_real_path_and_the_repo_recipes_name_tracked_scripts(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    plan = hy.retest_plan(hy.load(), 2026)
    studies = {r["study"]: r for r in plan["queue"]}
    assert {"item1", "item5"} <= set(studies)
    out = studies["item1"]["output"]
    assert out.startswith(tmp_path.as_posix()) and out.endswith("item1-retest-2026.json")
    assert "~" not in " ".join(studies["item1"]["args"]["args"])
    for r in plan["queue"]:
        assert (hy.repo_root() / r["args"]["path"]).is_file()
