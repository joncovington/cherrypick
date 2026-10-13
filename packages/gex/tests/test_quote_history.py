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
    conn = sqlite3.connect(cfg["history_db_path"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM gex_quote_history ORDER BY side, strike")]
    finally:
        conn.close()


def test_a_sample_keeps_the_nearest_twenty_strikes_each_side_on_both_rights(tmp_path):
    cfg = _cfg(tmp_path)
    assert service.record_quotes(cfg, now_et=IN_SESSION) == 80
    rows = _rows(cfg)
    puts = [r["strike"] for r in rows if r["side"] == "put"]
    assert puts[0] == 5905.0 and puts[-1] == 6100.0 and len(puts) == 40  # 20 below 6002, 20 at/above
    assert {r["expiration"] for r in rows} == {EXPIRY}
    assert all(r["delta"] is not None and r["mid"] == 1.1 for r in rows)


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
    monkeypatch.setattr(service._regime, "sample", lambda cfg: {"status": "ok", "usable": 0, "written": 0})
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
