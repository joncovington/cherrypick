"""#1067: a register row and its Result file (on this machine, beside the register) agree on whether a
verdict is recorded, and a written result whose row is still `declared` is listed as a verdict waiting
to be recorded."""

from __future__ import annotations

from cherrypick.orchestrator import hypotheses as hy

# The declarations stay in the repo plan; only their headings matter to the check.
PLAN = """# Plan

### 80. A pending study

- **Rule:** x.

### 81. A recorded study

- **Rule:** y.

### 82. A study with no Result yet

- **Rule:** z.

## Process
"""

RESULTS = {
    "item80": "- **Result:** not yet run.\n",
    "item81": "- **Result (2026-11-14): fail, all arms.**\n  More prose.\n",
}


def _row(rid: int, status: str) -> dict:
    heading = {80: "80-a-pending-study", 81: "81-a-recorded-study", 82: "82-a-study-with-no-result-yet"}
    return {"id": rid, "study": f"item{rid}", "status": status, "legacy": False,
            "pointer": f"research/plan.md#{heading[rid]}"}  # fmt: skip


def test_the_result_file_says_pending_recorded_or_nothing():
    assert hy.result_state(RESULTS["item80"]) == "pending"
    assert hy.result_state(RESULTS["item81"]) == "recorded"
    assert hy.result_state("Notes, but no Result line.\n") is None
    assert hy.result_state(None) is None


def _drift(tmp_path, register_rows, rows):
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "plan.md").write_text(PLAN, encoding="utf-8")
    register_rows(rows, results=RESULTS)
    return [p for p in hy.check({"hypotheses": rows}, root=tmp_path) if "Result" in p]


def test_a_verdict_whose_result_still_says_not_yet_run_is_a_problem(tmp_path, register_rows):
    [p] = _drift(tmp_path, register_rows, [_row(80, "failed")])
    assert p.startswith("row 80:") and "not yet run" in p


def test_a_declared_row_whose_result_is_recorded_is_a_problem(tmp_path, register_rows):
    [p] = _drift(tmp_path, register_rows, [_row(81, "declared")])
    assert p.startswith("row 81:") and "declared" in p


def test_agreeing_rows_and_a_study_with_no_result_file_pass(tmp_path, register_rows):
    rows = [_row(80, "declared"), _row(81, "failed"), _row(82, "declared"), {**_row(82, "passed"), "id": 83,
            "study": "item83"}]  # fmt: skip
    assert _drift(tmp_path, register_rows, rows) == []


def test_a_declared_row_with_a_written_result_awaits_its_verdict(tmp_path):
    written = tmp_path / "data" / "item90" / "study.json"
    written.parent.mkdir(parents=True)
    written.write_text("{}", encoding="utf-8")
    reg = {
        "hypotheses": [
            # Listed in the register and written: waiting.
            {"id": 90, "study": "item90", "status": "declared", "results": ["{home}/data/item90/study.json"]},
            # Listed but not written yet: still running, nothing to record.
            {"id": 91, "study": "item91", "status": "declared", "results": ["{home}/data/item91/study.json"]},
            # No results listed: a done study step whose output exists counts.
            {"id": 92, "study": "item92", "status": "declared"},
            # Recorded already.
            {"id": 93, "study": "item93", "status": "failed", "results": ["{home}/data/item90/study.json"]},
        ]
    }
    tasks = [
        {"study": "item92", "step": "plan", "status": "done", "output": str(written)},
        {"study": "item92", "step": "study-design", "status": "done", "output": str(written)},
        {"study": "item91", "step": "study", "status": "failed", "output": str(written)},
    ]
    got = hy.awaiting_verdict(reg, tasks, home=tmp_path)
    assert [g["id"] for g in got] == [90, 92]
    assert got[0]["written"] == [str(written)] and got[1]["written"] == [str(written)]


def test_a_plan_step_output_alone_is_not_a_verdict(tmp_path):
    out = tmp_path / "path_requests.json"
    out.write_text("{}", encoding="utf-8")
    reg = {"hypotheses": [{"id": 92, "study": "item92", "status": "declared"}]}
    tasks = [{"study": "item92", "step": "plan", "status": "done", "output": str(out)}]
    assert hy.awaiting_verdict(reg, tasks, home=tmp_path) == []
