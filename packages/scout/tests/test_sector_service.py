import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import sector_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _StubSession:
    """Not a real BrokerSession -- sector_service only ever threads it through to fetch_fn."""


class _FakeWatchlist:
    def __init__(self, group_name, name, entries):
        self.group_name = group_name
        self.name = name
        self.watchlist_entries = entries


def test_sector_buckets_cover_all_eleven_named_sectors():
    assert len(sector_service.SECTORS) == 11
    assert "Technology" in sector_service.SECTORS
    assert "Healthcare" in sector_service.SECTORS


@pytest.mark.asyncio
async def test_get_sector_map_filters_to_the_sectors_group_and_uppercases_symbols(conn):
    watchlists = [
        _FakeWatchlist("Sectors", "Technology", [{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        _FakeWatchlist("Sectors", "Energy", [{"symbol": "XOM"}]),
        _FakeWatchlist("tasty", "Market", [{"symbol": "SPY"}]),  # not a sector watchlist -- ignored
    ]

    async def fetch(_session):
        symbol_to_sector = {}
        for wl in watchlists:
            if wl.group_name != "Sectors" or not wl.watchlist_entries:
                continue
            for entry in wl.watchlist_entries:
                symbol_to_sector[entry["symbol"].upper()] = wl.name
        return symbol_to_sector

    result = await sector_service.get_sector_map(conn, _StubSession(), fetch_fn=fetch, now=0)
    assert result == {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}
    assert "SPY" not in result


@pytest.mark.asyncio
async def test_get_sector_map_is_cached_and_not_refetched_within_ttl(conn):
    calls = {"n": 0}

    async def fetch(_session):
        calls["n"] += 1
        return {"AAPL": "Technology"}

    first = await sector_service.get_sector_map(conn, _StubSession(), ttl=86400, fetch_fn=fetch, now=0)
    second = await sector_service.get_sector_map(conn, _StubSession(), ttl=86400, fetch_fn=fetch, now=10)
    assert first == second == {"AAPL": "Technology"}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_sector_map_refetches_after_ttl_expires(conn):
    calls = {"n": 0}

    async def fetch(_session):
        calls["n"] += 1
        return {"AAPL": "Technology"}

    await sector_service.get_sector_map(conn, _StubSession(), ttl=100, fetch_fn=fetch, now=0)
    await sector_service.get_sector_map(conn, _StubSession(), ttl=100, fetch_fn=fetch, now=200)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_get_sector_map_propagates_a_fetch_failure_with_no_cache_to_fall_back_on(conn):
    async def broken(_session):
        raise RuntimeError("no session")

    with pytest.raises(RuntimeError):
        await sector_service.get_sector_map(conn, _StubSession(), fetch_fn=broken, now=0)


@pytest.mark.asyncio
async def test_default_fetch_filters_to_sectors_group_and_uppercases_symbols(monkeypatch):
    """`_default_fetch` against a stand-in for `tastytrade.watchlists.PublicWatchlist.get` --
    live-verified shape (2026-08-05): `group_name == "Sectors"`, `watchlist_entries` is a list of
    `{"symbol": ..., "instrument-type": "Equity"}` dicts."""
    watchlists = [
        _FakeWatchlist("Sectors", "Technology", [{"symbol": "aapl", "instrument-type": "Equity"}]),
        _FakeWatchlist("tasty", "Market", [{"symbol": "SPY", "instrument-type": "Equity"}]),
        _FakeWatchlist("Sectors", "Empty Sector", None),  # no entries -- skipped, not an error
    ]

    class _FakePublicWatchlist:
        @staticmethod
        async def get(_session):
            return watchlists

    import sys
    import types

    fake_module = types.ModuleType("tastytrade.watchlists")
    fake_module.PublicWatchlist = _FakePublicWatchlist
    monkeypatch.setitem(sys.modules, "tastytrade.watchlists", fake_module)

    class _StubSessionWithCall:
        async def call(self, fn, *args):
            return await fn("fake-tastytrade-session", *args)

    result = await sector_service._default_fetch(_StubSessionWithCall())
    assert result == {"AAPL": "Technology"}


@pytest.mark.asyncio
async def test_get_sector_map_serves_stale_cache_on_a_later_fetch_failure(conn):
    async def ok(_session):
        return {"AAPL": "Technology"}

    async def broken(_session):
        raise RuntimeError("broker hiccup")

    await sector_service.get_sector_map(conn, _StubSession(), ttl=100, fetch_fn=ok, now=0)
    stale = await sector_service.get_sector_map(conn, _StubSession(), ttl=100, fetch_fn=broken, now=200)
    assert stale == {"AAPL": "Technology"}
