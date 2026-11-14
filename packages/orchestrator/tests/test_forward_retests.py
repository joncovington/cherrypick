"""#1085: a passed item's forward retest over the sessions since it was declared, monthly."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import hypotheses as hy
from cherrypick.orchestrator import jobspec

PLAN_STEP = {
    "step": "plan",
    "kind": "script",
    "args": ["plan", "--through", "{through}"],
    "output": "{results}/r.json",
}
FETCH_STEP = {
    "step": "fetch",
    "kind": "fetch-chain-paths",
    "task_args": {"requests": "{results}/r.json"},
    "repeat": 2,
}
STUDY_STEP = {
    "step": "study",
    "kind": "script",
    "args": ["study", "--out", "{results}/forward-{stamp}.json"],
    "output": "{results}/forward-{stamp}.json",
}
RECIPE = {"script": "packages/backtest/scripts/x_item90.py", "steps": [PLAN_STEP, FETCH_STEP, STUDY_STEP]}


def _reg(*rows):
    return {"hypotheses": list(rows)}


def _plan(reg, day=date(2026, 12, 2)):
    return hy.forward_plan(reg, day, results_for=lambda s: Path("/res") / s)


def test_a_passed_row_with_a_recipe_plans_its_chain_for_the_month():
    p = _plan(_reg({"id": 90, "study": "item90", "status": "passed", "retest_forward": RECIPE}))
    rows = p["chains"]["item90"]["rows"]
    stamp = "forward-2026-12"
    assert [r["step"] for r in rows] == [
        f"{stamp}-plan",
        f"{stamp}-fetch",
        f"{stamp}-fetch-2",
        f"{stamp}-study",
    ]
    assert rows[0]["args"]["args"] == ["plan", "--through", "2026-12-02"] and rows[0]["after"] is None
    assert rows[3]["after"] == "item90/forward-2026-12-fetch-2"
    assert rows[3]["output"] == "/res/item90/forward-2026-12.json"


def test_a_month_already_recorded_failed_rows_and_rows_without_a_recipe_are_not_planned():
    recorded = {"forward": "2026-12"}
    p = _plan(
        _reg(
            {
                "id": 90,
                "study": "item90",
                "status": "passed",
                "retest_forward": RECIPE,
                "retests": [recorded],
            },
            {"id": 91, "study": "item91", "status": "failed", "retest_forward": RECIPE},
            {"id": 92, "study": "item92", "status": "passed"},
            {"id": 93, "study": "item93", "status": "passed", "retest_note": "its own forward read"},
        )
    )
    assert p["chains"] == {} and p["already_recorded"] == ["item90"]
    assert p["needs_recipe"] == ["item92"] and p["declined"] == ["item93"]


def test_a_malformed_recipe_is_a_register_problem_and_never_planned():
    assert hy.forward_problems({"script": "x.py"}) == ["retest_forward is {script, steps}"]
    bad = {"script": "x.py", "steps": [{"step": "s", "kind": "script", "repeat": 2}]}
    assert hy.forward_problems(bad)
    row = {"id": 94, "study": "item94", "status": "passed", "retest_forward": bad}
    assert _plan(_reg(row))["needs_recipe"] == ["item94"]


def test_the_monthly_job_ships_off_and_runs_the_forward_plan_when_on():
    assert cfgmod.research_queue_settings({})["forward_retests"] == {
        "enabled": False,
        "day": 2,
        "at": "05:00",
    }
    on = {"research_queue": {"forward_retests": {"enabled": True, "day": 3, "at": "06:30"}}}
    assert cfgmod.research_queue_settings(on)["forward_retests"]["day"] == 3
    now = datetime(2026, 12, 2, 4, 0, tzinfo=ZoneInfo("America/New_York"))
    for cfg, enabled in (({}, False), (on, True)):
        jobs, _ = jobspec.derive_jobs(cfg, pythonw="pythonw", launcher="run.py", now=now, pipelines=False)
        job = next(j for j in jobs if j.id == "research-retests")
        assert job.enabled is enabled and job.argv[-3:] == ("retests", "--forward", "--apply")


def test_the_repo_registers_forward_recipes_are_sound():
    assert [p for p in hy.check(hy.load()) if "retest_forward" in p] == []
    planned = hy.forward_plan(hy.load(), date(2026, 12, 2))
    assert "item57" in planned["chains"]
