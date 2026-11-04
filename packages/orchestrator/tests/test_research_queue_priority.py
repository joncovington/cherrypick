"""A row's priority orders the queue: the ready row with the highest priority runs next, the oldest
among equals, and priority never releases a row from what it waits for."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from cherrypick.orchestrator import researchqueue as rq

NOW = datetime(2026, 10, 25, 11, 0, tzinfo=rq.ET)  # a Sunday: no blackout


class _Proc:
    pid = 4242
    returncode = 0

    def communicate(self):
        return '{"complete": 1}', None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "build_argv", lambda *a, **k: ["x"])
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    return tmp_path / "q.db"


def _next(db) -> int | None:
    out = rq.run_next(
        path=db,
        now=lambda: NOW,
        is_trading_day=lambda d: False,
        spawn=lambda argv, **kw: _Proc(),
        notify=lambda *a: None,
    )
    return out["ran"]


def _add(db, step, **kw):
    return rq.add("item31", "fetch-chains", {"requests": "r.json"}, step=step, path=db, **kw)["id"]


def test_the_highest_priority_ready_row_runs_first_and_the_oldest_among_equals(db):
    a, b, c = _add(db, "a"), _add(db, "b"), _add(db, "c", priority=5)
    d = _add(db, "d", priority=5)
    assert [_next(db) for _ in range(4)] == [c, d, a, b]


def test_a_queued_row_is_reordered_and_says_what_it_was(db):
    a, b = _add(db, "a"), _add(db, "b")
    assert rq.set_priority(b, 3, db) == {"ok": True, "id": b, "priority": 3, "was": 0}
    assert _next(db) == b
    rq.set_priority(a, -1, db)
    assert rq.rows(db)[-1]["priority"] == -1


def test_only_a_queued_row_is_reordered(db):
    a = _add(db, "a")
    assert _next(db) == a
    out = rq.set_priority(a, 9, db)
    assert out["ok"] is False and "only a queued task" in out["error"]
    assert rq.set_priority(999, 1, db)["ok"] is False


@pytest.mark.parametrize("bad", [1.5, "2", True, None])
def test_a_priority_that_is_not_a_whole_number_is_refused(db, bad):
    with pytest.raises(rq.Refused, match="whole number"):
        _add(db, "a", priority=bad)
    _add(db, "b")
    with pytest.raises(rq.Refused, match="whole number"):
        rq.set_priority(1, bad, db)


def test_priority_never_releases_a_row_from_its_prerequisite(db):
    first = _add(db, "first")
    rq.set_priority(first, -5, db)
    _add(db, "second", after="item31/first", priority=9)
    other = _add(db, "other")
    assert _next(db) == other, "the raised row waits for its prerequisite; the next ready row runs"
    assert _next(db) == first


def test_an_older_queue_gains_the_column_and_its_rows_read_priority_zero(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, study TEXT NOT NULL, step TEXT NOT NULL,"
        " kind TEXT NOT NULL, args TEXT NOT NULL, output TEXT, result TEXT, needs TEXT NOT NULL DEFAULT '',"
        " not_before TEXT, stop_by TEXT, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,"
        " started_at TEXT, finished_at TEXT, runs INTEGER NOT NULL DEFAULT 0, pid INTEGER,"
        " exit_code INTEGER, note TEXT)"
    )
    conn.execute(
        "INSERT INTO tasks (study, step, kind, args, created_at) VALUES ('s', 'x', 'script', '{}', 'now')"
    )
    conn.commit()
    conn.close()
    assert rq.rows(path)[0]["priority"] == 0
