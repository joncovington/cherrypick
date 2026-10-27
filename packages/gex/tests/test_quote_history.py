"""The recorder's chain quotes, every tick (`gex_quote_history`, 2026-10-12)."""

import json
import sqlite3
import time
from datetime import datetime, timedelta

from cherrypick.core import quotehistory
from cherrypick.core.clock import ET
from test_service import EXPIRY, IN_SESSION

from cherrypick.gex import service

SPOT = 6002.0
STRIKES = [5800 + 5 * i for i in range(81)]  # 5800..6200


def _seed(path, *, stale=(), crossed=()):
    """A chain of 81 strikes on each right, fresh quotes and deltas, except the legs named stale/crossed."""
    now = time.time()
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stream_chain (streamer_symbol TEXT PRIMARY KEY, expiration TEXT, underlying_symbol TEXT,
                                   data_json TEXT, updated_at REAL);
        CREATE TABLE stream_greeks (symbol TEXT PRIMARY KEY, delta REAL, gamma REAL, theta REAL, vega REAL,
                                    rho REAL, iv REAL, price REAL, updated_at REAL);
        CREATE TABLE stream_trades (symbol TEXT PRIMARY KEY, last REAL, change REAL, volume REAL, updated_at REAL);
        CREATE TABLE stream_oi (symbol TEXT PRIMARY KEY, open_interest INTEGER, updated_at REAL);
        CREATE TABLE stream_quotes (symbol TEXT PRIMARY KEY, bid REAL, ask REAL, mid REAL, bid_size REAL,
                                    ask_size REAL, updated_at REAL);
    """)
    conn.execute(
        "INSERT INTO stream_trades (symbol, last, volume, updated_at) VALUES ('SPX', ?, 0, ?)", (SPOT, now)
    )
    for k in STRIKES:
        for right in ("C", "P"):
            sym = f".SPX_{right.lower()}{k}"
            opt = {
                "streamer_symbol": sym,
                "strike_price": k,
                "option_type": right,
                "shares_per_contract": 100,
            }
            conn.execute("INSERT INTO stream_chain VALUES (?,?,?,?,0)", (sym, EXPIRY, "SPX", json.dumps(opt)))
            conn.execute(
                "INSERT INTO stream_greeks (symbol, delta, gamma, iv, updated_at) VALUES (?,?,?,?,?)",
                (sym, 0.5 if right == "C" else -0.5, 0.01, 0.2, now),
            )
            bid, ask = (2.0, 1.0) if sym in crossed else (1.0, 1.2)
            conn.execute(
                "INSERT INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
                (sym, bid, ask, (bid + ask) / 2, now - 600 if sym in stale else now),
            )
    conn.commit()
    conn.close()


def _cfg(tmp_path, **seed):
    db = tmp_path / "stream_cache.db"
    _seed(db, **seed)
    return {
        "stream_cache_db": db,
        "history_db_path": tmp_path / "gex_history.db",
        "symbols": ["SPX"],
        "quote_history": {"enabled": True},
    }


def _rows(cfg):
    """Every recorded quote, in the first layout's row shape (side by name), oldest sample first."""
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT s.symbol, s.trade_date, s.ts, s.expiration, q.side, q.strike, q.bid, q.ask, q.mid, "
                "q.age_s, q.delta, q.delta_age_s, q.greek_price, q.greek_iv "
                "FROM gex_quote_samples s JOIN gex_quotes q ON q.sample_id = s.id "
                "ORDER BY s.ts, q.side DESC, q.strike"
            )
        ]
    finally:
        conn.close()
    names = {v: k for k, v in service.QUOTE_SIDES.items()}
    return [{**r, "side": names[r["side"]]} for r in rows]


