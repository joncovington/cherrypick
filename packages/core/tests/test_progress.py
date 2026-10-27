"""core.progress: a job's own file, a child's sibling, and the view joined from both."""

from __future__ import annotations

import json
import os

import pytest

from cherrypick.core import progress


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _no_inherited_file(monkeypatch, tmp_path):
    monkeypatch.delenv(progress.ENV, raising=False)
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_hand_started_job_writes_its_own_file_and_names_it_for_children(tmp_path):
    rep = progress.Reporter("backtest-validate", clock=Clock())
    assert rep.path == progress.progress_dir() / f"backtest-validate-{os.getpid()}.json"
    assert os.environ[progress.ENV] == str(rep.path)
    assert _doc(rep.path)["state"] == "running"


def test_phases_count_and_the_final_state_is_written_on_exit(tmp_path):
    path = tmp_path / "q3.json"
    with progress.Reporter("validate", path=path, clock=Clock(), throttle_s=0) as rep:
        rep.phase("sessions", total=4, unit="days")
        rep.advance("sessions", current="2026-10-13")
        rep.advance("sessions", current="2026-10-14")
        doc = _doc(path)
        assert doc["phases"][0]["done"] == 2 and doc["phases"][0]["current"] == "2026-10-14"
    assert _doc(path)["state"] == "done"


def test_an_exception_leaves_the_job_marked_failed(tmp_path):
    path = tmp_path / "q4.json"
    with pytest.raises(RuntimeError), progress.Reporter("run", path=path, clock=Clock()):
        raise RuntimeError("boom")
    assert _doc(path)["state"] == "failed"


def test_a_failure_the_job_wrote_is_not_overwritten_on_a_normal_exit(tmp_path):
    path = tmp_path / "q10.json"
    with progress.Reporter("check", path=path, clock=Clock()) as rep:
        rep.finish("failed")  # then returns an error reply, no exception
    assert _doc(path)["state"] == "failed"


def test_writes_are_throttled_but_phase_changes_are_not(tmp_path):
    clock = Clock()
    path = tmp_path / "q5.json"
    rep = progress.Reporter("run", path=path, clock=clock, throttle_s=1.0)
    rep.phase("sessions", total=10)
    rep.advance("sessions")  # same instant: held back
    assert _doc(path)["phases"][0]["done"] == 0
    clock.t += 2
    rep.advance("sessions")
    assert _doc(path)["phases"][0]["done"] == 2


def test_a_child_reports_beside_its_parent_and_the_view_nests_them(tmp_path, monkeypatch):
    """A validation and its replay subprocess are two processes; neither may write the other's file."""
    clock = Clock()
    root = tmp_path / "q3.json"
    parent = progress.Reporter("validate", path=root, clock=clock, throttle_s=0)
    parent.phase("decisions", total=3, unit="sources")
    parent.advance("decisions", 0, current="vendor")
    # The child: another pid owns the root file.
    monkeypatch.setattr(progress, "_owner_pid", lambda p: os.getpid() + 1)
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    child = progress.Reporter("meic-replay-check", clock=clock, throttle_s=0)
    assert child.parent == "q3" and child.path.name == f"q3.{os.getpid()}.json"
    child.phase("sessions", total=4, unit="days")
    child.advance("sessions", 2, current="2026-10-15")
    view = progress.read_job("q3", tmp_path, now=clock.t)
    assert [p["name"] for p in view["phases"]] == ["decisions", "sessions"]
    assert view["fraction"] == pytest.approx((0 + 2 / 4) / 3, abs=1e-4)
    assert "decisions 0/3 sources (vendor) · sessions 2/4 days (2026-10-15)" in view["line"]


def test_the_eta_comes_from_the_overall_fraction(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock = Clock(1000.0)
    path = tmp_path / "q6.json"
    rep = progress.Reporter("run", path=path, clock=clock, throttle_s=0)
    rep.phase("sessions", total=4)
    clock.t = 1600.0
    rep.advance("sessions")  # a quarter done in 600 s
    view = progress.read_job("q6", tmp_path, now=clock.t)
    assert view["eta_s"] == 1800 and "~30 min left" in view["line"]


def test_a_live_job_with_no_update_reads_stalled_and_a_dead_one_orphaned(tmp_path, monkeypatch):
    clock = Clock(1000.0)
    path = tmp_path / "q7.json"
    rep = progress.Reporter("run", path=path, clock=clock)
    rep.phase("sessions", total=4)
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    assert progress.read_job("q7", tmp_path, now=1000.0 + progress.STALL_SECONDS + 1)["state"] == "stalled"
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: False)
    assert progress.read_job("q7", tmp_path, now=1001.0)["state"] == "orphaned"


def test_a_stale_file_from_a_dead_run_is_taken_over_not_nested_under(tmp_path, monkeypatch):
    path = tmp_path / "q8.json"
    path.write_text(json.dumps({"job": "old", "pid": 999999, "state": "running", "phases": []}))
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: False)
    rep = progress.Reporter("run", path=path, clock=Clock())
    assert rep.parent is None and rep.path == path and _doc(path)["pid"] == os.getpid()


def test_prune_keeps_running_and_recent_files(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: False)
    old = 1000.0
    now = old + 31 * 86400
    for name, state, stamp in (("q1", "done", old), ("q2", "done", now - 60), ("q3", "failed", old)):
        (tmp_path / f"{name}.json").write_text(json.dumps({"pid": 1, "state": state, "updated_at": stamp}))
    assert progress.prune(tmp_path, now=now) == 2
    assert [p.stem for p in tmp_path.glob("*.json")] == ["q2"]


def test_a_failed_write_never_raises_into_the_job(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(progress, "write_json_atomic", broken)
    with progress.Reporter("run", path=tmp_path / "q9.json", clock=Clock()) as rep:
        rep.phase("sessions", total=2)
        rep.advance("sessions")
