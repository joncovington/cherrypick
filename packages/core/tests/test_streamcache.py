"""Tests for cherrypick.core.streamcache — schema/connect, status upsert, chain write, ATM window."""

import json
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


# --------------------------------------------------------------------------- usable_quote
def _row(bid, ask, updated, mid=None):
    return {"bid": bid, "ask": ask, "updated_at": updated, "mid": mid}


def test_usable_quote_accepts_a_good_quote():
    q = streamcache.usable_quote(_row(1.0, 1.2, 1000.0), now_ts=1000.0, max_age=120)
    assert q == {"bid": 1.0, "ask": 1.2, "mid": pytest.approx(1.1), "age_seconds": 0.0}


def test_usable_quote_rejects_stale():
    assert streamcache.usable_quote(_row(1.0, 1.2, 1000.0), now_ts=1121.0, max_age=120) is None
    assert streamcache.usable_quote(_row(1.0, 1.2, 1000.0), now_ts=1120.0, max_age=120) is not None


def test_usable_quote_rejects_crossed():
    """bid > ask is a torn read or a broken feed, not an opportunity."""
    assert streamcache.usable_quote(_row(1.5, 1.2, 1000.0), now_ts=1000.0, max_age=120) is None


def test_usable_quote_rejects_a_non_positive_ask():
    assert streamcache.usable_quote(_row(0.0, 0.0, 1000.0), now_ts=1000.0, max_age=120) is None
    assert streamcache.usable_quote(_row(0.0, -1.0, 1000.0), now_ts=1000.0, max_age=120) is None


def test_usable_quote_rejects_missing_fields():
    for row in (_row(None, 1.2, 1000.0), _row(1.0, None, 1000.0), _row(1.0, 1.2, None)):
        assert streamcache.usable_quote(row, now_ts=1000.0, max_age=120) is None


def test_usable_quote_uses_the_stored_mid_when_present():
    q = streamcache.usable_quote(_row(1.0, 2.0, 1000.0, mid=1.75), now_ts=1000.0, max_age=120)
    assert q["mid"] == 1.75


# --------------------------------------------------------------------------- read_spot
def _cache_with_spot(tmp_path, last, age_seconds=0.0):
    conn = streamcache.connect(tmp_path / "sc.db")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("SPX", last, 0.0, 0.0, time.time() - age_seconds),
    )
    conn.commit()
    conn.close()
    return tmp_path / "sc.db"


def test_read_spot_returns_the_last_price(tmp_path):
    assert streamcache.read_spot(_cache_with_spot(tmp_path, 7641.16), "spx") == 7641.16


def test_read_spot_refuses_a_stale_print_when_gated(tmp_path):
    """The 2026-07-20 incident: settlement decides every leg's P&L at once and cannot be undone, so
    a stalled feed must refuse rather than settle against an old print."""
    path = _cache_with_spot(tmp_path, 7641.16, age_seconds=600)
    assert streamcache.read_spot(path, "SPX", max_age_seconds=120) is None
    assert streamcache.read_spot(path, "SPX") == 7641.16, "ungated callers keep the old behaviour"


def test_read_spot_on_a_missing_cache_or_symbol(tmp_path):
    assert streamcache.read_spot(tmp_path / "nope.db", "SPX") is None
    assert streamcache.read_spot(_cache_with_spot(tmp_path, 1.0), "NOSUCH") is None


# --------------------------------------------------------------------------- chain reads
def _chain(tmp_path, rows):
    conn = streamcache.connect(tmp_path / "sc.db")
    for r in rows:
        conn.execute(
            "INSERT INTO stream_chain(streamer_symbol, expiration, underlying_symbol, data_json, updated_at) "
            "VALUES (?,?,?,?,?)",
            (r["streamer_symbol"], r["expiration"], r["underlying_symbol"],
             json.dumps(r["opt"]), time.time()),
        )
    conn.commit()
    return conn


