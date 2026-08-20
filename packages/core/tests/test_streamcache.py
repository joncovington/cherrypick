"""Tests for cherrypick.core.streamcache — schema/connect, status upsert, chain write, ATM window."""

import sqlite3
import threading
import time

import pytest

from cherrypick.core import streamcache


class _Opt:
    """Minimal stand-in for a tastytrade Option (has model_dump + strike_price)."""

    def __init__(self, sym, strike, exp="2026-07-10", und="SPX"):
        self.streamer_symbol = sym
        self.strike_price = strike
        self._d = {
            "streamer_symbol": sym,
            "strike_price": strike,
            "expiration_date": exp,
            "underlying_symbol": und,
        }

    def model_dump(self, mode="json"):
        return dict(self._d)


def test_connect_sets_a_busy_timeout(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == streamcache.BUSY_TIMEOUT_MS
    conn.close()


def test_connect_bounds_the_wal_size(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
    assert limit == streamcache.JOURNAL_SIZE_LIMIT_BYTES
    conn.close()


def test_checkpoint_resets_a_grown_wal(tmp_path):
    """The automatic checkpoint copies pages out but cannot truncate the file while a reader holds
    frames, and this cache always has one — so the WAL reached 98MB against a 49MB database. An
    explicit TRUNCATE checkpoint is what puts it back on the floor."""
    db = tmp_path / "sc.db"
    conn = streamcache.connect(db)
    for i in range(4000):
        streamcache.upsert_status(conn, last_event_at=f"2026-08-20T00:00:{i % 60:02d}")
    wal = db.with_name(db.name + "-wal")
    grown = wal.stat().st_size
    assert grown > 0

    reset, reclaimed = streamcache.checkpoint(conn)

    assert reset is True
    assert reclaimed > 0, "a successful TRUNCATE must report the bytes it gave back"
    assert wal.stat().st_size < grown
    conn.close()


def test_checkpoint_is_best_effort_under_contention(tmp_path):
    """A reader mid-transaction is the ordinary case during a session. The attempt must give up
    quietly and leave the connection usable rather than raise into the stream's task group, and it
    must restore the writer's own busy timeout on the way out."""
    db = tmp_path / "sc.db"
    conn = streamcache.connect(db)
    streamcache.upsert_status(conn, last_event_at="2026-08-20T00:00:00")

    reader = sqlite3.connect(str(db))
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM stream_status").fetchall()
    try:
        reset, reclaimed = streamcache.checkpoint(conn)  # must not raise
        assert reclaimed >= 0
        assert reset in (True, False)
    finally:
        reader.close()

    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == streamcache.BUSY_TIMEOUT_MS
    streamcache.upsert_status(conn, last_event_at="2026-08-20T00:00:01")  # still usable
    conn.close()


def test_a_contended_write_waits_instead_of_raising(tmp_path):
    """The 2026-08-17 failure in one test: with SQLite's default timeout of 0, a write attempted
    while another connection holds the lock raises `database is locked` immediately. The producer's
    status write lives inside its stream task group, so that exception tore down the DXLink
    connection and the whole suite's quotes went stale behind the reconnect loop."""
    db = tmp_path / "sc.db"
    writer = streamcache.connect(db)
    holder = streamcache.connect(db)
    released = threading.Event()

    def hold_the_lock():
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            "INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('HELD', 1.0, 0)"
        )
        time.sleep(0.4)
        holder.commit()
        released.set()

    t = threading.Thread(target=hold_the_lock)
    t.start()
    time.sleep(0.1)  # let the holder take the write lock first
    # Without the pragma this raises OperationalError right here.
    writer.execute("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('WAITED', 2.0, 0)")
    writer.commit()
    t.join()

    assert released.is_set()
    rows = {r["symbol"] for r in writer.execute("SELECT symbol FROM stream_trades")}
    assert rows == {"HELD", "WAITED"}
    writer.close()
    holder.close()


def test_without_the_timeout_the_same_contention_raises(tmp_path):
    """The control for the test above — proof it is the pragma doing the work, not luck in the
    scheduling. A bare connection (SQLite's default busy_timeout of 0) fails on the same sequence."""
    db = tmp_path / "sc.db"
    streamcache.connect(db).close()  # create the schema
    holder = sqlite3.connect(db)
    bare = sqlite3.connect(db)
    bare.execute("PRAGMA busy_timeout=0")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('HELD', 1.0, 0)")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        bare.execute("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('X', 2.0, 0)")
    holder.rollback()
    holder.close()
    bare.close()


def test_connect_creates_schema_and_is_reusable(tmp_path):
    db = tmp_path / "sc.db"
    conn = streamcache.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"stream_chain", "stream_greeks", "stream_oi", "stream_trades", "stream_status"} <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stream_chain)")}
    assert "underlying_symbol" in cols
    conn.close()
    streamcache.connect(db).close()  # idempotent re-open


