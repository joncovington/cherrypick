import sys
import time
import types

import pytest
from cherrypick.core import streamcache as _core_streamcache

from cherrypick.scout.services import quote_service


class _FakeQuote:
    def __init__(self, symbol, last, prev_close, bid=None, ask=None, mid=None, mark=None):
        self.symbol = symbol
        self.last = last
        self.prev_close = prev_close
        self.bid = bid
        self.ask = ask
        self.mid = mid
        self.mark = mark


class _FakeBrokerSession:
    def __init__(self, quotes_by_symbol):
        self.calls = []
        self._quotes_by_symbol = quotes_by_symbol

    async def call(self, fn, **kwargs):
        self.calls.append(sorted(kwargs["equities"]))
        return await fn(object(), **kwargs)


def _install_fake_market_data(monkeypatch, quotes_by_symbol):
    async def fake_get_market_data_by_type(_session, equities):
        return [quotes_by_symbol[sym] for sym in equities if sym in quotes_by_symbol]

    fake_module = types.ModuleType("tastytrade.market_data")
    fake_module.get_market_data_by_type = fake_get_market_data_by_type
    monkeypatch.setitem(sys.modules, "tastytrade.market_data", fake_module)


@pytest.mark.asyncio
async def test_get_quotes_serializes_and_computes_change_pct(monkeypatch):
    quotes = {"AAPL": _FakeQuote("AAPL", 105.0, 100.0, bid=104.9, ask=105.1, mid=105.0, mark=105.0)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(session, ["aapl"])
    assert result["AAPL"]["last"] == 105.0
    assert result["AAPL"]["change_pct"] == pytest.approx(0.05)
    assert session.calls == [["AAPL"]]


@pytest.mark.asyncio
async def test_get_quotes_omits_change_pct_without_a_previous_close(monkeypatch):
    quotes = {"AAPL": _FakeQuote("AAPL", 105.0, None)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(session, ["AAPL"])
    assert result["AAPL"]["change_pct"] is None


@pytest.mark.asyncio
async def test_get_quotes_chunks_large_symbol_lists(monkeypatch):
    quotes = {f"S{i}": _FakeQuote(f"S{i}", 10.0, 9.0) for i in range(250)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(session, list(quotes.keys()))
    assert len(result) == 250
    assert len(session.calls) == 3  # 100 + 100 + 50


@pytest.mark.asyncio
async def test_get_quotes_on_empty_list_is_empty():
    class _FakeSession:
        async def call(self, *_a, **_kw):
            raise AssertionError("should never be called for an empty symbol list")

    assert await quote_service.get_quotes(_FakeSession(), []) == {}


@pytest.mark.asyncio
async def test_get_quotes_drops_a_chunk_that_errors(monkeypatch):
    async def fake_get_market_data_by_type(_session, equities):
        raise RuntimeError("rate limited")

    fake_module = types.ModuleType("tastytrade.market_data")
    fake_module.get_market_data_by_type = fake_get_market_data_by_type
    monkeypatch.setitem(sys.modules, "tastytrade.market_data", fake_module)

    class _FakeSession:
        async def call(self, fn, **kwargs):
            return await fn(object(), **kwargs)

    result = await quote_service.get_quotes(_FakeSession(), ["AAPL"])
    assert result == {}


# --------------------------------------------------------------------------- streamer-before-API


def _stream_cache(tmp_path, *, trades=(), quotes=()):
    conn = _core_streamcache.connect(tmp_path / "stream_cache.db")
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


@pytest.mark.asyncio
async def test_get_quotes_prefers_a_fresh_stream_cache_row_over_the_broker(tmp_path, monkeypatch):
    conn = _stream_cache(tmp_path, trades=[("AAPL", 105.0, 5.0, time.time())])

    class _FailingSession:
        async def call(self, *_a, **_kw):
            raise AssertionError("must not reach the broker when the stream cache is fresh")

    result = await quote_service.get_quotes(
        _FailingSession(), ["AAPL"], stream_cache_conn=conn, stream_cache_max_age_seconds=60
    )
    conn.close()
    assert result["AAPL"]["last"] == 105.0


@pytest.mark.asyncio
async def test_get_quotes_falls_back_to_the_broker_for_symbols_missing_from_the_stream_cache(
    tmp_path, monkeypatch
):
    conn = _stream_cache(tmp_path, trades=[("AAPL", 105.0, 5.0, time.time())])
    quotes = {"MSFT": _FakeQuote("MSFT", 300.0, 295.0)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(
        session, ["AAPL", "MSFT"], stream_cache_conn=conn, stream_cache_max_age_seconds=60
    )
    conn.close()
    assert result["AAPL"]["last"] == 105.0  # from the stream cache
    assert result["MSFT"]["last"] == 300.0  # from the broker fallback
    assert session.calls == [["MSFT"]]  # never re-requested the symbol the cache already covered


@pytest.mark.asyncio
async def test_get_quotes_falls_back_when_the_stream_cache_row_is_stale(tmp_path, monkeypatch):
    conn = _stream_cache(tmp_path, trades=[("AAPL", 105.0, 5.0, 1000.0)])  # far in the past
    quotes = {"AAPL": _FakeQuote("AAPL", 106.0, 100.0)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(
        session, ["AAPL"], stream_cache_conn=conn, stream_cache_max_age_seconds=10
    )
    conn.close()
    assert result["AAPL"]["last"] == 106.0  # the fresher broker read, not the ancient cache row
    assert session.calls == [["AAPL"]]


@pytest.mark.asyncio
async def test_get_quotes_with_no_stream_cache_falls_back_to_the_broker_entirely(monkeypatch):
    quotes = {"AAPL": _FakeQuote("AAPL", 105.0, 100.0)}
    _install_fake_market_data(monkeypatch, quotes)
    session = _FakeBrokerSession(quotes)

    result = await quote_service.get_quotes(session, ["AAPL"], stream_cache_conn=None)
    assert result["AAPL"]["last"] == 105.0
    assert session.calls == [["AAPL"]]
