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


def test_track_counts_a_loop_overall_and_per_symbol_and_finishes_its_file(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    rows = [{"fund": "SPY"}, {"fund": "QQQ"}, {"fund": "SPY"}]
    seen = []
    for r in progress.track(rows, job="item-study", phase="trades", key=lambda r: r["fund"]):
        seen.append(r["fund"])
    assert seen == ["SPY", "QQQ", "SPY"]
    (view,) = progress.read_all()
    assert view["state"] == "done" and view["fraction"] == 1.0
    assert view["by_symbol"]["SPY"] == {"done": 2, "total": 2, "share": 1.0}
    with pytest.raises(RuntimeError):
        for _ in progress.track(rows, job="item-fail", phase="trades"):
            raise RuntimeError("boom")
    view = {v["job"]: v for v in progress.read_all()}["item-fail"]
    assert view["state"] == "failed" and view["note"] == "stopped at 0/3"


def test_a_phase_split_by_symbol_counts_each_and_the_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock, path = Clock(), tmp_path / "q7.json"
    rep = progress.Reporter("fetch", path=path, clock=clock, throttle_s=0)
    keys = ["SPX", "SPX", "QQQ", "SPX"]
    assert progress.counts_by(keys) == {"SPX": 3, "QQQ": 1}
    rep.phase("requests", total=len(keys), unit="windows", by=progress.counts_by(keys))
    rep.advance("requests", key="SPX")
    rep.advance("requests", key="SPX", skipped=True)
    rep.advance("requests", key="IWM")  # not opened with it: counted, with no total
    clock.t += 10
    view = progress.read_job("q7", tmp_path, now=clock.t)
    assert view["fraction"] == pytest.approx(3 / 4)
    assert view["by_symbol"] == {
        "SPX": {"done": 2, "total": 3, "share": pytest.approx(0.6667, abs=1e-4)},
        "QQQ": {"done": 0, "total": 1, "share": 0.0},
        "IWM": {"done": 1, "total": None, "share": None},
    }
    assert "[IWM 1]" in view["line"]
    rep.advance("requests", key="QQQ")
    assert "[QQQ 1/1]" in progress.read_job("q7", tmp_path, now=clock.t)["line"]
    rep.phase("requests", total=2)  # reopened without a split: the old split goes
    assert progress.read_job("q7", tmp_path, now=clock.t)["by_symbol"] is None


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
    assert view["eta_s"] == 1800 and "~00:30 remaining" in view["line"]


def test_a_job_counted_in_steps_estimates_only_the_step_under_way(tmp_path, monkeypatch):
    """2026-11-18: the full set's SPX fetch, 1296 of 1511 sessions in five hours, then a VIX pass,
    read 43% and six hours left: the overall share counted the light VIX step the same as the
    sessions. The steps are unlike, so the time left is the running step's, from its own pace."""
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock = Clock(1000.0)
    path = tmp_path / "q11.json"
    rep = progress.Reporter("fetch", path=path, clock=clock, throttle_s=0)
    rep.phase("steps", total=2, unit="steps")
    rep.at("steps", "sessions")
    rep.phase("sessions", total=100, unit="sessions")
    clock.t = 1800.0
    rep.advance("sessions", 80)  # 10 s each
    view = progress.read_job("q11", tmp_path, now=clock.t)
    assert view["fraction"] == 0.4
    assert view["eta_s"] == 200, "20 sessions left at 10 s each, not the job's pace over 40%"
    assert view["steps_after"] == 1
    assert "~00:03 remaining in this step, then 1 more step" in view["line"]
    rep.close_phase("sessions")
    rep.advance("steps", current="vix")
    rep.phase("vix", total=10, unit="sessions")
    clock.t = 1810.0
    rep.advance("vix", 5)
    view = progress.read_job("q11", tmp_path, now=clock.t)
    assert view["eta_s"] == 10 and view["steps_after"] == 0
    assert view["line"].endswith("<00:01 remaining in this step")


def test_a_job_without_steps_says_nothing_of_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock = Clock(1000.0)
    path = tmp_path / "q12.json"
    rep = progress.Reporter("run", path=path, clock=clock, throttle_s=0)
    rep.phase("sessions", total=4)
    clock.t = 1600.0
    rep.advance("sessions")
    view = progress.read_job("q12", tmp_path, now=clock.t)
    assert view["steps_after"] is None and "step" not in view["line"]


def test_the_rate_is_the_innermost_counted_phase_per_item(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock = Clock(1000.0)
    path = tmp_path / "q9.json"
    rep = progress.Reporter("run", path=path, clock=clock, throttle_s=0)
    rep.phase("days", total=10)
    clock.t = 1100.0
    rep.phase("paths", total=50, unit="expiries")  # nested; its own clock starts here
    clock.t = 1182.0
    rep.advance("paths", 10, current="TSLA 2018-12-28")
    view = progress.read_job("q9", tmp_path, now=clock.t)
    assert view["rate"] == {"phase": "paths", "seconds_each": 8.2}
    assert "(TSLA 2018-12-28) · 8.2 s/path · ~" in view["line"]


def test_skipped_items_count_as_done_but_not_toward_the_rate_or_the_eta(tmp_path, monkeypatch):
    """2026-11-06: item 44 resumed with 493 of 783 sessions finished; counted as worked, they read
    4.3 s a session and 18 minutes left where it took 57 s and about four hours."""
    monkeypatch.setattr(progress, "_pid_alive", lambda pid: True)
    clock = Clock(1000.0)
    path = tmp_path / "q7.json"
    rep = progress.Reporter("run", path=path, clock=clock, throttle_s=0)
    rep.phase("sessions", total=100, unit="days")
    rep.advance("sessions", 60, skipped=True)  # a resume's finished sessions, read back at once
    clock.t = 1600.0
    rep.advance("sessions", 10)  # ten replayed in 600 s
    view = progress.read_job("q7", tmp_path, now=clock.t)
    assert view["fraction"] == 0.7, "skipped sessions still count toward the share done"
    assert view["rate"] == {"phase": "sessions", "seconds_each": 60.0}
    assert view["eta_s"] == 1800, "30 left at 60 s each"
    assert "60.0 s/session" in view["line"] and "~00:30 remaining" in view["line"]
    rep.phase("sessions", total=100)  # a phase reopened starts afresh
    assert "skipped" not in rep.doc["phases"][0]


def test_a_fast_phase_reads_as_items_per_second_and_an_uncounted_one_has_no_rate():
    def line(rate):
        return progress.summary_line({"phases": [], "eta_s": None, "rate": rate})

    assert line({"phase": "symbol-days", "seconds_each": 0.25}) == "4.0 symbol-days/s"
    assert line({"phase": "symbol-days", "seconds_each": 2.0}) == "2.0 s/symbol-day"
    assert line(None) == ""


def test_the_eta_reads_as_hours_and_minutes():
    def line(eta_s):
        return progress.summary_line({"phases": [], "eta_s": eta_s})

    assert line(5 * 3600 + 7 * 60) == "~05:07 remaining"
    assert line(29) == "<00:01 remaining"


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
