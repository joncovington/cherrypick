"""Schema, upserts, the collapsing journal, migrations, and the stale-writer guard."""

from cherrypick.calendars import db


def _position(pid="2026-08-17:control:put", **overrides):
    row = {
        "position_id": pid,
        "week_of": "2026-08-17",
        "entry_session": "2026-08-17",
        "book": "control",
        "side": "put",
        "symbol": "SPX",
        "structure": "dc_4_7",
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-08-24",
        "strike": 6400.0,
        "quantity": 1,
        "status": "open",
    }
    row.update(overrides)
    return row


def test_connect_is_idempotent(tmp_path):
    path = str(tmp_path / "paper.db")
    conn = db.connect(path)
    conn.close()
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM dc_positions").fetchone()[0] == 0


def test_save_position_upserts_on_position_id(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    db.save_position(conn, _position())
    db.save_position(conn, {"position_id": "2026-08-17:control:put", "status": "closed"})
    rows = conn.execute("SELECT status, week_of FROM dc_positions").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["week_of"] == "2026-08-17"  # untouched fields survive the partial upsert


def test_save_leg_upserts_on_composite_key(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    leg = {
        "position_id": "p1",
        "leg_role": "front_put",
        "occ_symbol": "SPXW  260821P06400000",
        "streamer_symbol": ".SPXW260821P6400",
        "expiration": "2026-08-21",
        "strike": 6400.0,
        "option_type": "put",
        "action": "Sell to Open",
        "status": "open",
    }
    db.save_leg(conn, leg)
    db.save_leg(conn, {"position_id": "p1", "leg_role": "front_put", "status": "settled"})
    rows = conn.execute("SELECT status, occ_symbol FROM dc_legs").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "settled"
    assert rows[0]["occ_symbol"] == "SPXW  260821P06400000"


def test_record_decision_collapses_identical_runs(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    for _ in range(3):
        db.record_decision(
            conn,
            trade_date="2026-08-17",
            book="*",
            symbol="SPX",
            mode="entry",
            reason="no_fresh_quotes",
            accepted=False,
        )
    db.record_decision(
        conn,
        trade_date="2026-08-17",
        book="*",
        symbol="SPX",
        mode="entry",
        reason="entered dc_4_7",
        accepted=True,
    )
    rows = conn.execute("SELECT reason, occurrences FROM dc_decisions ORDER BY id").fetchall()
    assert [(r["reason"], r["occurrences"]) for r in rows] == [
        ("no_fresh_quotes", 3),
        ("entered dc_4_7", 1),
    ]


def test_measurement_break_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    db.record_measurement_break(conn, break_date="2026-08-17", key="tick_cadence", new_value="30")
    db.record_measurement_break(conn, break_date="2026-08-17", key="tick_cadence", new_value="30")
    assert conn.execute("SELECT COUNT(*) FROM measurement_breaks").fetchone()[0] == 1


def test_migrations_add_declared_columns(tmp_path, monkeypatch):
    path = str(tmp_path / "paper.db")
    db.connect(path).close()
    monkeypatch.setitem(db._ADDED_COLUMNS, "dc_positions", {"new_measure": "REAL"})
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(dc_positions)")}
    assert "new_measure" in cols


def test_stale_writer_columns_flags_schema_the_code_does_not_know(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    assert db.stale_writer_columns(conn) == []
    # A newer checkout added a column; this (older) code has no entry for it.
    conn.execute("ALTER TABLE dc_positions ADD COLUMN from_the_future REAL")
    assert db.stale_writer_columns(conn) == ["dc_positions.from_the_future"]


def test_open_leg_expirations_and_expiring_join(tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    db.save_position(conn, _position())
    for role, expiration in (("front_put", "2026-08-21"), ("back_put", "2026-08-24")):
        db.save_leg(
            conn,
            {
                "position_id": "2026-08-17:control:put",
                "leg_role": role,
                "occ_symbol": "X",
                "streamer_symbol": f".{role}",
                "expiration": expiration,
                "strike": 6400.0,
                "option_type": "put",
                "action": "Sell to Open" if role.startswith("front") else "Buy to Open",
                "status": "open",
            },
        )
    assert db.open_leg_expirations(conn) == ["2026-08-21", "2026-08-24"]
    expiring = db.expiring_open_legs(conn, "2026-08-21")
    assert [leg["leg_role"] for leg in expiring] == ["front_put"]
    assert expiring[0]["position_symbol"] == "SPX"
