import pytest

from cherrypick.scout.services import cache as _cache


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


def test_read_candles_on_an_empty_symbol_is_empty(conn):
    assert _cache.read_candles(conn, "AAPL", "1d") == []


def test_write_then_read_round_trips_sorted_by_time(conn):
    _cache.write_candles(
        conn,
        "AAPL",
        "1d",
        [
            {"t": 200, "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 100},
            {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 50},
        ],
    )
    bars = _cache.read_candles(conn, "AAPL", "1d")
    assert [b["t"] for b in bars] == [100, 200]
    assert bars[0] == {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 50}


def test_write_candles_upserts_on_conflict(conn):
    _cache.write_candles(conn, "AAPL", "1d", [{"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 50}])
    _cache.write_candles(conn, "AAPL", "1d", [{"t": 100, "o": 1, "h": 2.5, "l": 0.5, "c": 2.0, "v": 75}])
    bars = _cache.read_candles(conn, "AAPL", "1d")
    assert len(bars) == 1
    assert bars[0]["c"] == 2.0
    assert bars[0]["v"] == 75


def test_candles_are_scoped_by_symbol_and_period(conn):
    _cache.write_candles(conn, "AAPL", "1d", [{"t": 100, "o": 1, "h": 1, "l": 1, "c": 1, "v": None}])
    _cache.write_candles(conn, "MSFT", "1d", [{"t": 100, "o": 2, "h": 2, "l": 2, "c": 2, "v": None}])
    _cache.write_candles(conn, "AAPL", "1h", [{"t": 100, "o": 3, "h": 3, "l": 3, "c": 3, "v": None}])
    assert _cache.read_candles(conn, "AAPL", "1d")[0]["c"] == 1
    assert _cache.read_candles(conn, "MSFT", "1d")[0]["c"] == 2
    assert _cache.read_candles(conn, "AAPL", "1h")[0]["c"] == 3


def test_candle_meta_round_trips(conn):
    assert _cache.get_candle_meta(conn, "AAPL", "1d") is None
    _cache.set_candle_meta(conn, "AAPL", "1d", 12345.0)
    assert _cache.get_candle_meta(conn, "AAPL", "1d") == 12345.0
    _cache.set_candle_meta(conn, "AAPL", "1d", 99999.0)
    assert _cache.get_candle_meta(conn, "AAPL", "1d") == 99999.0
