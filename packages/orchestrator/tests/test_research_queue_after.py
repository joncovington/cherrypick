"""A row that waits for another row's success, and is held -- never run -- when that row fails."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from cherrypick.orchestrator import researchqueue as rq

NOW = datetime(2026, 10, 25, 11, 0, tzinfo=rq.ET)  # a Sunday: no blackout


def _trading(_d):
    return False


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "build_argv", lambda *a, **k: ["x"])
    return tmp_path / "q.db"


def _set(db, step, status, study="item17"):
    with rq.connect(db) as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE study = ? AND step = ?", (status, study, step))


def _insert(db, study, step, after, status="queued"):
    """A row put straight into the table, as one that predates `add`'s checks would be."""
    with rq.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (study, step, kind, args, after, status, created_at)"
            " VALUES (?, ?, 'script', '{}', ?, ?, '2026-10-25T10:00:00')",
            (study, step, after, status),
        )
        return cur.lastrowid


def _why(db, row_id):
    with rq.connect(db) as conn:
        row = dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (row_id,)).fetchone())
        return rq.ready(
            row, NOW, _trading, False, after_status=rq.prereq_status(conn, row["after"], row["id"])
        )


def test_after_must_name_a_row(db):
    with pytest.raises(rq.Refused, match="names no row"):
        rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)


@pytest.mark.parametrize(
    "status, expect",
    [
        ("queued", "waits for"),
        ("running", "waits for"),
        ("done", None),
        ("failed", "held:"),
        ("cancelled", "held:"),
    ],
)
def test_the_dependent_row_waits_runs_or_is_held(db, status, expect):
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)
    _set(db, "fetch-chains", status)
    why = _why(db, 2)
    assert (why is None) if expect is None else expect in why


def test_a_held_row_says_re_queuing_the_prerequisite_releases_it(db):
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)
    _set(db, "fetch-chains", "failed")
    assert "re-queue it" in _why(db, 2) and "cancel" not in _why(db, 2)
    # ...and it does: the re-queued prerequisite is the newest by its name, so the row waits for it.
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    assert _why(db, 2) == "waits for item17/fetch-chains (queued)"


def test_a_row_cannot_wait_for_itself(db):
    rq.add("s", "script", {}, step="study", path=db)
    _set(db, "study", "done", study="s")
    with pytest.raises(rq.Refused, match="itself"):
        rq.add("s", "script", {}, step="study", after="s/study", path=db)


def test_a_row_that_would_close_a_cycle_is_refused(db):
    rq.add("x", "script", {}, step="a", path=db)
    rq.add("x", "script", {}, step="b", path=db)
    _set(db, "a", "done", study="x")
    _set(db, "b", "done", study="x")
    rq.add("x", "script", {}, step="a", after="x/b", path=db)  # fine: x/b is done
    # x/b again, after x/a: the newest x/a waits for x/b, which would be this row.
    with pytest.raises(rq.Refused, match="cycle"):
        rq.add("x", "script", {}, step="b", after="x/a", path=db)


def test_a_cycle_is_held_never_waited_on(db):
    _insert(db, "s", "study", None, status="done")
    own = _insert(db, "s", "study", "s/study")  # waits on itself: the newest s/study is this row
    a = _insert(db, "x", "a", "x/b")
    b = _insert(db, "x", "b", "x/a")
    c = _insert(db, "x", "c", "x/a")  # not in the cycle, but waits on it
    for row_id in (own, a, b, c):
        why = _why(db, row_id)
        assert why.startswith("held:") and "cycle" in why, why


# ------------------------------------------------------------------------------------------------
# The wiring: run_next and queue-list each pass the prerequisite's status to `ready`.


class _Proc:
    pid = 4321
    returncode = 0

    def communicate(self):
        return "", None


def _run(db, sent, spawned):
    def spawn(argv, **kw):
        spawned.append(argv)
        return _Proc()

    return rq.run_next(
        path=db,
        now=lambda: NOW,
        is_trading_day=_trading,
        spawn=spawn,
        notify=lambda level, title, msg: sent.append((level, title, msg)),
    )


@pytest.fixture
def runner(db, tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    monkeypatch.setattr(rq, "log_dir", lambda: tmp_path / "logs")
    return db


def test_run_next_runs_a_row_whose_prerequisite_is_done_and_holds_a_cycle(runner):
    db = runner
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)
    _set(db, "fetch-chains", "done")
    a = _insert(db, "x", "a", "x/b")
    _insert(db, "x", "b", "x/a")
    sent, spawned = [], []
    result = _run(db, sent, spawned)
    assert result["ran"] == 2 and spawned == [["x"]]
    _set(db, "study", "done")
    result = _run(db, [], [])
    assert result["ran"] is None and "cycle" in result["waiting"][a]


def test_a_held_row_notifies_once_and_is_released_by_re_queuing_its_prerequisite(runner):
    db = runner
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)
    _set(db, "fetch-chains", "failed")
    sent, spawned = [], []
    assert _run(db, sent, spawned)["ran"] is None
    assert [(lvl, title) for lvl, title, _ in sent] == [("WARNING", "Research task held: item17/study")]
    assert "re-queue it" in sent[0][2]
    # Every later pass is quiet, while a task runs or not.
    _run(db, sent, spawned)
    _run(db, sent, spawned)
    assert len(sent) == 1 and spawned == []
    # Re-queuing the prerequisite releases the held row: the prerequisite runs, then the row.
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    assert _run(db, sent, spawned)["ran"] == 3
    assert rq.rows(db)[-2]["held_at"] is None
    assert _run(db, sent, spawned)["ran"] == 2
    assert len(spawned) == 2 and not any("held" in title for _, title, _ in sent[1:])


def test_queue_list_shows_a_done_prerequisite_as_ready_and_a_cycle_as_held(db, monkeypatch, capsys):
    from cherrypick import cli

    monkeypatch.setattr(rq, "db_path", lambda: db)
    monkeypatch.setattr(rq, "session_held", lambda state_dir=None: False)
    rq.add("item17", "script", {}, step="fetch-chains", path=db)
    rq.add("item17", "script", {}, step="study", after="item17/fetch-chains", path=db)
    _set(db, "fetch-chains", "done")
    _insert(db, "x", "a", "x/b")
    _insert(db, "x", "b", "x/a")
    cli.cmd_queue_list({}, SimpleNamespace(study=None))
    waits = {t["name"]: t.get("waits") for t in json.loads(capsys.readouterr().out)["tasks"]}
    assert waits["item17/study"] == "ready"
    assert "cycle" in waits["x/a"] and "cycle" in waits["x/b"]


# ------------------------------------------------------------------------------------------------
# The columns an older queue gains.


def test_an_older_queue_gains_the_columns(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(rq.SCHEMA.replace("    after TEXT,\n", "").replace("    held_at TEXT,\n", ""))
    con.close()
    with rq.connect(p) as conn:
        assert {"after", "held_at"} <= {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}


def test_a_column_added_by_another_process_meanwhile_is_no_error(tmp_path, monkeypatch):
    p = tmp_path / "q.db"
    rq.connect(p).close()  # the columns are there
    # This process read the columns before the other added them: both look missing to it.
    monkeypatch.setattr(rq, "_columns", lambda conn: set())
    with rq.connect(p) as conn:
        assert {"after", "held_at"} <= {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
