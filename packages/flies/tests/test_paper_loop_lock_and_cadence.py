"""The paper loop's single-instance lock (shared by --interval and --once) and the tick-cadence
measurement-break journal — both added with the supervisor cutover's resident 15s mode.
"""

from __future__ import annotations

import json
import os
import time

from cherrypick.flies import db as dbmod
from cherrypick.flies import paper_loop as pl


def test_loop_lock_never_steals_from_a_live_pid(managed_home):
    assert pl._acquire_loop_lock()
    assert int(open(pl._loop_lock_path(), encoding="utf-8").read()) == os.getpid()
    # a second acquire in the same (alive) process must refuse, regardless of age
    old = time.time() - 10_000
    os.utime(pl._loop_lock_path(), (old, old))
    assert not pl._acquire_loop_lock(stale_seconds=1)
    pl._release_loop_lock()
    assert not os.path.exists(pl._loop_lock_path())


def test_loop_lock_reclaims_dead_holder(managed_home):
    path = pl._loop_lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("999999999")
    assert pl._acquire_loop_lock()
    pl._release_loop_lock()


def test_cadence_change_is_journaled_exactly_once(managed_home):
    conn = dbmod.connect()
    # first resident run at 15s: baseline is 60s (the whole pre-supervisor ledger's cadence)
    pl._note_cadence_change(conn, 15)
    rows = conn.execute(
        "SELECT reason, detail FROM fly_decisions WHERE mode = 'cadence'"
    ).fetchall()
    assert len(rows) == 1
    assert "60s->15s" in rows[0]["reason"] and "not comparable" in rows[0]["reason"]
    assert json.loads(rows[0]["detail"]) == {"old_seconds": 60, "new_seconds": 15}
    state = json.load(open(pl._cadence_state_path(), encoding="utf-8"))
    assert state["seconds"] == 15

    # same cadence again (every subsequent session start): no new row
    pl._note_cadence_change(conn, 15)
    n = conn.execute("SELECT COUNT(*) FROM fly_decisions WHERE mode = 'cadence'").fetchone()[0]
    assert n == 1

    # a later change journals a second break
    pl._note_cadence_change(conn, 30)
    rows = conn.execute(
        "SELECT reason FROM fly_decisions WHERE mode = 'cadence' ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2 and "15s->30s" in rows[1]["reason"]
