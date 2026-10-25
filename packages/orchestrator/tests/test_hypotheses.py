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


def test_similar_lists_only_rejected_matches_on_stated_tags():
    reg = hy.load()
    hits = hy.similar(reg, {"setup": "down-day", "structure": "put-bwb"})
    assert [r["study"] for r in hits] == ["item6"]
    assert hy.similar(reg, {}) == []
    # A declared (not rejected) look-alike is not listed.
    assert hy.similar(reg, {"setup": "setup-side-width"}) == []


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
