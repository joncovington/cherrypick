"""End-to-end: seed a MEIC-style stream cache, then drive the read-only provider + service.

No streamer, no network — just a temp SQLite shaped like the real stream_cache.db.
"""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

import pytest
from cherrypick.core import gex
from cherrypick.core.clock import ET

from cherrypick.gex import provider, service

# A Wednesday, mid-session: the recorder only writes during RTH, so tests that want a row say when.
IN_SESSION = datetime(2026, 9, 30, 11, 0, tzinfo=ET)

# Two days out, not today: the provider's forward-only horizon compares against the CURRENT date,
# and a chain seeded to expire "today" in local time is already expired once UTC (and ET) cross
# midnight -- which made this whole file fail every night between roughly 22:00 local and midnight,
# exactly the window the CI schedule sits in. The fixture needs an unexpired chain, not a same-day
# one; nothing here tests expiry selection except the two-expiration tests, which seed their own.
EXPIRY = (date.today() + timedelta(days=2)).isoformat()

# One 0DTE-ish chain for SPX: two strikes, calls + puts. gamma/OI/volume chosen so the OI and volume
# GEX series clearly diverge (C610 has heavy OI but light volume).
_CHAIN = [
    {"streamer_symbol": ".SPX_c600", "strike_price": 600, "option_type": "C", "shares_per_contract": 100},
    {"streamer_symbol": ".SPX_p600", "strike_price": 600, "option_type": "P", "shares_per_contract": 100},
    {"streamer_symbol": ".SPX_c610", "strike_price": 610, "option_type": "C", "shares_per_contract": 100},
]
_GREEKS = {".SPX_c600": (0.01, 0.20), ".SPX_p600": (0.01, 0.22), ".SPX_c610": (0.05, 0.18)}  # gamma, iv(dec)
_OI = {".SPX_c600": 100, ".SPX_p600": 300, ".SPX_c610": 50}
_VOL = {".SPX_c600": 10, ".SPX_p600": 20, ".SPX_c610": 5}


