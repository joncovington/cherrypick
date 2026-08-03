import sys
import types

import pytest

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
