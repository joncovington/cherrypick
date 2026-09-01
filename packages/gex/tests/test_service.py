"""End-to-end: seed a MEIC-style stream cache, then drive the read-only provider + service.

No streamer, no network — just a temp SQLite shaped like the real stream_cache.db.
"""

import json
import sqlite3
import time
from datetime import date, timedelta

from cherrypick.core import gex

from cherrypick.gex import provider, service

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
    assert service.record_regimes(cfg) == 1  # only SPX has a cached chain here
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM gex_regime_history").fetchone()
    conn.close()
    assert row["symbol"] == "SPX"
    assert row["net_gex"] is not None and row["net_gex_vol"] is not None
    assert row["call_wall"] == 610 and row["put_wall"] == 600
    assert row["spot"] is not None and row["expiration"]


def test_record_regimes_throttles_to_one_row_per_interval(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg) == 1
    # An immediate second call is inside the 5-minute throttle: no second row.
    assert service.record_regimes(cfg) == 0
    # But an explicit zero interval writes again (the cadence knob is the caller's).
    assert service.record_regimes(cfg, min_interval_s=0) == 1


def test_record_regimes_skips_symbols_without_chains(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_regimes(cfg, symbols=["QQQ"]) == 0


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
