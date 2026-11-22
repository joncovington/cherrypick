"""A study's register row is declared in its plan section's `register-row` block, and the register on
this machine imports it (`hypotheses.plan_rows`, `load`): one PR declares a study, and nobody copies
a row by hand. A row the register already holds is never changed from the plan."""

from __future__ import annotations

import json

from cherrypick.orchestrator import hypotheses as hy
from cherrypick.orchestrator import verdictstore as vs

BLOCK = {
    "title": "A new study",
    "measure": "m",
    "expected": "e",
    "sample": "s",
    "rule": "r",
    "mechanism": "why",
    "setup": "x",
    "side": "y",
    "structure": "z",
    "window": "w",
}


def _plan(*sections: str) -> str:
    return (
        "# Plan\n\n## The hypotheses, in priority order\n\n" + "\n".join(sections) + "\n## Process\n\nText.\n"
    )


def _section(n: int, title: str, block: dict | str | None) -> str:
    body = f"### {n}. {title}\n\nDeclared before any data.\n\n- **Rule:** x.\n\n"
    if block is not None:
        text = block if isinstance(block, str) else json.dumps(block, indent=2)
        body += f"```register-row\n{text}\n```\n\n"
    return body


def test_a_plan_section_declares_its_row_with_its_id_study_pointer_and_status_filled():
    rows, problems = hy.plan_rows(
        _plan(_section(108, "A new study: one line", BLOCK), _section(109, "Prose only", None))
    )
    assert problems == []
    [row] = rows
    assert (
        row["id"] == 108
        and row["study"] == "item108"
        and row["status"] == "declared"
        and row["outcome"] is None
    )
    assert row["pointer"] == "research/edge-hypotheses-plan.md#108-a-new-study-one-line"
    assert row["measure"] == "m" and row["mechanism"] == "why"


def test_a_bad_block_is_a_problem_never_a_row():
    cases = [
        (_section(110, "Not JSON", "{oops"), "not JSON"),
        (_section(111, "A list", "[1, 2]"), "one JSON object"),
        (_section(112, "Wrong id", {**BLOCK, "id": 99}), "names id 99"),
        (_section(113, "Wrong study", {**BLOCK, "study": "item7"}), "study item7"),
        (
            _section(114, "Claims a verdict", {**BLOCK, "status": "passed", "outcome": "pass"}),
            "recorded on the machine",
        ),
        (
            _section(115, "Two blocks", BLOCK) + f"```register-row\n{json.dumps(BLOCK)}\n```\n",
            "one register-row block",
        ),
    ]
    for text, why in cases:
        rows, problems = hy.plan_rows(_plan(text))
        assert rows == [] and len(problems) == 1 and why in problems[0], (why, problems)


def test_the_register_imports_a_declared_row_once_and_never_changes_one_it_holds(
    tmp_path, monkeypatch, isolated_register, register_rows
):
    plan = tmp_path / "plan.md"
    plan.write_text(
        _plan(_section(90, "Held already", BLOCK), _section(108, "A new study", BLOCK)), encoding="utf-8"
    )
    monkeypatch.setattr(hy, "plan_path", lambda: plan)
    register_rows(
        [{"id": 90, "study": "item90", "status": "failed", "outcome": "fail: kept", "measure": "old"}]
    )
    reg = hy.load()
    assert [r["study"] for r in reg["hypotheses"]] == ["item90", "item108"]
    # The row it held keeps its verdict and its own fields; the new one is saved, declared.
    assert reg["hypotheses"][0] == {
        "id": 90,
        "study": "item90",
        "status": "failed",
        "outcome": "fail: kept",
        "measure": "old",
    }
    saved = json.loads((isolated_register / "hypotheses.json").read_text(encoding="utf-8"))
    assert [r["study"] for r in saved["hypotheses"]] == ["item90", "item108"]
    assert saved["hypotheses"][1]["status"] == "declared"
    # Read again: nothing more to import, nothing rewritten.
    stamp = (isolated_register / "hypotheses.json").stat().st_mtime_ns
    assert len(hy.load()["hypotheses"]) == 2
    assert (isolated_register / "hypotheses.json").stat().st_mtime_ns == stamp
    # A path given is that file alone: no import.
    assert hy.load(isolated_register / "hypotheses.json") == saved


def test_a_study_declared_only_in_the_plan_can_be_recorded(tmp_path, monkeypatch, isolated_register):
    plan = tmp_path / "plan.md"
    plan.write_text(_plan(_section(108, "A new study", BLOCK)), encoding="utf-8")
    monkeypatch.setattr(hy, "plan_path", lambda: plan)
    # No register file at all yet: the first read builds it from the plan.
    vs.record("item108", "failed", "fail: a fail, by the declared rule",
              "{home}/data/item108/results.json", by="by hand")  # fmt: skip
    [row] = json.loads((isolated_register / "hypotheses.json").read_text(encoding="utf-8"))["hypotheses"]
    assert row["study"] == "item108" and row["status"] == "failed" and row["measure"] == "m"


def test_the_check_reports_a_bad_block_in_the_plan(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / hy.PLAN).write_text(
        _plan(_section(114, "Claims a verdict", {**BLOCK, "status": "passed"})), encoding="utf-8"
    )
    problems = hy.check({"hypotheses": []}, root=tmp_path)
    assert any("plan section 114" in p and "recorded on the machine" in p for p in problems)


def test_the_repo_plans_blocks_are_sound():
    # Every register-row block in the repo's own plan parses (CI sees the plan, never the register).
    _rows, problems = hy.plan_rows((hy.repo_root() / hy.PLAN).read_text(encoding="utf-8"))
    assert problems == []
