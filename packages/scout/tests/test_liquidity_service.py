import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import liquidity_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _StubSession:
    """Not a real BrokerSession -- liquidity_service only ever threads it through to fetch_fn."""


class _FakeWatchlist:
    def __init__(self, group_name, name, entries):
        self.group_name = group_name
        self.name = name
        self.watchlist_entries = entries


@pytest.mark.asyncio
async def test_get_liquid_symbols_uppercases_and_caches(conn):
    calls = {"n": 0}

    async def fetch(_session):
        calls["n"] += 1
        return ["AAPL", "MSFT"]  # fetch_fn's own responsibility to normalize, like _default_fetch does

    first = await liquidity_service.get_liquid_symbols(conn, _StubSession(), fetch_fn=fetch, now=0)
    second = await liquidity_service.get_liquid_symbols(
        conn, _StubSession(), ttl=86400, fetch_fn=fetch, now=10
    )
    assert first == second == {"AAPL", "MSFT"}
    assert calls["n"] == 1  # second call was a cache hit


@pytest.mark.asyncio
async def test_get_liquid_symbols_refetches_after_ttl_expires(conn):
    calls = {"n": 0}

    async def fetch(_session):
        calls["n"] += 1
        return ["AAPL"]

    await liquidity_service.get_liquid_symbols(conn, _StubSession(), ttl=100, fetch_fn=fetch, now=0)
    await liquidity_service.get_liquid_symbols(conn, _StubSession(), ttl=100, fetch_fn=fetch, now=200)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_get_liquid_symbols_is_empty_not_raising_on_a_fetch_failure_with_no_cache(conn):
    async def broken(_session):
        raise RuntimeError("no session")

    result = await liquidity_service.get_liquid_symbols(conn, _StubSession(), fetch_fn=broken, now=0)
    assert result == set()


@pytest.mark.asyncio
async def test_get_liquid_symbols_serves_stale_cache_on_a_later_fetch_failure(conn):
    async def ok(_session):
        return ["AAPL"]

    async def broken(_session):
        raise RuntimeError("broker hiccup")

    await liquidity_service.get_liquid_symbols(conn, _StubSession(), ttl=100, fetch_fn=ok, now=0)
    stale = await liquidity_service.get_liquid_symbols(
        conn, _StubSession(), ttl=100, fetch_fn=broken, now=200
    )
    assert stale == {"AAPL"}


@pytest.mark.asyncio
async def test_default_fetch_filters_to_the_liquidity_group_and_liquid_symbols_name(monkeypatch):
    """Live-verified shape (2026-08-06): group_name == "Liquidity" has three watchlists (High
    Options Volume, Liquid Symbols, Liquid ETFs) -- only "Liquid Symbols" is the one this reads."""
    watchlists = [
        _FakeWatchlist("Liquidity", "Liquid Symbols", [{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        _FakeWatchlist("Liquidity", "High Options Volume", [{"symbol": "SPY"}]),
        _FakeWatchlist("Liquidity", "Liquid ETFs", [{"symbol": "DIA"}]),
        _FakeWatchlist("tasty", "Market", [{"symbol": "IGNORED"}]),
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

    result = await liquidity_service._default_fetch(_StubSessionWithCall())
    assert result == ["AAPL", "MSFT"]
