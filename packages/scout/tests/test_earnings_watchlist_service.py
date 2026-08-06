import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import earnings_watchlist_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _StubSession:
    """Not a real BrokerSession -- earnings_watchlist_service only ever threads it to fetch_fn."""


class _FakeWatchlist:
    def __init__(self, group_name, name, entries):
        self.group_name = group_name
        self.name = name
        self.watchlist_entries = entries


@pytest.mark.asyncio
async def test_get_earnings_watchlist_symbols_caches(conn):
    calls = {"n": 0}

    async def fetch(_session):
        calls["n"] += 1
        return ["AAPL", "MSFT"]

    first = await earnings_watchlist_service.get_earnings_watchlist_symbols(
        conn, _StubSession(), fetch_fn=fetch, now=0
    )
    second = await earnings_watchlist_service.get_earnings_watchlist_symbols(
        conn, _StubSession(), ttl=86400, fetch_fn=fetch, now=10
    )
    assert first == second == {"AAPL", "MSFT"}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_earnings_watchlist_symbols_is_empty_not_raising_on_a_fetch_failure(conn):
    async def broken(_session):
        raise RuntimeError("no session")

    result = await earnings_watchlist_service.get_earnings_watchlist_symbols(
        conn, _StubSession(), fetch_fn=broken, now=0
    )
    assert result == set()


@pytest.mark.asyncio
async def test_default_fetch_reads_all_earnings_not_tasty_earnings(monkeypatch):
    """Live-verified shape (2026-08-06): group "Earnings" has "All Earnings" (85 symbols, broad)
    and "tasty Earnings" (26, curated) -- this reads the broader one."""
    watchlists = [
        _FakeWatchlist("Earnings", "All Earnings", [{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        _FakeWatchlist("Earnings", "tasty Earnings", [{"symbol": "KHC"}]),
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

    result = await earnings_watchlist_service._default_fetch(_StubSessionWithCall())
    assert result == ["AAPL", "MSFT"]
