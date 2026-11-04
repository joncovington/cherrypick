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


def _study(db, study, step, **kw):
    return rq.add(study, "script", {"path": "scripts/x.py"}, step=step, path=db, **kw)["id"]


def test_a_study_whose_fetch_is_done_runs_before_a_higher_priority_fetch(db):
    fetch = _add(db, "fetch", priority=-1)
    study = _study(db, "item49", "study", after="item31/fetch")
    other = _add(db, "other-fetch", priority=10)
    assert _next(db) == other, "the study's fetch has not run: it waits, the raised fetch runs"
    assert _next(db) == fetch
    late = _add(db, "late-fetch", priority=10)
    assert _next(db) == study, "its input is in: the study goes before any fetch"
    assert _next(db) == late


def test_a_validate_row_after_its_prerequisite_counts_as_a_study(db):
    first = _add(db, "first")
    assert _next(db) == first
    _add(db, "raised", priority=10)
    v = rq.add("validation", "validate", {"module": "meic", "cadence": 60}, step="v", path=db,
               after="item31/first")["id"]  # fmt: skip
    assert _next(db) == v


def test_a_script_with_no_prerequisite_takes_its_place_by_priority(db):
    loose = _study(db, "item48", "design")
    raised = _add(db, "raised", priority=10)
    assert _next(db) == raised, "nothing says the loose script's inputs were fetched"
    assert _next(db) == loose


def test_priority_then_age_orders_studies_whose_inputs_are_in(db):
    a, b = _add(db, "a"), _add(db, "b")
    assert [_next(db), _next(db)] == [a, b]
    s1 = _study(db, "s", "one", after="item31/a")
    s2 = _study(db, "s", "two", after="item31/b", priority=3)
    s3 = _study(db, "s", "three", after="item31/b", priority=3)
    assert [_next(db) for _ in range(3)] == [s2, s3, s1]


def test_run_order_is_the_rule_spelt_once():
    fetch = {"id": 1, "kind": "fetch-chains", "after": "a/b", "priority": 99}
    study = {"id": 2, "kind": "script", "after": "a/b", "priority": 0}
    loose = {"id": 3, "kind": "script", "after": None, "priority": 0}
    assert rq.study_first(study) and not rq.study_first(fetch) and not rq.study_first(loose)
    assert sorted([loose, fetch, study], key=rq.run_order) == [study, fetch, loose]


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