def _seed_cache(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stream_chain (streamer_symbol TEXT PRIMARY KEY, expiration TEXT, underlying_symbol TEXT,
                                   data_json TEXT, updated_at REAL);
        CREATE TABLE stream_greeks (symbol TEXT PRIMARY KEY, delta REAL, gamma REAL, theta REAL, vega REAL,
                                    rho REAL, iv REAL, price REAL, updated_at REAL);
        CREATE TABLE stream_trades (symbol TEXT PRIMARY KEY, last REAL, change REAL, volume REAL, updated_at REAL);
        CREATE TABLE stream_oi (symbol TEXT PRIMARY KEY, open_interest INTEGER, updated_at REAL);
    """)
    # A FRESH print: the recorder age-gates its samples, so updated_at=0 would now read as a
    # stalled feed and be skipped (which the dedicated test below covers).
    conn.execute(
        "INSERT INTO stream_trades (symbol, last, volume, updated_at) VALUES ('SPX', 605.0, 0, ?)",
        (time.time(),),
    )
    for opt in _CHAIN:
        conn.execute(
            "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, updated_at)"
            " VALUES (?,?,?,?,0)",
            (opt["streamer_symbol"], EXPIRY, "SPX", json.dumps(opt)),
        )
        sym = opt["streamer_symbol"]
        gamma, iv = _GREEKS[sym]
        conn.execute(
            "INSERT INTO stream_greeks (symbol, gamma, iv, updated_at) VALUES (?,?,?,0)", (sym, gamma, iv)
        )
        conn.execute(
            "INSERT INTO stream_oi (symbol, open_interest, updated_at) VALUES (?,?,0)", (sym, _OI[sym])
        )
        conn.execute(
            "INSERT INTO stream_trades (symbol, last, volume, updated_at) VALUES (?,?,?,0)",
            (sym, 0, _VOL[sym]),
        )
    conn.commit()
    conn.close()


def _cfg(tmp_path):
    db = tmp_path / "stream_cache.db"
    _seed_cache(db)
    return {
        "stream_cache_db": db,
        "history_db_path": tmp_path / "gex_history.db",
        "symbols": ["SPX"],
        "serve": {"host": "127.0.0.1", "port": 5055, "refresh_seconds": 15},
    }


def test_provider_reads_chain_greeks_oi_volume(tmp_path):
    cfg = _cfg(tmp_path)
    snap = provider.snapshot_from_stream_cache(cfg["stream_cache_db"], "SPX")
    assert snap.spot == 605.0 and snap.expiration == EXPIRY
    assert len(snap.chain_entries) == 3
    assert snap.oi[".SPX_c610"] == 50 and snap.volume[".SPX_c600"] == 10
    # IV normalised from raw decimal to percent
    assert abs(snap.greeks[".SPX_c600"]["iv"] - 20.0) < 1e-9


def test_provider_opens_read_only(tmp_path):
    cfg = _cfg(tmp_path)
    conn = provider._connect_ro(cfg["stream_cache_db"])
    try:
        import pytest

        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO stream_oi (symbol, open_interest, updated_at) VALUES ('x',1,0)")
    finally:
        conn.close()


# The gexbot zero-gamma / net-wall / volume-total math moved to cherrypick.core.gex (shared with MEIC);
# these keep the gex package's golden values as a regression guard against the core it now imports.
def test_nearest_zero_gamma_picks_crossing_closest_to_spot():
    # Series [-10, 5, -20, 20] sign-flips 3x (90.67, ~92.8, 100.5); returns the crossing nearest spot.
    series = [
        {"strike": 90.0, "net": -10},
        {"strike": 91.0, "net": 5},
        {"strike": 100.0, "net": -20},
        {"strike": 101.0, "net": 20},
    ]
    assert gex.nearest_zero_gamma(series, 100.5, "net") == 100.5
    assert gex.nearest_zero_gamma(series, 90.0, "net") == 90.67


def test_nearest_zero_gamma_none_when_no_sign_change():
    series = [{"strike": 100.0, "net": 5}, {"strike": 101.0, "net": 10}]
    assert gex.nearest_zero_gamma(series, 100.0, "net") is None


def test_net_walls_are_net_gex_extremes():
    series = [
        {"strike": 100.0, "net": -30},
        {"strike": 101.0, "net": 50},
        {"strike": 102.0, "net": 10},
    ]
    assert gex.net_walls(series, "net") == (101.0, 100.0)  # max-net, min-net
    assert gex.net_walls([], "net") == (None, None)


def test_volume_totals_rolls_up_vol_fields():
    series = [
        {"strike": 100.0, "call_gex_vol": 30, "put_gex_vol": -50, "net_gex_vol": -20},
        {"strike": 101.0, "call_gex_vol": 90, "put_gex_vol": -10, "net_gex_vol": 80},
    ]
    vt = gex.volume_totals(series)
    assert vt["total_call_gex_vol"] == 120  # 30 + 90 (only positives)
    assert vt["total_put_gex_vol"] == 60  # abs(-50 + -10)
    assert vt["net_gex_vol"] == 60  # -20 + 80


def test_build_gex_payload_shape_and_oi_vs_volume(tmp_path):
    cfg = _cfg(tmp_path)
    out = service.build_gex(cfg, "SPX")
    assert out["ok"] is True
    assert out["symbol"] == "SPX" and out["expiration"] == EXPIRY
    assert {"series", "totals", "spot_history", "market_open_ts", "market_close_ts"} <= out.keys()
    s600 = next(s for s in out["series"] if s["strike"] == 600)
    # OI ("positioning") and volume ("flow") series are computed independently and diverge
    assert s600["net_gex"] != s600["net_gex_vol"]
    t = out["totals"]
    assert t["call_wall"] == 610 and t["put_wall"] == 600
    assert t["zero_gamma"] is not None
    # Volume rollups sit alongside the OI keys.
    for k in (
        "total_call_gex_vol",
        "total_put_gex_vol",
        "net_gex_vol",
        "zero_gamma_vol",
        "call_wall_vol",
        "put_wall_vol",
    ):
        assert k in t
    # build_gex reads the spot trail read-only (the dashboard's recorder writes it) — a list, empty
    # until record_spots has run.
    assert isinstance(out["spot_history"], list)


def test_record_spots_records_every_symbol_then_build_gex_reads_the_trail(tmp_path):
    cfg = _cfg(tmp_path)
    # record_spots samples EVERY offered symbol with a cached spot (not just the one on screen), so a
    # symbol's trail has no gap when the viewer switches — the whole point of the background recorder.
    assert service.record_spots(cfg) == 1  # only SPX has a cached spot in this fixture
    assert service.record_spots(cfg) == 1  # a second sample -> a second point
    out = service.build_gex(cfg, "SPX")
    assert len(out["spot_history"]) == 2  # build_gex reads back both recorded ticks
    # a symbol with no cached spot is simply skipped, never errors
    assert service.record_spots(cfg, symbols=["NOPE"]) == 0


def test_build_gex_reports_not_ready_when_symbol_absent(tmp_path):
    cfg = _cfg(tmp_path)
    out = service.build_gex(cfg, "QQQ")
    assert out["ok"] is False and "no cached chain" in out["error"]


def test_record_regimes_persists_a_compact_summary_row(tmp_path):
    """The historical dimension the audit found missing entirely: the profile was
    recomputed live and discarded, so regime-vs-outcome analysis was impossible."""
    import sqlite3

    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 1  # only SPX has a cached chain here
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM gex_regime_history").fetchone()
    conn.close()
    assert row["symbol"] == "SPX"
    assert row["net_gex"] is not None and row["net_gex_vol"] is not None
    assert row["call_wall"] == 610 and row["put_wall"] == 600
    assert row["spot"] is not None and row["expiration"]


def test_record_regimes_records_flow_at_the_walls(tmp_path):
    """Calls traded at the call wall (610: 5), puts at the put wall (600: 20), and chain totals."""
    import sqlite3

    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 1
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM gex_regime_history").fetchone()
    conn.close()
    assert (row["call_volume"], row["put_volume"]) == (15, 20)
    assert (row["call_wall_volume"], row["put_wall_volume"]) == (5, 20)
    assert row["total_call_gex_vol"] > 0 and row["total_put_gex_vol"] > 0
    assert row["net_gex_vol"] == pytest.approx(row["total_call_gex_vol"] - row["total_put_gex_vol"], abs=2)


def test_an_existing_history_db_gains_the_flow_columns_and_keeps_its_rows(tmp_path):
    """The live database predates the columns: it must be migrated in place, its rows untouched and
    NULL in the new columns (never backfilled), and the next reading must carry them."""
    import sqlite3

    cfg = _cfg(tmp_path)
    old = sqlite3.connect(cfg["history_db_path"])
    old.execute(
        "CREATE TABLE gex_regime_history (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, ts REAL NOT NULL, "
        "spot REAL, net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, "
        "expiration TEXT)"
    )
    old.execute(
        "INSERT INTO gex_regime_history VALUES ('SPX','2026-10-02',1.0,7700,1,1,7690,7750,7650,'2026-10-02')"
    )
    old.commit()
    old.close()
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 1
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM gex_regime_history ORDER BY ts").fetchall()
    conn.close()
    assert len(rows) == 2 and rows[0]["call_wall"] == 7750
    tables = {r[0] for r in sqlite3.connect(cfg["history_db_path"]).execute("SELECT name FROM sqlite_master")}
    assert "gex_profile_history" in tables
    assert all(rows[0][c] is None for c in service.FLOW_COLUMNS)
    assert rows[1]["call_wall_volume"] == 5


def test_record_regimes_records_coverage_and_the_per_strike_profile(tmp_path):
    """Coverage says which contracts the totals could see; the profile keeps every strike with data,
    keyed to the regime row by (symbol, ts), so a past session can be re-asked at any strike."""
    import sqlite3

    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 1
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM gex_regime_history").fetchone()
    prof = conn.execute("SELECT * FROM gex_profile_history ORDER BY strike").fetchall()
    conn.close()
    assert (row["volume_contracts"], row["volume_low_strike"], row["volume_high_strike"]) == (3, 600, 610)
    assert [p["strike"] for p in prof] == [600, 610]
    assert all(p["symbol"] == "SPX" and p["ts"] == row["ts"] for p in prof)
    at600, at610 = prof
    assert (at600["call_oi"], at600["put_oi"], at600["call_vol"], at600["put_vol"]) == (100, 300, 10, 20)
    assert (at610["call_oi"], at610["put_oi"], at610["call_vol"], at610["put_vol"]) == (50, 0, 5, 0)
    assert at610["call_gamma"] == 0.05 and at600["put_iv"] == 22.0


def test_an_all_zero_strike_is_not_stored_and_coverage_handles_no_trades():
    zero = {c: 0 for c in service.PROFILE_COLUMNS}
    series = [{"strike": 5000, **zero}, {"strike": 6000, **zero, "put_oi": 7}]
    assert [s["strike"] for s in service.profile_rows(series)] == [6000]
    entries = [{"streamer_symbol": ".X", "strike_price": 60.0}]
    assert service.volume_coverage(entries, {}) == {
        "volume_contracts": 0,
        "volume_low_strike": None,
        "volume_high_strike": None,
    }
    assert service.volume_coverage(entries, {".X": 0}, strike_scale=10)["volume_low_strike"] == 600.0


def test_a_wall_with_no_series_row_records_null_not_zero():
    series = [{"strike": 600, "call_vol": 3, "put_vol": 4}]
    flow = service.flow_measures(series, 610, 600, {})
    assert flow["call_wall_volume"] is None and flow["put_wall_volume"] == 4


def test_record_regimes_throttles_to_one_row_per_interval(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 1
    # An immediate second call is inside the 5-minute throttle: no second row.
    assert service.record_regimes(cfg, now_et=IN_SESSION) == 0
    # But an explicit zero interval writes again (the cadence knob is the caller's).
    assert service.record_regimes(cfg, min_interval_s=0, now_et=IN_SESSION) == 1


def test_record_regimes_skips_symbols_without_chains(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, symbols=["QQQ"], now_et=IN_SESSION) == 0


def test_record_regimes_writes_nothing_outside_rth(tmp_path):
    """Off-hours rows were readings of no session: the provider fell forward to whatever chain was
    still streaming (another module's extra window) and the row was filed under today. The overview
    read one as the pre-open gamma flip and walls, and the advisor as the day's "open" walls."""
    cfg = _cfg(tmp_path)
    for when in (
        datetime(2026, 9, 30, 8, 45, tzinfo=ET),  # pre-open, the overview's own run time
        datetime(2026, 9, 30, 16, 0, tzinfo=ET),  # the bell itself is outside
        datetime(2026, 9, 30, 23, 0, tzinfo=ET),  # overnight
        datetime(2026, 9, 26, 11, 0, tzinfo=ET),  # a Saturday at midday
        datetime(2026, 11, 26, 11, 0, tzinfo=ET),  # Thanksgiving
    ):
        assert service.record_regimes(cfg, min_interval_s=0, now_et=when) == 0, when
    # The gate sits before any write, so off-hours does not even create the history DB.
    assert not cfg["history_db_path"].exists()


def _history_with_off_hours_rows(tmp_path):
    db = tmp_path / "gex_history.db"
    conn = sqlite3.connect(db)
    service._ensure_history_table(conn)
    rows = [
        ("2026-09-21", datetime(2026, 9, 21, 0, 5, tzinfo=ET)),  # overnight, another expiry's chain
        ("2026-09-21", datetime(2026, 9, 21, 8, 41, tzinfo=ET)),  # pre-open: the overview's read
        ("2026-09-21", datetime(2026, 9, 21, 10, 0, tzinfo=ET)),  # a real reading
        ("2026-09-21", datetime(2026, 9, 21, 16, 30, tzinfo=ET)),  # after the bell
        ("2026-09-20", datetime(2026, 9, 20, 12, 0, tzinfo=ET)),  # a Sunday
    ]
    conn.executemany(
        "INSERT INTO gex_regime_history (symbol, trade_date, ts, net_gex) VALUES ('SPX', ?, ?, 1.0)",
        [(d, w.timestamp()) for d, w in rows],
    )
    conn.commit()
    conn.close()
    return db


def test_repair_history_dry_run_reports_and_touches_nothing(tmp_path):
    db = _history_with_off_hours_rows(tmp_path)
    report = service.repair_regime_history(db)
    assert report["off_hours"] == 4 and report["kept"] == 1
    assert report["by_date"] == {"2026-09-20": 1, "2026-09-21": 3}
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM gex_regime_history").fetchone()[0] == 5
    conn.close()
    assert not list(tmp_path.glob("gex_history.db.bak-*"))


def test_repair_history_apply_keeps_only_rth_rows_and_a_backup(tmp_path):
    db = _history_with_off_hours_rows(tmp_path)
    report = service.repair_regime_history(db, apply=True)
    conn = sqlite3.connect(db)
    left = [r[0] for r in conn.execute("SELECT ts FROM gex_regime_history")]
    conn.close()
    assert left == [datetime(2026, 9, 21, 10, 0, tzinfo=ET).timestamp()]
    backup = sqlite3.connect(report["backup"])
    assert backup.execute("SELECT COUNT(*) FROM gex_regime_history").fetchone()[0] == 5
    backup.close()


def _stamp_greeks(db, stamps: dict[str, float]) -> None:
    """Set each strike's greeks AND open-interest age, as the producer would on a live subscription."""
    conn = sqlite3.connect(db)
    for sym, ts in stamps.items():
        conn.execute("UPDATE stream_greeks SET updated_at = ? WHERE symbol = ?", (ts, sym))
        conn.execute("UPDATE stream_oi SET updated_at = ? WHERE symbol = ?", (ts, sym))
    conn.commit()
    conn.close()


def test_a_leftover_strike_is_not_summed_into_the_profile(tmp_path):
    """A strike the producer's window re-centred away from keeps its last greeks forever. On
    2026-09-30 a week of them on bwb's 10-02 window moved that chain's zero-gamma 545 points."""
    cfg = _cfg(tmp_path)
    now = time.time()
    # C610 stopped updating an hour before the rest of its chain: a leftover.
    _stamp_greeks(cfg["stream_cache_db"], {".SPX_c600": now, ".SPX_p600": now - 5, ".SPX_c610": now - 3600})

    snap = provider.snapshot_from_stream_cache(cfg["stream_cache_db"], "SPX")

    assert snap.leftover_rows_dropped == 1
    assert ".SPX_c610" not in snap.greeks and ".SPX_c610" not in snap.oi
    assert set(snap.greeks) == {".SPX_c600", ".SPX_p600"}
    # And the age it reports is the live chain's, not the leftover's hour.
    assert snap.input_age_seconds is not None and snap.input_age_seconds < 60


def test_a_chain_updating_together_keeps_every_strike(tmp_path):
    """The cut is relative to the chain's own newest row, so a whole chain that is merely old (a
    quiet feed) keeps every strike -- staleness of the FEED is input_age_seconds' job, not this."""
    cfg = _cfg(tmp_path)
    old = time.time() - 7200
    _stamp_greeks(cfg["stream_cache_db"], {".SPX_c600": old, ".SPX_p600": old - 30, ".SPX_c610": old - 60})

    snap = provider.snapshot_from_stream_cache(cfg["stream_cache_db"], "SPX")

    assert snap.leftover_rows_dropped == 0 and len(snap.greeks) == 3


def test_build_gex_reports_missing_cache(tmp_path):
    cfg = {
        "stream_cache_db": tmp_path / "nope.db",
        "history_db_path": tmp_path / "h.db",
        "symbols": ["SPX"],
        "serve": {},
    }
    out = service.build_gex(cfg, "SPX")
    assert out["ok"] is False and "not found" in out["error"]


def test_record_spots_skips_a_stale_print(tmp_path):
    """A frozen print must not be written as a fresh sample.

    The recorder had no age check and sampled through the night and through any stall: on
    2026-08-19 that produced 5,737 rows of which 4,193 consecutive pairs were the identical value,
    so a dead feed and a quiet market drew the same flat line. Skipping leaves a gap instead.
    """
    cfg = _cfg(tmp_path)
    conn = sqlite3.connect(cfg["stream_cache_db"])
    conn.execute("UPDATE stream_trades SET updated_at = ? WHERE symbol = 'SPX'", (time.time() - 600,))
    conn.commit()
    conn.close()

    assert service.record_spots(cfg) == 0


def test_the_age_gate_can_be_turned_off(tmp_path):
    """`source.max_spot_age_seconds: null` restores the pre-2026-08-20 behaviour."""
    cfg = {**_cfg(tmp_path), "source": {"max_spot_age_seconds": None}}
    conn = sqlite3.connect(cfg["stream_cache_db"])
    conn.execute("UPDATE stream_trades SET updated_at = ? WHERE symbol = 'SPX'", (time.time() - 600,))
    conn.commit()
    conn.close()

    assert service.record_spots(cfg) == 1


def test_spot_max_age_default_and_override():
    assert service.spot_max_age_seconds({}) == service.DEFAULT_SPOT_MAX_AGE_SECONDS
    assert service.spot_max_age_seconds({"source": {"max_spot_age_seconds": 30}}) == 30.0
    assert service.spot_max_age_seconds({"source": {"max_spot_age_seconds": None}}) is None


# --------------------------------------------------------------------------- expired-chain guard


def _seed_two_expirations(db, today, other, *, greeks_on):
    """A cache holding two expirations, with live greeks on only one of them.

    `greeks_on` is where the non-zero gammas go. `stream_greeks` is never pruned, so an expired
    chain keeps its last gammas indefinitely — which is precisely what let one win the horizon.
    """
    _seed_cache(db)  # the standard SPX chain, expiring EXPIRY
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM stream_chain")
    conn.execute("DELETE FROM stream_greeks")
    for expiration in (today, other):
        for opt in _CHAIN:
            sym = f"{opt['streamer_symbol']}@{expiration}"
            stamped = dict(opt, streamer_symbol=sym)
            conn.execute(
                "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol,"
                " data_json, updated_at) VALUES (?,?,?,?,0)",
                (sym, expiration, "SPX", json.dumps(stamped)),
            )
            gamma = _GREEKS[opt["streamer_symbol"]][0] if expiration == greeks_on else None
            conn.execute(
                "INSERT INTO stream_greeks (symbol, gamma, iv, updated_at) VALUES (?,?,?,0)",
                (sym, gamma, 0.2),
            )
    conn.commit()
    conn.close()


def test_an_expired_chain_is_never_used_as_the_gex_horizon(tmp_path):
    """The 2026-08-26 finding: 3,991 of 10,516 recorded regime readings (38%) were computed from a
    chain that had already expired, nearly all frozen at one constant net_gex for hours.

    The mechanism was two-part and both halves are covered here: candidates were ordered by ABSOLUTE
    distance from now, which ranks yesterday as near as tomorrow, and `stream_greeks` is never
    pruned, so the expired chain still satisfied the has-greeks test and won. Gamma exposure on
    contracts that no longer exist is not a reading.
    """
    db = tmp_path / "stream_cache.db"
    today, yesterday = "2026-08-19", "2026-08-18"
    # Only the EXPIRED chain has live greeks — the exact shape that used to win.
    _seed_two_expirations(db, today, yesterday, greeks_on=yesterday)

    snap = provider.snapshot_from_stream_cache(db, "SPX", today=today)

    assert snap.expiration != yesterday, "an expired expiration was used as the GEX horizon"
    assert snap.expiration == today


def test_a_future_expiration_still_wins_when_it_is_the_one_with_greeks(tmp_path):
    """Forward-only ordering must not become today-only: on a session whose own chain is not live
    yet, the nearest FUTURE expiration is the right horizon and the recorder should use it. That is
    what happened on 2026-08-20, where every reading came off the +1d chain."""
    db = tmp_path / "stream_cache.db"
    today, tomorrow = "2026-08-20", "2026-08-21"
    _seed_two_expirations(db, today, tomorrow, greeks_on=tomorrow)

    snap = provider.snapshot_from_stream_cache(db, "SPX", today=today)

    assert snap.expiration == tomorrow


def test_the_nearest_future_expiration_wins_when_both_are_live(tmp_path):
    db = tmp_path / "stream_cache.db"
    today, tomorrow = "2026-08-20", "2026-08-21"
    _seed_two_expirations(db, today, tomorrow, greeks_on=today)

    assert provider.snapshot_from_stream_cache(db, "SPX", today=today).expiration == today


def test_an_all_expired_cache_reports_not_ready_rather_than_a_stale_number(tmp_path):
    """The answer a stale chain was silently replacing. "No usable chain" and "GEX is negative" are
    different facts, and the second is what 38% of the history recorded."""
    db = tmp_path / "stream_cache.db"
    _seed_two_expirations(db, "2026-08-17", "2026-08-18", greeks_on="2026-08-18")

    snap = provider.snapshot_from_stream_cache(db, "SPX", today="2026-08-19")

    assert snap.expiration is None
    assert not snap.chain_entries
