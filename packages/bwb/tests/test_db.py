from cherrypick.bwb import db


def test_connect_creates_schema(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in (
        "bwb_positions",
        "bwb_legs",
        "bwb_marks",
        "bwb_trigger_ticks",
        "bwb_management_events",
        "bwb_decisions",
        "bwb_entry_attempts",
        "bwb_snapshots",
        "bwb_loop_iterations",
        "measurement_breaks",
    ):
        assert expected in tables


def test_connect_is_idempotent(tmp_path):
    path = str(tmp_path / "paper_trades.db")
    db.connect(path)
    conn = db.connect(path)  # second open must not raise or duplicate
    assert conn.execute("SELECT COUNT(*) FROM bwb_positions").fetchone()[0] == 0


def test_migrate_is_additive(tmp_path):
    path = str(tmp_path / "paper_trades.db")
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO bwb_positions (position_id, symbol, book, entry_session, structure_signature, "
        "expiration, body_strike, near_strike, far_strike) VALUES "
        "('a', 'SPX', 'control', '2026-09-01', 'sig1', '2026-09-18', 6480, 6485, 6470)"
    )
    conn.commit()

    added = {**db._ADDED_COLUMNS, "bwb_positions": {**db._ADDED_COLUMNS["bwb_positions"], "test_col": "REAL"}}
    store = db._ledgerstore.LedgerStore("bwb_", db._SCHEMA, added)
    touched = store.migrate(conn)
    assert "bwb_positions.test_col" in touched
    row = conn.execute("SELECT * FROM bwb_positions WHERE position_id = 'a'").fetchone()
    assert row["symbol"] == "SPX"
    assert row["test_col"] is None
    assert store.migrate(conn) == []


def test_save_position_and_legs_round_trip(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    db.save_position(
        conn,
        {
            "position_id": "SPX:control:2026-09-01",
            "symbol": "SPX",
            "book": "control",
            "entry_session": "2026-09-01",
            "structure_signature": "sig1",
            "expiration": "2026-09-18",
            "body_strike": 6480,
            "near_strike": 6485,
            "far_strike": 6470,
            "entry_credit": 4.5,
            "status": "open",
        },
    )
    db.save_leg(
        conn,
        {
            "position_id": "SPX:control:2026-09-01",
            "leg_role": "near_long",
            "occ_symbol": "SPXW  260918P06485000",
            "streamer_symbol": ".SPXW260918P6485",
            "expiration": "2026-09-18",
            "strike": 6485,
            "option_type": "put",
            "action": "Buy to Open",
            "entry_mid": 18.0,
            "status": "open",
        },
    )
    open_positions = db.open_positions(conn)
    assert len(open_positions) == 1
    assert open_positions[0]["entry_credit"] == 4.5
    legs = db.legs_for(conn, "SPX:control:2026-09-01")
    assert len(legs) == 1
    assert legs[0]["leg_role"] == "near_long"


def test_record_and_read_trigger_ticks_by_cohort(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    db.record_trigger_tick(
        conn,
        {
            "entry_session": "2026-09-01",
            "structure_signature": "sig1",
            "symbol": "SPX",
            "ticked_at": 1000.0,
            "session_date": "2026-09-01",
            "near_abs_delta": 0.30,
            "peak_abs_delta": 0.30,
            "spot": 6500.0,
            "gamma_flip": 6400.0,
            "gamma_flip_basis": "live_stream_cache",
            "below_flip_seen": 0,
            "addon_short_bid": None,
            "addon_short_ask": None,
            "addon_long_bid": None,
            "addon_long_ask": None,
            "measured": 1,
            "refusal": None,
        },
    )
    db.record_trigger_tick(
        conn,
        {
            "entry_session": "2026-09-01",
            "structure_signature": "sig1",
            "symbol": "SPX",
            "ticked_at": 1060.0,
            "session_date": "2026-09-01",
            "near_abs_delta": 0.35,
            "peak_abs_delta": 0.35,
            "spot": 6495.0,
            "gamma_flip": 6400.0,
            "gamma_flip_basis": "live_stream_cache",
            "below_flip_seen": 0,
            "addon_short_bid": None,
            "addon_short_ask": None,
            "addon_long_bid": None,
            "addon_long_ask": None,
            "measured": 1,
            "refusal": None,
        },
    )
    ticks = db.trigger_ticks_for_cohort(conn, "2026-09-01", "sig1")
    assert len(ticks) == 2
    assert ticks[0]["ticked_at"] < ticks[1]["ticked_at"]  # ordered oldest first
    assert db.trigger_ticks_for_cohort(conn, "2026-09-01", "sig_other") == []


def test_open_position_for_and_count(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    assert db.open_position_for(conn, "SPX", "control", "2026-09-01") is None
    db.save_position(
        conn,
        {
            "position_id": "SPX:control:2026-09-01",
            "symbol": "SPX",
            "book": "control",
            "entry_session": "2026-09-01",
            "structure_signature": "sig1",
            "expiration": "2026-09-18",
            "body_strike": 6480,
            "near_strike": 6485,
            "far_strike": 6470,
            "status": "open",
        },
    )
    assert db.open_position_for(conn, "SPX", "control", "2026-09-01") is not None
    assert db.open_position_count(conn, "control") == 1
    assert db.open_position_count(conn, "delta") == 0


def test_stale_writer_columns_empty_on_fresh_db(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    assert db.stale_writer_columns(conn) == []
