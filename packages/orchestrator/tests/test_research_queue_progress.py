"""The queue's progress is what each job writes (core.progress), read from files: a task is handed its
file when it starts, and queue-list attaches it -- and any run started by hand -- without spawning."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from cherrypick.core import progress

from cherrypick.orchestrator import researchqueue as rq


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(progress.ENV, raising=False)
    monkeypatch.setattr(rq, "build_argv", lambda *a, **k: ["x"])
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(rq, "db_path", lambda: tmp_path / "q.db")
    return tmp_path / "q.db"


class _Proc:
    pid = 4321
    returncode = 0

    def communicate(self):
        return "", None


def test_a_task_is_handed_its_own_progress_file(db):
    from datetime import datetime

    rq.add("item19", "script", {}, step="study", path=db)
    seen = {}

    def spawn(argv, **kw):
        seen.update(kw)
        return _Proc()

    rq.run_next(
        path=db,
        now=lambda: datetime(2026, 10, 27, 12, 0, tzinfo=rq.ET),
        is_trading_day=lambda d: False,
        spawn=spawn,
        notify=lambda *a: None,
    )
    with rq.connect(db) as conn:
        task_id = conn.execute("SELECT id FROM tasks").fetchone()[0]
    assert seen["env"][progress.ENV] == str(rq.progress_file(task_id))
    assert rq.progress_file(task_id).name == f"q{task_id}.json"


def test_queue_list_reads_progress_files_and_shows_hand_started_runs(db, monkeypatch, capsys):
    from cherrypick import cli

    rq.add("item19", "script", {}, step="study", path=db)
    with rq.connect(db) as conn:
        task_id = conn.execute("SELECT id FROM tasks").fetchone()[0]
    queued = progress.Reporter("backtest-validate", path=rq.progress_file(task_id), throttle_s=0)
    queued.phase("sessions", total=4, unit="days")
    queued.advance("sessions", 1, current="2026-10-13")
    monkeypatch.delenv(progress.ENV, raising=False)
    by_hand = progress.Reporter("backtest-run-meic")  # no file given: a run started by hand
    by_hand.phase("sessions", total=10)

    def no_spawning(*a, **k):
        raise AssertionError("queue-list must not start a process to measure progress")

    monkeypatch.setattr(rq.subprocess, "run", no_spawning)
    cli.cmd_queue_list({}, SimpleNamespace(study=None))
    out = json.loads(capsys.readouterr().out)
    row = out["tasks"][0]
    assert row["progress"]["phases"][0]["done"] == 1 and "sessions 1/4 days" in row["progress"]["line"]
    assert [v["job"] for v in out["by_hand"]] == ["backtest-run-meic"]
    assert out["by_hand"][0]["file"] == f"backtest-run-meic-{os.getpid()}"
