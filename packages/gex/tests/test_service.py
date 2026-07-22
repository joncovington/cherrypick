"""End-to-end: seed a MEIC-style stream cache, then drive the read-only provider + service.

No streamer, no network — just a temp SQLite shaped like the real stream_cache.db.
"""

import json
import sqlite3
from datetime import date

from cherrypick.core import gex

import provider
import service

TODAY = date.today().isoformat()

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
    conn.execute("INSERT INTO stream_trades (symbol, last, volume, updated_at) VALUES ('SPX', 605.0, 0, 0)")
    for opt in _CHAIN:
        conn.execute(
            "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, updated_at)"
            " VALUES (?,?,?,?,0)",
            (opt["streamer_symbol"], TODAY, "SPX", json.dumps(opt)),
        )
        sym = opt["streamer_symbol"]
        gamma, iv = _GREEKS[sym]
        conn.execute("INSERT INTO stream_greeks (symbol, gamma, iv, updated_at) VALUES (?,?,?,0)", (sym, gamma, iv))
        conn.execute("INSERT INTO stream_oi (symbol, open_interest, updated_at) VALUES (?,?,0)", (sym, _OI[sym]))
        conn.execute("INSERT INTO stream_trades (symbol, last, volume, updated_at) VALUES (?,?,?,0)",
                     (sym, 0, _VOL[sym]))
    conn.commit()
    conn.close()


def _cfg(tmp_path):
    db = tmp_path / "stream_cache.db"
    _seed_cache(db)
    return {"stream_cache_db": db, "history_db_path": tmp_path / "gex_history.db", "symbols": ["SPX"],
            "serve": {"host": "127.0.0.1", "port": 5055, "refresh_seconds": 15}}


def test_provider_reads_chain_greeks_oi_volume(tmp_path):
    cfg = _cfg(tmp_path)
    snap = provider.snapshot_from_stream_cache(cfg["stream_cache_db"], "SPX")
    assert snap.spot == 605.0 and snap.expiration == TODAY
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
        {"strike": 90.0, "net": -10}, {"strike": 91.0, "net": 5},
        {"strike": 100.0, "net": -20}, {"strike": 101.0, "net": 20},
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
    assert vt["total_call_gex_vol"] == 120      # 30 + 90 (only positives)
    assert vt["total_put_gex_vol"] == 60        # abs(-50 + -10)
    assert vt["net_gex_vol"] == 60              # -20 + 80


def test_build_gex_payload_shape_and_oi_vs_volume(tmp_path):
    cfg = _cfg(tmp_path)
    out = service.build_gex(cfg, "SPX")
    assert out["ok"] is True
    assert out["symbol"] == "SPX" and out["expiration"] == TODAY
    assert {"series", "totals", "spot_history", "market_open_ts", "market_close_ts"} <= out.keys()
    s600 = next(s for s in out["series"] if s["strike"] == 600)
    # OI ("positioning") and volume ("flow") series are computed independently and diverge
    assert s600["net_gex"] != s600["net_gex_vol"]
    t = out["totals"]
    assert t["call_wall"] == 610 and t["put_wall"] == 600
    assert t["zero_gamma"] is not None
    # Volume rollups sit alongside the OI keys.
    for k in ("total_call_gex_vol", "total_put_gex_vol", "net_gex_vol",
              "zero_gamma_vol", "call_wall_vol", "put_wall_vol"):
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


def test_build_gex_reports_missing_cache(tmp_path):
    cfg = {"stream_cache_db": tmp_path / "nope.db", "history_db_path": tmp_path / "h.db",
           "symbols": ["SPX"], "serve": {}}
    out = service.build_gex(cfg, "SPX")
    assert out["ok"] is False and "not found" in out["error"]