def test_to_float_nan_and_none_safe():
    assert streamcache.to_float(None) is None
    assert streamcache.to_float(float("nan")) is None
    assert streamcache.to_float("1.5") == 1.5
    assert streamcache.to_float(3) == 3.0


def test_upsert_status_single_row(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    streamcache.upsert_status(conn, pid=123, subscribed_symbols=5)
    streamcache.upsert_status(conn, subscribed_symbols=9)  # partial update keeps pid
    row = conn.execute("SELECT id, pid, subscribed_symbols FROM stream_status").fetchone()
    assert row["id"] == 1 and row["pid"] == 123 and row["subscribed_symbols"] == 9
    assert conn.execute("SELECT COUNT(*) FROM stream_status").fetchone()[0] == 1
    conn.close()


def test_upsert_symbol_health_partial_updates_dont_blank_other_fields(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    streamcache.upsert_symbol_health(conn, "XSP", chain_fetch_error="boom")
    row = conn.execute(
        "SELECT chain_loaded_at, chain_fetch_error FROM stream_symbol_health WHERE symbol = 'XSP'"
    ).fetchone()
    assert row["chain_fetch_error"] == "boom"
    assert row["chain_loaded_at"] is None

    # A success call clears the error and stamps chain_loaded_at, without a separate blank-out step.
    streamcache.upsert_symbol_health(
        conn, "XSP", chain_loaded_at="2026-07-31T12:00:00+00:00", chain_fetch_error=None
    )
    row = conn.execute(
        "SELECT chain_loaded_at, chain_fetch_error FROM stream_symbol_health WHERE symbol = 'XSP'"
    ).fetchone()
    assert row["chain_loaded_at"] == "2026-07-31T12:00:00+00:00"
    assert row["chain_fetch_error"] is None
    assert conn.execute("SELECT COUNT(*) FROM stream_symbol_health").fetchone()[0] == 1
    conn.close()


def test_write_chain_tags_underlying(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    opts = {"C600": _Opt("C600", 600), "P600": _Opt("P600", 600)}
    assert streamcache.write_chain(conn, opts) == 2
    rows = conn.execute("SELECT streamer_symbol, underlying_symbol, expiration FROM stream_chain").fetchall()
    assert {r["streamer_symbol"] for r in rows} == {"C600", "P600"}
    assert all(r["underlying_symbol"] == "SPX" and r["expiration"] == "2026-07-10" for r in rows)
    conn.close()


def test_current_underlying_price_reads_last(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    conn.execute("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('SPX', 605.5, 0)")
    conn.commit()
    assert streamcache.current_underlying_price(conn, "SPX") == 605.5
    assert streamcache.current_underlying_price(conn, "QQQ") is None
    conn.close()


def test_atm_window_syms_centres_and_bounds():
    opts = {f"S{k}": _Opt(f"S{k}", k) for k in range(600, 621)}  # strikes 600..620
    keep = streamcache.atm_window_syms(opts, center=610.4, strike_count=2)
    strikes = sorted(int(s[1:]) for s in keep)
    assert strikes == [608, 609, 610, 611, 612]  # nearest (610) ± 2
    assert streamcache.atm_window_syms({}, 610, 2) == []
