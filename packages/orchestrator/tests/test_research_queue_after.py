"""A row that waits for another row's success, and is held -- never run -- when that row fails."""

from __future__ import annotations

from datetime import datetime

import pytest

from cherrypick.orchestrator import researchqueue as rq

NOW = datetime(2026, 10, 25, 11, 0, tzinfo=rq.ET)  # a Sunday: no blackout


def _trading(_d):
    return False


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "build_argv", lambda *a, **k: ["x"])
    return tmp_path / "q.db"


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
    with rq.connect(db) as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE step = 'fetch-chains'", (status,))
        row = dict(conn.execute("SELECT * FROM tasks WHERE step = 'study'").fetchone())
        why = rq.ready(row, NOW, _trading, False, after_status=rq.prereq_status(conn, row["after"]))
    assert (why is None) if expect is None else expect in why


def test_an_older_queue_gains_the_column(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(rq.SCHEMA.replace("    after TEXT,\n", ""))
    con.close()
    with rq.connect(p) as conn:
        assert "after" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
