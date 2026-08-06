import pytest

from cherrypick.scout.services import cache as _cache


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


def test_symbol_meta_freshness_is_none_on_an_empty_table(conn):
    assert _cache.symbol_meta_freshness(conn) is None


def test_read_sector_map_on_an_empty_table_is_empty(conn):
    assert _cache.read_sector_map(conn) == {}


def test_write_then_read_sector_map_round_trips(conn):
    _cache.write_sector_map(conn, {"AAPL": "Technology", "XOM": "Energy"}, 100.0, "tastytrade")
    assert _cache.read_sector_map(conn) == {"AAPL": "Technology", "XOM": "Energy"}
    assert _cache.symbol_meta_freshness(conn) == 100.0


def test_write_sector_map_upserts_on_conflict(conn):
    _cache.write_sector_map(conn, {"AAPL": "Technology"}, 100.0, "tastytrade")
    _cache.write_sector_map(conn, {"AAPL": "Healthcare"}, 200.0, "tastytrade")
    assert _cache.read_sector_map(conn) == {"AAPL": "Healthcare"}
    assert _cache.symbol_meta_freshness(conn) == 200.0


def test_symbol_meta_freshness_is_the_table_wide_max(conn):
    # A bulk fetch writes every row with the same fetched_at, but the freshness check itself must
    # still tolerate independently-timestamped rows (e.g. a later partial write) without erroring.
    _cache.write_sector_map(conn, {"AAPL": "Technology"}, 100.0, "tastytrade")
    _cache.write_sector_map(conn, {"XOM": "Energy"}, 150.0, "tastytrade")
    assert _cache.symbol_meta_freshness(conn) == 150.0
    assert _cache.read_sector_map(conn) == {"AAPL": "Technology", "XOM": "Energy"}