def test_a_sample_keeps_the_nearest_twenty_strikes_each_side_on_both_rights(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 80
    rows = _rows(cfg)
    puts = [r["strike"] for r in rows if r["side"] == "put"]
    assert puts[0] == 5905.0 and puts[-1] == 6100.0 and len(puts) == 40  # 20 below 6002, 20 at/above
    assert {r["expiration"] for r in rows} == {EXPIRY}
    assert all(r["delta"] is not None and r["mid"] == 1.1 for r in rows)


def test_each_quote_carries_its_greeks_age_price_and_vol(tmp_path):
    """The feed's delta lags its quotes (2026-10-27): recorded without its age it cannot be read."""
    cfg = _cfg(tmp_path)
    conn = sqlite3.connect(cfg["stream_cache_db"])
    conn.execute("UPDATE stream_greeks SET updated_at = updated_at - 75, price = 1.4, iv = 0.18")
    conn.commit()
    conn.close()
    service.record_quotes(cfg, now_et=IN_SESSION)
    db = sqlite3.connect(cfg["history_db_path"])
    ages = db.execute("SELECT delta_age_s, greek_price, greek_iv FROM gex_quotes").fetchall()
    db.close()
    assert ages and all(75 <= a < 90 and p == 1.4 and v == 0.18 for a, p, v in ages)


def test_an_older_history_file_gains_the_greek_columns_and_keeps_its_rows(tmp_path):
    cfg = _cfg(tmp_path)
    old = sqlite3.connect(cfg["history_db_path"])
    old.executescript("""
        CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                                        ts REAL NOT NULL, expiration TEXT NOT NULL);
        CREATE TABLE gex_quotes (sample_id INTEGER NOT NULL, side INTEGER NOT NULL, strike REAL NOT NULL,
                                 bid REAL NOT NULL, ask REAL NOT NULL, mid REAL NOT NULL, age_s REAL, delta REAL,
                                 PRIMARY KEY (sample_id, side, strike)) WITHOUT ROWID;
        INSERT INTO gex_quote_samples VALUES (1, 'SPX', '2026-10-26', 1.0, '2026-10-26');
        INSERT INTO gex_quotes VALUES (1, 1, 6000, 1.0, 1.2, 1.1, 0.5, 0.5);
    """)
    old.commit()
    old.close()
    service.record_quotes(cfg, now_et=IN_SESSION)
    db = sqlite3.connect(cfg["history_db_path"])
    cols = {r[1] for r in db.execute("PRAGMA table_info(gex_quotes)")}
    kept = db.execute("SELECT delta, delta_age_s FROM gex_quotes WHERE sample_id = 1").fetchone()
    db.close()
    assert set(service.GREEK_COLUMNS) <= cols
    assert kept == (0.5, None)


def test_a_stale_or_crossed_quote_is_absent_never_a_zero(tmp_path):
    cfg = _cfg(tmp_path, stale={".SPX_p6000"}, crossed={".SPX_c6005"})
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 78
    kept = {(r["side"], r["strike"]) for r in _rows(cfg)}
    assert ("put", 6000.0) not in kept and ("call", 6005.0) not in kept
    assert ("call", 6000.0) in kept and ("put", 6005.0) in kept


def test_one_sample_a_tick_and_nothing_outside_rth(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 80
    assert service.record_quotes(cfg, now_et=IN_SESSION + timedelta(seconds=5)) == 0  # a double write
    assert service.record_quotes(cfg, now_et=IN_SESSION + timedelta(seconds=15)) == 80  # the next tick
    (tmp_path / "off").mkdir()
    other = _cfg(tmp_path / "off")
    for when in (datetime(2026, 9, 30, 9, 0, tzinfo=ET), datetime(2026, 9, 26, 11, 0, tzinfo=ET)):
        assert service.record_quotes(other, now_et=when) == 0
    assert not other["history_db_path"].exists()


def test_a_session_reads_back_with_spot_joined_at_or_before(tmp_path):
    """Shown to fail without the join rule: a spot recorded AFTER the sample is never used, and one
    older than the bound leaves the minute's spot unmeasured rather than borrowed."""
    cfg = _cfg(tmp_path)
    t0 = IN_SESSION
    service.record_quotes(cfg, now_et=t0)
    service.record_quotes(cfg, now_et=t0 + timedelta(minutes=5))
    conn = sqlite3.connect(cfg["history_db_path"])
    day = t0.date().isoformat()
    conn.executemany(
        "INSERT INTO gex_spot_history (symbol, trade_date, ts, spot) VALUES ('SPX', ?, ?, ?)",
        [(day, t0.timestamp() - 10, 6001.5), (day, t0.timestamp() + 5, 6009.0)],
    )
    conn.commit()
    conn.close()
    first, second = quotehistory.session("SPX", day, history_db=cfg["history_db_path"])
    assert first["underlying_price"] == 6001.5, "the later 6009.0 is look-ahead"
    assert first["now_min"] == 11 * 60 and first["puts"][6000.0]["bid"] == 1.0
    assert second["underlying_price"] is None and second["spot_reason"] == "spot_stale"
    assert set(first["calls"]) == set(first["puts"]) and len(first["calls"]) == 40


def test_the_recorder_loop_calls_it(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "record_quotes", lambda cfg, **k: seen.append(1) or 0)
    monkeypatch.setattr(service, "record_spots", lambda cfg, **k: 0)
    monkeypatch.setattr(service, "record_regimes", lambda cfg, **k: 0)
    monkeypatch.setattr(
        service._regime, "sample", lambda cfg, **k: {"status": "ok", "usable": 0, "written": 0}
    )
    service.run_recorder({**_cfg(tmp_path), "serve": {"refresh_seconds": 15}}, once=True)
    assert seen == [1]


# --------------------------------------------------------------------------- the config switch
def test_it_is_off_unless_a_config_turns_it_on(tmp_path):
    """Shown to fail with the default on: no block, or enabled false, records nothing and creates no
    history file."""
    cfg = _cfg(tmp_path)
    for block in (None, {}, {"enabled": False}, {"enabled": "yes"}):
        off = {k: v for k, v in cfg.items() if k != "quote_history"}
        if block is not None:
            off["quote_history"] = block
        assert service.record_quotes(off, now_et=IN_SESSION) == 0, block
    assert not cfg["history_db_path"].exists()
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 80


def test_its_own_symbols_override_the_recorders(tmp_path):
    cfg = {**_cfg(tmp_path), "symbols": ["SPX", "XSP", "QQQ"]}
    assert service.quote_history_settings({**cfg, "quote_history": {"enabled": True}})["symbols"] == [
        "SPX",
        "XSP",
        "QQQ",
    ]
    only = {**cfg, "quote_history": {"enabled": True, "symbols": ["spx"]}}
    assert service.quote_history_settings(only)["symbols"] == ["SPX"]
    assert service.record_quotes(only, now_et=IN_SESSION) == 80


def test_its_settings_are_read_and_a_bad_value_keeps_the_default(tmp_path):
    cfg = _cfg(tmp_path)
    narrow = {**cfg, "quote_history": {"enabled": True, "strikes_each_side": 5, "interval_seconds": 60}}
    assert service.record_quotes(narrow, now_et=IN_SESSION) == 20
    assert service.record_quotes(narrow, now_et=IN_SESSION + timedelta(seconds=30)) == 0
    bad = service.quote_history_settings(
        {
            "quote_history": {
                "enabled": True,
                "strikes_each_side": "20",
                "interval_seconds": -1,
                "symbols": "SPX",
            }
        }
    )
    assert bad["strikes_each_side"] == 20 and bad["interval_seconds"] == 10 and bad["symbols"] == []


# --------------------------------------------------------------------------- the storage layout
def test_a_session_round_trips_exactly_what_the_recorder_saw(tmp_path, monkeypatch):
    """Every quote `chain_quotes` handed the recorder comes back from `core.quotehistory` unchanged,
    sample by sample, with the sample's expiration and time. Shown to fail by swapping the side codes
    in the reader (puts read back as calls)."""
    cfg = _cfg(tmp_path, crossed={".SPX_c6005"})
    seen = []
    real = service._provider.chain_quotes

    def spy(*a, **k):
        seen.append(real(*a, **k))
        return seen[-1]

    monkeypatch.setattr(service._provider, "chain_quotes", spy)
    ticks = [IN_SESSION + timedelta(seconds=15 * i) for i in range(3)]
    for when in ticks:
        assert service.record_quotes(cfg, now_et=when) == 79
    got = quotehistory.session("SPX", IN_SESSION.date().isoformat(), history_db=cfg["history_db_path"])
    assert [s["ts"] for s in got] == [t.timestamp() for t in ticks]
    for sample, quotes in zip(got, seen, strict=True):
        assert sample["expiration"] == EXPIRY and sample["symbol"] == "SPX"
        back = {
            ("put" if book == "puts" else "call", strike): q
            for book in ("puts", "calls")
            for strike, q in sample[book].items()
        }
        want = {
            (q["side"], q["strike"]): {
                "bid": q["bid"],
                "ask": q["ask"],
                "mid": q["mid"],
                "age_seconds": q["age_seconds"],
                "delta": q["delta"],
            }
            for q in quotes
        }
        assert back == want


def test_the_compact_layout_is_well_under_the_first(tmp_path):
    """Bytes for the same rows in both layouts: the reason for the change, kept as a check."""
    cfg = _cfg(tmp_path)
    for i in range(60):
        service.record_quotes(cfg, now_et=IN_SESSION + timedelta(seconds=15 * i))
    rows = _rows(cfg)
    assert len(rows) == 60 * 80
    first = sqlite3.connect(tmp_path / "first.db")
    first.execute(
        "CREATE TABLE gex_quote_history (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, "
        "expiration TEXT NOT NULL, side TEXT NOT NULL, strike REAL NOT NULL, bid REAL NOT NULL, "
        "ask REAL NOT NULL, mid REAL NOT NULL, age_s REAL, delta REAL, "
        "delta_age_s REAL, greek_price REAL, greek_iv REAL)"
    )
    first.execute("CREATE INDEX idx_gqh_sym_date ON gex_quote_history(symbol, trade_date, ts)")
    # Same content in both layouts, the feed-greek columns included (2026-10-27): the check is the
    # layout's cost, not the columns'.
    first.executemany(
        "INSERT INTO gex_quote_history VALUES (:symbol,:trade_date,:ts,:expiration,:side,:strike,:bid,:ask,"
        ":mid,:age_s,:delta,:delta_age_s,:greek_price,:greek_iv)",
        rows,
    )
    first.commit()
    first.close()
    compact = tmp_path / "compact.db"
    src = sqlite3.connect(cfg["history_db_path"])
    src.execute("VACUUM INTO ?", (str(compact),))
    src.close()
    drop = sqlite3.connect(compact)  # the quotes alone: the recorder's file has its other tables too
    for table in ("gex_spot_history", "gex_regime_history", "gex_profile_history"):
        drop.execute(f"DROP TABLE {table}")
    drop.commit()
    drop.execute("VACUUM")
    drop.close()
    assert compact.stat().st_size < 0.6 * (tmp_path / "first.db").stat().st_size


def test_an_empty_first_layout_table_is_dropped_and_one_with_rows_is_kept(tmp_path):
    for keep in (False, True):
        where = tmp_path / str(keep)
        where.mkdir()
        cfg = _cfg(where)
        conn = sqlite3.connect(cfg["history_db_path"])
        conn.execute("CREATE TABLE gex_quote_history (symbol TEXT, ts REAL)")
        if keep:
            conn.execute("INSERT INTO gex_quote_history VALUES ('SPX', 1.0)")
        conn.commit()
        conn.close()
        service.record_quotes(cfg, now_et=IN_SESSION)
        conn = sqlite3.connect(cfg["history_db_path"])
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        conn.close()
        assert ("gex_quote_history" in tables) is keep
        assert {"gex_quote_samples", "gex_quotes"} <= tables


# --------------------------------------------------------------------------- the light chain read
def test_the_light_read_picks_the_chain_the_full_snapshot_does(tmp_path):
    cfg = _cfg(tmp_path)
    full = service._provider.snapshot_from_stream_cache(cfg["stream_cache_db"], "SPX")
    light = service._provider.chain_from_stream_cache(cfg["stream_cache_db"], "SPX")
    assert (light.spot, light.expiration, light.chain_entries) == (
        full.spot,
        full.expiration,
        full.chain_entries,
    )
    assert full.greeks and not light.greeks and not light.oi and not light.volume
    kw = {"each_side": 20, "max_age_seconds": 120.0}
    a = service._provider.chain_quotes(cfg["stream_cache_db"], full, **kw)
    b = service._provider.chain_quotes(cfg["stream_cache_db"], light, **kw)
    assert [(q["side"], q["strike"], q["bid"], q["ask"], q["mid"], q["delta"]) for q in a] == [
        (q["side"], q["strike"], q["bid"], q["ask"], q["mid"], q["delta"]) for q in b
    ]


# --------------------------------------------------------------------------- held strikes
def test_a_declared_held_leg_outside_the_window_is_recorded(tmp_path):
    """Shown to fail without `held_symbols`: 5850 is 30 strikes below spot, outside the 20."""
    from cherrypick.core import streamrequests

    cfg = _cfg(tmp_path)
    trades = tmp_path / "trades.db"
    conn = sqlite3.connect(trades)
    conn.execute("CREATE TABLE fly_positions (wing_leg_symbol TEXT, status TEXT)")
    conn.executemany(
        "INSERT INTO fly_positions VALUES (?, ?)", [(".SPX_p5850", "open"), (".SPX_p5800", "closed")]
    )
    conn.commit()
    conn.close()
    query = "SELECT wing_leg_symbol FROM fly_positions WHERE status = 'open'"
    streamrequests.write_request("flies", ["SPX"], leg_sources=[streamrequests.leg_source(trades, query)])
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 82
    kept = {(r["side"], r["strike"]) for r in _rows(cfg)}
    assert ("put", 5850.0) in kept and ("call", 5850.0) in kept and ("put", 5800.0) not in kept
    off = {**cfg, "quote_history": {"enabled": True, "held_strikes": False}}
    assert service.record_quotes(off, now_et=IN_SESSION + timedelta(seconds=15)) == 80


# --------------------------------------------------------------------------- the history file
def test_the_history_db_is_wal_and_a_read_only_reader_opens_it(tmp_path):
    from cherrypick.core import db as core_db

    cfg = _cfg(tmp_path)
    service.record_quotes(cfg, now_et=IN_SESSION)
    conn = sqlite3.connect(cfg["history_db_path"])
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
    # Every writer is closed, so no -shm is left; a mode=ro reader (flies, overview) still reads it.
    ro = core_db.connect_ro(cfg["history_db_path"])
    try:
        assert ro.execute("SELECT COUNT(*) FROM gex_quotes").fetchone()[0] == 80
    finally:
        ro.close()


def test_a_failing_series_loses_only_its_own_rows_in_a_shared_tick(tmp_path, monkeypatch):
    """Shown to fail without the savepoint in `historydb.step`: the spots written before the failing
    series are committed with the tick, and nothing the failing series wrote is."""
    from cherrypick.gex import historydb

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(service._provider, "read_spots", lambda *a, **k: {"SPX": SPOT})
    try:
        with historydb.tick(cfg["history_db_path"]) as conn:
            assert service.record_spots(cfg, conn=conn) == 1
            with historydb.step(cfg["history_db_path"], conn) as c:
                c.execute(
                    "INSERT INTO gex_quote_samples (symbol, trade_date, ts, expiration) VALUES ('X','d',1,'e')"
                )
                raise RuntimeError("boom")
    except RuntimeError:
        pass
    db = sqlite3.connect(cfg["history_db_path"])
    try:
        assert db.execute("SELECT COUNT(*) FROM gex_spot_history").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM gex_quote_samples").fetchone()[0] == 0
    finally:
        db.close()


def test_flies_wall_read_uses_the_symbol_ts_index(tmp_path):
    """`core.regime.gex_at`'s query, planned against the recorder's schema. Shown to fail with the
    index removed (the plan falls back to the (symbol, trade_date) index and a sort)."""
    cfg = _cfg(tmp_path)
    service.record_quotes(cfg, now_et=IN_SESSION)
    conn = sqlite3.connect(cfg["history_db_path"])
    try:
        plan = " ".join(
            str(r[-1])
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT ts FROM gex_regime_history WHERE symbol = ? AND ts <= ? "
                "AND (expiration IS NULL OR expiration >= trade_date) ORDER BY ts DESC",
                ("SPX", 1.0),
            )
        )
    finally:
        conn.close()
    assert "idx_grh_sym_ts" in plan and "TEMP B-TREE" not in plan, plan