def _opt(sym, occ, strike, otype="call"):
    return {"streamer_symbol": sym, "symbol": occ, "strike_price": strike, "option_type": otype}


def test_occ_root_takes_the_first_six_padded_characters():
    assert streamcache.occ_root("SPXW  260821P06400000") == "SPXW"
    assert streamcache.occ_root("TNA   260724C00067000") == "TNA"
    assert streamcache.occ_root("TNA1  260724C00067000") == "TNA1"
    assert streamcache.occ_root(None) == "" and streamcache.occ_root("") == ""


def test_chain_for_expiration_filters_by_root(tmp_path):
    """One date can carry more than one product: an AM-settled monthly beside the weekly, or a
    post-split adjusted root beside the standard one. Either would price against contracts the
    module is not trading."""
    conn = _chain(tmp_path, [
        {"streamer_symbol": ".A", "expiration": "2026-08-21", "underlying_symbol": "TNA",
         "opt": _opt(".A", "TNA   260821C00067000", 67.0)},
        {"streamer_symbol": ".B", "expiration": "2026-08-21", "underlying_symbol": "TNA",
         "opt": _opt(".B", "TNA1  260821C00067000", 67.0)},
    ])
    got = streamcache.chain_for_expiration(conn, "TNA", "2026-08-21", "TNA")
    assert [e["occ_symbol"] for e in got] == ["TNA   260821C00067000"]


def test_chain_for_expiration_filters_by_underlying(tmp_path):
    """SPX and XSP share dates and their strikes differ by 10x."""
    conn = _chain(tmp_path, [
        {"streamer_symbol": ".S", "expiration": "2026-08-21", "underlying_symbol": "SPX",
         "opt": _opt(".S", "SPXW  260821C06400000", 6400.0)},
        {"streamer_symbol": ".X", "expiration": "2026-08-21", "underlying_symbol": "XSP",
         "opt": _opt(".X", "SPXW  260821C00640000", 640.0)},
    ])
    got = streamcache.chain_for_expiration(conn, "XSP", "2026-08-21", "SPXW")
    assert [e["strike_price"] for e in got] == [640.0]


def test_chain_for_expiration_skips_unusable_rows(tmp_path):
    conn = _chain(tmp_path, [
        {"streamer_symbol": ".ok", "expiration": "2026-08-21", "underlying_symbol": "SPX",
         "opt": _opt(".ok", "SPXW  260821P06400000", 6400.0, "put")},
        {"streamer_symbol": ".nostrike", "expiration": "2026-08-21", "underlying_symbol": "SPX",
         "opt": {"streamer_symbol": ".nostrike", "symbol": "SPXW  260821C1", "strike_price": None}},
        {"streamer_symbol": ".noocc", "expiration": "2026-08-21", "underlying_symbol": "SPX",
         "opt": {"streamer_symbol": ".noocc", "strike_price": 1.0}},
    ])
    got = streamcache.chain_for_expiration(conn, "SPX", "2026-08-21", "SPXW")
    assert [e["streamer_symbol"] for e in got] == [".ok"]
    assert got[0]["option_type"] == "put"


def test_greeks_for_is_age_bounded_and_absent_is_absent(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    now = time.time()
    conn.execute("INSERT INTO stream_greeks(symbol, delta, iv, vega, updated_at) VALUES (?,?,?,?,?)",
                 (".fresh", 0.5, 0.2, 1.0, now))
    conn.execute("INSERT INTO stream_greeks(symbol, delta, iv, vega, updated_at) VALUES (?,?,?,?,?)",
                 (".stale", 0.4, 0.2, 1.0, now - 5000))
    conn.commit()
    got = streamcache.greeks_for(
        conn, [".fresh", ".stale", ".missing"], now_ts=now, max_age_seconds=1800
    )
    assert set(got) == {".fresh"}
    assert got[".fresh"]["delta"] == 0.5
    assert streamcache.greeks_for(conn, [], now_ts=now, max_age_seconds=1800) == {}
