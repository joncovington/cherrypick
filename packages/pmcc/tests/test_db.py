"""Schema mechanics: migration, the stale-writer guard, upsert idempotence, and the readers."""

from cherrypick.pmcc import db


def test_connect_creates_schema_and_migrates(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "pmcc_positions",
        "pmcc_legs",
        "pmcc_marks",
        "pmcc_assignments",
        "pmcc_management_events",
        "pmcc_decisions",
        "pmcc_entry_attempts",
        "pmcc_snapshots",
        "pmcc_loop_iterations",
        "pmcc_daily_bars",
        "pmcc_stream_window",
        "measurement_breaks",
    } <= tables
    assert db.stale_writer_columns(conn) == []


def test_stale_writer_detects_unknown_column(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    conn.execute("ALTER TABLE pmcc_positions ADD COLUMN from_the_future TEXT")
    assert db.stale_writer_columns(conn) == ["pmcc_positions.from_the_future"]


def test_upsert_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    row = {
        "position_id": "TQQQ:control:2026-08-17",
        "symbol": "TQQQ",
        "book": "control",
        "entry_session": "2026-08-17",
        "long_expiration": "2026-09-04",
        "long_strike": 50.0,
        "short_expiration": "2026-08-28",
        "short_strike": 67.0,
        "status": "open",
    }
    db.save_position(conn, row)
    db.save_position(conn, {**row, "status": "closed"})
    rows = conn.execute("SELECT COUNT(*) FROM pmcc_positions").fetchone()[0]
    assert rows == 1
    assert conn.execute("SELECT status FROM pmcc_positions").fetchone()["status"] == "closed"


def test_next_short_role(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    assert db.next_short_role(conn, "P") == "short_call_1"
    leg = {
        "position_id": "P",
        "leg_role": "short_call_1",
        "occ_symbol": "X",
        "streamer_symbol": ".X",
        "expiration": "2026-08-28",
        "strike": 67.0,
        "option_type": "call",
        "action": "Sell to Open",
    }
    db.save_leg(conn, leg)
    assert db.next_short_role(conn, "P") == "short_call_2"


def test_record_decision_collapses(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    for _ in range(3):
        db.record_decision(
            conn,
            trade_date="2026-08-17",
            book="control",
            symbol="TQQQ",
            mode="entry",
            reason="no_fresh_quotes",
            accepted=False,
        )
    rows = conn.execute("SELECT occurrences FROM pmcc_decisions").fetchall()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 3


def test_measurement_break_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    for _ in range(2):
        db.record_measurement_break(conn, break_date="2026-08-17", key="tick_cadence", new_value="60")
    assert conn.execute("SELECT COUNT(*) FROM measurement_breaks").fetchone()[0] == 1
