"""#1088's reproduce step on a throwaway study: identical, differs, failed, and the recorded folder
always put back exactly as it was. No store is read."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "reproduce_studies", Path(__file__).resolve().parents[1] / "scripts" / "reproduce_studies.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

STUDY = """import json, sys
from pathlib import Path
out = Path(sys.argv[1])
mode = sys.argv[2]
if mode == "crash":
    raise SystemExit("TypeError: a chain had no forward")
value = 2 if mode in ("moved", "more-data") else 1
inputs = {"sha256": "b" if mode == "more-data" else "a"}
doc = {"verdict": {"A": "fail"}, "arms": {"A": {"mean": value}}, "inputs": inputs, "at": "now"}
out.write_text(json.dumps(doc))
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "packages/backtest/scripts").mkdir(parents=True)
    (repo / "packages/backtest/scripts/fake_item90.py").write_text(STUDY, encoding="utf-8")
    research = tmp_path / "results" / "research"
    folder = research / "item90"
    folder.mkdir(parents=True)
    recorded = {"verdict": {"A": "fail"}, "arms": {"A": {"mean": 1}}, "inputs": {"sha256": "a"}, "at": "then"}
    (folder / "results.json").write_text(json.dumps(recorded), encoding="utf-8")
    (folder / "results.version.json").write_text(json.dumps({"version": 0}), encoding="utf-8")
    (folder / "notes.md").write_text("kept", encoding="utf-8")
    monkeypatch.setattr(m, "REPO", repo)
    monkeypatch.setattr(m, "results_dir", lambda: research)
    db = tmp_path / "q.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id INTEGER, study TEXT, step TEXT, kind TEXT, status TEXT, args TEXT)")
    conn.commit()
    conn.close()
    return {"repo": repo, "folder": folder, "db": db, "out": tmp_path / "out", "recorded": recorded}


def _queue(w, mode, step="study", rid=1, path="packages/backtest/scripts/fake_item90.py"):
    conn = sqlite3.connect(w["db"])
    args = {"path": path, "args": [str(w["folder"] / "results.json"), mode]}
    conn.execute(
        "INSERT INTO tasks VALUES (?, 'item90', ?, 'script', 'done', ?)", (rid, step, json.dumps(args))
    )
    conn.commit()
    conn.close()


def _snapshot(folder: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def _run(w):
    before = _snapshot(w["folder"])
    out = m.reproduce("item90", db=w["db"], out_root=w["out"], python=sys.executable)
    assert _snapshot(w["folder"]) == before  # put back exactly, every time
    return out


def test_the_same_code_on_the_same_data_is_identical_and_run_time_fields_are_ignored(world):
    _queue(world, "same")
    out = _run(world)
    assert out["status"] == "identical" and out["files"] == ["results.json"]
    assert json.loads((world["out"] / "item90" / "results.json").read_text())["at"] == "now"


def test_a_moved_result_names_the_path_that_moved_not_its_value(world):
    _queue(world, "moved")
    out = _run(world)
    assert out["status"] == "differs" and out["moved"] == {"results.json": ["arms.A.mean"]}


def test_a_crash_is_failed_with_its_last_line_and_the_folder_is_restored(world):
    _queue(world, "crash")
    out = _run(world)
    assert out["status"] == "failed" and "no forward" in out["said"]


def test_a_difference_with_its_declared_inputs_moved_is_inputs_changed(world):
    _queue(world, "more-data")
    out = _run(world)
    assert out["status"] == "inputs_changed" and "inputs.sha256" in out["moved"]["results.json"]


def test_a_difference_after_a_logged_correction_is_expected(world, monkeypatch):
    _queue(world, "moved")
    monkeypatch.setattr(m, "_corrections", lambda script: 1)  # the log grew to version 1; the stamp says 0
    assert _run(world)["status"] == "expected"


def test_the_newest_row_of_each_study_step_reruns_in_queue_order(world):
    _queue(world, "moved", step="study", rid=1)
    _queue(world, "same", step="study", rid=5)  # the re-run that replaced row 1
    _queue(world, "same", step="study-holdout", rid=3)
    rows = m.last_study_rows("item90", world["db"])
    assert [r["id"] for r in rows] == [3, 5]


def test_a_script_moved_since_its_row_was_queued_is_found_where_it_lives_now(world):
    assert m.script_path("scripts/fake_item90.py", world["repo"]) == (
        world["repo"] / "packages/backtest/scripts/fake_item90.py"
    )
    assert m.script_path("scripts/gone_item91.py", world["repo"]) is None


def test_nothing_to_rerun_is_skipped(world):
    assert m.reproduce("item90", db=world["db"], out_root=world["out"])["status"] == "skipped"


def test_diff_paths_reads_lists_missing_keys_and_exact_floats():
    assert m.diff_paths({"a": [1, 2], "b": 0.1}, {"a": [1, 3], "b": 0.1}) == ["a[1]"]
    assert m.diff_paths({"a": 1}, {"a": 1, "c": 2}) == ["c"]
    assert m.diff_paths({"x": 0.30000000000000004}, {"x": 0.3}) == ["x"]
    assert m.diff_paths({"at": 1, "y": [1]}, {"at": 2, "y": [1, 1]}) == ["y[len]"]
