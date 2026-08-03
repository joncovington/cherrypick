import sqlite3

import pytest
from cherrypick.core import streamcache as _core_streamcache

from cherrypick.scout.services import streamcache as _streamcache


def _write_cache(path, *, trades=(), quotes=()):
    conn = _core_streamcache.connect(path)
    for symbol, last, change, updated_at in trades:
        conn.execute(
            "INSERT INTO stream_trades (symbol, last, change, volume, updated_at) VALUES (?, ?, ?, 0, ?)",
            (symbol, last, change, updated_at),
        )
    for symbol, bid, ask, mid, updated_at in quotes:
        conn.execute(
            "INSERT INTO stream_quotes (symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?)",
            (symbol, bid, ask, mid, updated_at),
        )
    conn.commit()
    return conn


def test_open_ro_returns_none_when_the_file_does_not_exist(tmp_path):
    assert _streamcache.open_ro(tmp_path / "no-such-file.db") is None


def test_open_ro_opens_an_existing_cache_read_only(tmp_path):
    path = tmp_path / "stream_cache.db"
    _core_streamcache.connect(path).close()
    conn = _streamcache.open_ro(path)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):  # read-only connection refuses a write
        conn.execute("INSERT INTO stream_status (id, pid) VALUES (1, 1)")
    conn.close()


def test_read_equity_quotes_combines_trade_and_quote_rows(tmp_path):
    path = tmp_path / "stream_cache.db"
    _write_cache(path, trades=[("AAPL", 105.0, 5.0, 1000.0)], quotes=[("AAPL", 104.9, 105.1, 105.0, 1000.0)])
    conn = _streamcache.open_ro(path)
    result = _streamcache.read_equity_quotes(conn, ["aapl"], max_age_seconds=60, now=1005.0)
    conn.close()
    assert result["AAPL"]["last"] == 105.0
    assert result["AAPL"]["bid"] == 104.9
    assert result["AAPL"]["change_pct"] == pytest.approx(0.05)  # change=5 on a prev_close of 100


def test_read_equity_quotes_drops_a_row_older_than_max_age(tmp_path):
    path = tmp_path / "stream_cache.db"
    _write_cache(path, trades=[("AAPL", 105.0, 5.0, 1000.0)])
    conn = _streamcache.open_ro(path)
    result = _streamcache.read_equity_quotes(conn, ["AAPL"], max_age_seconds=2, now=1005.0)
    conn.close()
    assert result == {}


def test_read_equity_quotes_ignores_symbols_with_no_row(tmp_path):
    path = tmp_path / "stream_cache.db"
    _write_cache(path, trades=[("AAPL", 105.0, 5.0, 1000.0)])
    conn = _streamcache.open_ro(path)
    result = _streamcache.read_equity_quotes(conn, ["AAPL", "MSFT"], max_age_seconds=60, now=1005.0)
    conn.close()
    assert set(result.keys()) == {"AAPL"}


def test_read_equity_quotes_on_empty_symbol_list_is_empty(tmp_path):
    path = tmp_path / "stream_cache.db"
    _core_streamcache.connect(path).close()
    conn = _streamcache.open_ro(path)
    assert _streamcache.read_equity_quotes(conn, [], max_age_seconds=60) == {}
    conn.close()
