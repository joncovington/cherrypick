"""The recorder's options prints (`gex_prints`, 2026-11-01): copied raw from the cache's tape."""

import json
import sqlite3

from cherrypick.core import streamcache, streamrequests

from cherrypick.gex import service, stream_request

EXPIRY = "2026-11-02"


def _print(symbol, idx, received_at, **over):
    row = {
        "symbol": symbol,
        "idx": idx,
        "event_ms": 1_793_637_000_000 + idx,
        "received_at": received_at,
        "price": 4.4,
        "size": 3,
        "bid": 4.1,
        "ask": 4.4,
        "aggressor": "BUY",
        "spread_leg": 0,
        "eth": 0,
        "valid_tick": 1,
        "kind": "NEW",
        "conditions": "I",
        "exchange": "C",
    }
    row.update(over)
    return row


def _cache(path, prints=()):
    conn = streamcache.connect(path)
    for k in (7600, 7605):
        for right in ("C", "P"):
            sym = f".SPXW261102{right}{k}"
            meta = {"streamer_symbol": sym, "strike_price": k, "option_type": right}
            conn.execute(
                "INSERT INTO stream_chain VALUES (?,?,?,?,0)", (sym, EXPIRY, "SPX", json.dumps(meta))
            )
    for p in prints:
        streamcache.write_time_and_sale(conn, p)
    conn.commit()
    return conn


def _cfg(tmp_path, *, enabled=True):
    return {
        "symbols": ["SPX"],
        "stream_cache_db": str(tmp_path / "stream_cache.db"),
        "history_db_path": str(tmp_path / "gex_history.db"),
        "prints": {"enabled": enabled},
    }


def _held(tmp_path):
    conn = sqlite3.connect(tmp_path / "gex_history.db")
    try:
        return conn.execute(
            "SELECT side, strike, idx, price, aggressor, kind FROM gex_prints ORDER BY strike, side, idx"
        ).fetchall()
    finally:
        conn.close()


def test_off_by_default_records_nothing(tmp_path):
    _cache(tmp_path / "stream_cache.db", [_print(".SPXW261102C7600", 1, 100.0)]).close()
    assert service.record_prints(_cfg(tmp_path, enabled=False)) == 0
    assert service.prints_settings({"symbols": ["SPX"]})["enabled"] is False


def test_prints_are_copied_raw_with_their_strike_and_right(tmp_path):
    _cache(
        tmp_path / "stream_cache.db",
        [
            _print(".SPXW261102C7600", 1, 100.0),
            _print(".SPXW261102P7605", 2, 101.0, aggressor="SELL", spread_leg=1),
        ],
    ).close()
    assert service.record_prints(_cfg(tmp_path)) == 2
    assert _held(tmp_path) == [
        (service.QUOTE_SIDES["call"], 7600.0, 1, 4.4, "BUY", "NEW"),
        (service.QUOTE_SIDES["put"], 7605.0, 2, 4.4, "SELL", "NEW"),
    ]


def test_a_second_copy_takes_only_what_is_new_and_a_correction_replaces(tmp_path):
    cache = _cache(tmp_path / "stream_cache.db", [_print(".SPXW261102C7600", 1, 100.0)])
    cfg = _cfg(tmp_path)
    service.record_prints(cfg)
    streamcache.write_time_and_sale(cache, _print(".SPXW261102C7600", 1, 200.0, price=4.3, kind="CORRECTION"))
    streamcache.write_time_and_sale(cache, _print(".SPXW261102C7605", 3, 201.0))
    cache.commit()
    cache.close()
    assert service.record_prints(cfg) == 2  # the correction and the new print, not the first again
    assert service.record_prints(cfg) == 2  # the overlap re-reads the last instant, upserted
    held = _held(tmp_path)
    assert len(held) == 2
    assert held[0][3:] == (4.3, "BUY", "CORRECTION")


def test_a_print_with_no_chain_row_is_skipped(tmp_path):
    _cache(tmp_path / "stream_cache.db", [_print(".SPXW261102C9999", 1, 100.0)]).close()
    assert service.record_prints(_cfg(tmp_path)) == 0


def test_a_symbol_the_recorder_does_not_record_is_never_printed():
    settings = service.prints_settings(
        {"symbols": ["SPX"], "prints": {"enabled": True, "symbols": ["NDX", "SPX"]}}
    )
    assert settings["symbols"] == ["SPX"]


def test_enabled_prints_are_declared_without_narrowing_the_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    stream_request.register({"symbols": ["SPX"], "prints": {"enabled": True}})
    assert streamrequests.union_window_events()["SPX"] == [*streamrequests.WINDOW_EVENTS, "TimeAndSale"]


def test_disabled_prints_declare_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    stream_request.register({"symbols": ["SPX"]})
    assert streamrequests.union_window_events()["SPX"] == list(streamrequests.WINDOW_EVENTS)
