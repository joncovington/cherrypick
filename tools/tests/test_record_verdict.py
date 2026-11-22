"""tools/record_verdict.py: a finished study's verdict recorded on this machine by hand, never in the repo."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "record_verdict.py"
spec = importlib.util.spec_from_file_location("record_verdict", TOOL)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)

RULE = {"t_bar": 2.0, "min_n": 150}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from cherrypick.core import home
    from cherrypick.orchestrator import hypotheses, researchqueue, verdictstore

    monkeypatch.setattr(home, "home", lambda: tmp_path)
    store = tmp_path / "verdicts.json"
    monkeypatch.setattr(verdictstore, "path", lambda: store)
    reg = tmp_path / "hypotheses.json"
    rows = [
        {"id": 89, "study": "item89", "status": "failed", "outcome": "fail: kept", "results": []},
        {"id": 90, "study": "item90", "status": "declared", "outcome": None, "results": []},
    ]
    reg.write_text(json.dumps({"hypotheses": rows}), encoding="utf-8")
    monkeypatch.setattr(hypotheses, "register_path", lambda root=None: reg)
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
    return {"store": store, "result": result, "reg": reg}


def test_a_kit_result_found_through_the_queue_is_recorded_locally(setup, capsys):
    arms = {"a": {"n": 300, "parts": [], "verdict": "fail"}}
    setup["result"].write_text(
        json.dumps({"rule": RULE, "arms": arms, "verdict": {"a": "fail"}}), encoding="utf-8"
    )
    before = setup["reg"].read_text(encoding="utf-8")
    assert rv.main(["item90", "--dry-run"]) == 0
    assert not setup["store"].exists()
    assert rv.main(["item90"]) == 0
    rec = json.loads(setup["store"].read_text(encoding="utf-8"))["studies"]["item90"]
    assert (
        rec["status"] == "failed"
        and rec["results"] == ["{home}/data/item90/results.json"]
        and rec["by"] == "by hand"
    )
    assert setup["reg"].read_text(encoding="utf-8") == before, "the register in the repo is untouched"
    assert rv.main(["item90"]) == 1
    assert "recorded already" in capsys.readouterr().err


def test_a_result_without_the_kits_block_needs_the_status_and_outcome_by_hand(setup, capsys):
    setup["result"].write_text(json.dumps({"verdict": {"a": "fail"}}), encoding="utf-8")
    assert rv.main(["item90"]) == 1
    assert "record it by hand" in capsys.readouterr().err
    assert rv.main(["item90", "--status", "failed"]) == 1
    assert rv.main(["item90", "--status", "failed", "--outcome", "fail: a fail, by the declared rule"]) == 0
    assert json.loads(setup["store"].read_text(encoding="utf-8"))["studies"]["item90"]["status"] == "failed"


def test_a_study_the_register_already_records_is_refused(setup, capsys):
    assert rv.main(["item89"]) == 1
    assert "already records item89" in capsys.readouterr().err
