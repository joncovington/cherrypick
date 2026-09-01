from cherrypick.curve import db


def test_connect_creates_schema(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in (
        "curve_positions",
        "curve_legs",
        "curve_marks",
        "curve_regime",
        "curve_assignments",
        "curve_management_events",
        "curve_decisions",
        "curve_entry_attempts",
        "curve_snapshots",
        "curve_loop_iterations",
        "measurement_breaks",
    ):
        assert expected in tables


def test_connect_is_idempotent(tmp_path):
    path = str(tmp_path / "paper_trades.db")
    db.connect(path)
    conn = db.connect(path)  # second open must not raise or duplicate
    assert conn.execute("SELECT COUNT(*) FROM curve_positions").fetchone()[0] == 0


def test_migrate_is_additive(tmp_path):
    """Adding a column to _ADDED_COLUMNS is additive-only: an existing table gains the column,
    loses nothing, and re-running is a no-op."""
    path = str(tmp_path / "paper_trades.db")
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO curve_positions (position_id, symbol, book, entry_session, expiration, "
        "short_strike, long_strike) VALUES ('a', 'VXX', 'control', '2026-09-01', '2026-10-16', 30, 35)"
    )
    conn.commit()

    added = {
        **db._ADDED_COLUMNS,
        "curve_positions": {**db._ADDED_COLUMNS["curve_positions"], "test_col": "REAL"},
    }
    store = db._ledgerstore.LedgerStore("curve_", db._SCHEMA, added)
    touched = store.migrate(conn)
    assert "curve_positions.test_col" in touched
    # Existing row survives the migration untouched.
    row = conn.execute("SELECT * FROM curve_positions WHERE position_id = 'a'").fetchone()
    assert row["symbol"] == "VXX"
    assert row["test_col"] is None
    # Re-running is a no-op (already present).
    assert store.migrate(conn) == []


def test_save_and_read_regime(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    db.save_regime(
        conn,
        {
            "trade_date": "2026-09-01",
            "tick": "2026-09-01T10:00:00-04:00",
            "recorded_at": "x",
            "usable": 1,
            "refusal": None,
            "ratio": 0.85,
            "regime": "contango",
            "hook": 0,
            "vix": 17.0,
            "vix3m": 20.0,
            "vix_age_s": 3.0,
            "vix3m_age_s": 3.0,
        },
    )
    row = db.regime_for(conn, "2026-09-01")
    assert row["ratio"] == 0.85
    assert row["regime"] == "contango"


def test_save_regime_upserts_by_trade_date(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    db.save_regime(
        conn,
        {
            "trade_date": "2026-09-01",
            "tick": "t1",
            "ratio": 0.80,
            "regime": "contango",
            "hook": 0,
            "usable": 1,
        },
    )
    db.save_regime(
        conn,
        {
            "trade_date": "2026-09-01",
            "tick": "t2",
            "ratio": 0.90,
            "regime": "contango",
            "hook": 0,
            "usable": 1,
        },
    )
    count = conn.execute("SELECT COUNT(*) FROM curve_regime WHERE trade_date = '2026-09-01'").fetchone()[0]
    assert count == 1
    row = db.regime_for(conn, "2026-09-01")
    assert row["ratio"] == 0.90


def test_prior_ratio_before_only_reads_usable_rows(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    db.save_regime(
        conn,
        {
            "trade_date": "2026-08-31",
            "tick": "t",
            "ratio": 1.20,
            "regime": "backwardation",
            "hook": 0,
            "usable": 1,
        },
    )
    db.save_regime(
        conn,
        {
            "trade_date": "2026-09-01",
            "tick": "t",
            "ratio": None,
            "regime": None,
            "hook": 0,
            "usable": 0,
            "refusal": "stale_vix",
        },
    )
    assert db.prior_ratio_before(conn, "2026-09-02") == 1.20


def test_stale_writer_columns_empty_on_fresh_db(tmp_path):
    conn = db.connect(str(tmp_path / "paper_trades.db"))
    assert db.stale_writer_columns(conn) == []
