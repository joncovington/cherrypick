"""tools/record_verdict.py: a finished study's verdict recorded by hand in the register on this machine."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "tools" / "record_verdict.py"
spec = importlib.util.spec_from_file_location("record_verdict", TOOL)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)

RULE = {"t_bar": 2.0, "min_n": 150}


@pytest.fixture
def setup(tmp_path, monkeypatch, register_rows, isolated_register):
    from cherrypick.core import home

    from cherrypick.orchestrator import researchqueue

    monkeypatch.setattr(home, "home", lambda: tmp_path)
    register_rows(
        [
            {"id": 89, "study": "item89", "status": "failed", "outcome": "fail: kept", "results": []},
            {"id": 90, "study": "item90", "status": "declared", "outcome": None, "results": []},
        ]
    )
    result = tmp_path / "data" / "item90" / "results.json"
    result.parent.mkdir(parents=True)
    # The register lists no result: the finished study row's output is found through the queue.
    monkeypatch.setattr(
        researchqueue,
        "rows",
        lambda path=None, study=None, limit=200: [
            {
                "study": "item90",
                "step": "smoke",
                "status": "done",
                "output": str(result.with_name("smoke.json")),
            },
            {"study": "item90", "step": "study", "status": "done", "output": str(result)},
        ],
    )
    return {"result": result, "folder": isolated_register}


def _row(folder, study):
    reg = json.loads((folder / "hypotheses.json").read_text(encoding="utf-8"))
    return next(r for r in reg["hypotheses"] if r["study"] == study)


def test_a_kit_result_found_through_the_queue_is_recorded_with_its_paragraph(setup, capsys):
    arms = {"a": {"n": 300, "parts": [], "verdict": "fail"}}
    setup["result"].write_text(
        json.dumps({"rule": RULE, "arms": arms, "verdict": {"a": "fail"}}), encoding="utf-8"
    )
    assert rv.main(["item90", "--dry-run"]) == 0
    assert _row(setup["folder"], "item90")["status"] == "declared"
    assert rv.main(["item90"]) == 0
    row = _row(setup["folder"], "item90")
    assert row["status"] == "failed" and row["results"] == ["{home}/data/item90/results.json"]
    assert row["recorded_by"] == "by hand"
    assert (setup["folder"] / "results" / "item90.md").read_text(encoding="utf-8").startswith("- **Result (")
    assert rv.main(["item90"]) == 1
    assert "already records item90" in capsys.readouterr().err


def test_a_result_without_the_kits_block_needs_the_status_and_outcome_by_hand(setup, capsys):
    setup["result"].write_text(json.dumps({"verdict": {"a": "fail"}}), encoding="utf-8")
    assert rv.main(["item90"]) == 1
    assert "record it by hand" in capsys.readouterr().err
    assert rv.main(["item90", "--status", "failed"]) == 1
    assert rv.main(["item90", "--status", "failed", "--outcome", "fail: a fail, by the declared rule"]) == 0
    assert _row(setup["folder"], "item90")["status"] == "failed"
    md = (setup["folder"] / "results" / "item90.md").read_text(encoding="utf-8")
    assert md.startswith("- **Result (") and "fail: a fail, by the declared rule" in md


def test_a_study_the_register_already_records_is_refused(setup, capsys):
    assert rv.main(["item89"]) == 1
    assert "already records item89" in capsys.readouterr().err
