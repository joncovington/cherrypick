"""The store's promises: idempotent open, tolerant reads, atomic writes, deterministic FIFO order."""

from __future__ import annotations

import json

from cherrypick.advisor import paths, store


def test_connect_is_idempotent_and_creates_the_home():
    conn = store.connect()
    conn.close()
    assert paths.db_path().exists()
    # Re-opening an existing database must not raise or lose rows -- the CLI opens it on every verb.
    conn = store.connect()
    store.record_checkpoint(conn, session="2026-08-13", slot="am", model="sonnet", ok=True)
    conn.close()
    conn = store.connect()
    assert len(store.rows(conn, "SELECT * FROM checkpoints")) == 1
    conn.close()


def test_rerunning_a_slot_replaces_its_checkpoint():
    """`--force` re-runs a slot. The slot is one row, not an accumulating pile."""
    conn = store.connect()
    first = store.record_checkpoint(conn, session="2026-08-13", slot="am", model="sonnet",
                                    ok=False, error="claude not found")
    second = store.record_checkpoint(conn, session="2026-08-13", slot="am", model="sonnet",
                                     ok=True, observations=["vix drifting up"])
    assert first == second
    row = store.rows(conn, "SELECT * FROM checkpoints")[0]
    assert row["ok"] == 1 and row["error"] is None
    assert json.loads(row["observations_json"]) == ["vix drifting up"]
    conn.close()


def test_rows_tolerates_a_table_that_does_not_exist():
    """A module that has never run has no tables. That is a fact about the day, not an error --
    a pack reading a dozen tables across five packages cannot fail wholesale on one absence."""
    conn = store.connect()
    assert store.rows(conn, "SELECT * FROM a_table_that_never_existed") == []
    assert store.rows(conn, "SELECT no_such_column FROM checkpoints") == []
    conn.close()


def test_experiments_come_back_in_fifo_order():
    """Queued experiments activate oldest-first, so the order must not depend on dict iteration
    or on SQLite's rowid happening to agree."""
    conn = store.connect()
    for n, module in enumerate(("meic", "flies", "meic"), start=1):
        store.insert_experiment(conn, {
            "id": f"exp-2026-08-13-{module}-{n}",
            "module": module,
            "base_profile": "control",
            "params_json": json.dumps({"stop_trigger_ratio": 0.9}),
            "status": "queued",
            "created_session": "2026-08-13",
            "expires_after_sessions": 15,
            "created_at": f"2026-08-13T0{n}:00:00+00:00",
        })
    ids = [e["id"] for e in store.experiments(conn, module="meic")]
    assert ids == ["exp-2026-08-13-meic-1", "exp-2026-08-13-meic-3"]
    assert [e["id"] for e in store.experiments(conn, status="queued")][0] == "exp-2026-08-13-meic-1"
    conn.close()


def test_next_experiment_id_does_not_collide_within_a_session():
    conn = store.connect()
    first = store.next_experiment_id(conn, "2026-08-13", "meic")
    store.insert_experiment(conn, {
        "id": first, "module": "meic", "base_profile": "control",
        "params_json": "{}", "status": "active", "created_session": "2026-08-13",
        "expires_after_sessions": 15,
    })
    assert store.next_experiment_id(conn, "2026-08-13", "meic") != first
    conn.close()


def test_journal_records_the_lifecycle_in_order():
    conn = store.connect()
    store.insert_experiment(conn, {
        "id": "exp-1", "module": "meic", "base_profile": "control", "params_json": "{}",
        "status": "queued", "created_session": "2026-08-13", "expires_after_sessions": 15,
    })
    store.journal(conn, "exp-1", "created", session="2026-08-13")
    store.journal(conn, "exp-1", "activated", session="2026-08-14", detail={"reason": "slot freed"})
    assert [e["event"] for e in store.events(conn, "exp-1")] == ["created", "activated"]
    assert json.loads(store.events(conn, "exp-1")[1]["detail_json"]) == {"reason": "slot freed"}
    conn.close()


def test_write_json_is_atomic_and_leaves_no_tmp_behind(tmp_path):
    target = tmp_path / "packs" / "2026-08-13-am.json"
    store.write_json(target, {"pack_version": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"pack_version": 1}
    assert list(target.parent.iterdir()) == [target]


def test_read_json_degrades_instead_of_raising(tmp_path):
    """Every foreign artifact is read this way -- none of them are owned here, and a module
    mid-write must not take a checkpoint down."""
    assert store.read_json(tmp_path / "absent.json", default={}) == {}
    broken = tmp_path / "half-written.json"
    broken.write_text('{"module": "meic"', encoding="utf-8")
    assert store.read_json(broken, default={}) == {}
