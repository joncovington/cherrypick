import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import metrics_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _StubSession:
    """Not a real BrokerSession -- metrics_service only ever threads it through to fetch_batch_fn."""


@pytest.mark.asyncio
async def test_a_cold_cache_calls_the_batched_fetch_once_for_every_symbol(conn):
    calls = []

    async def fetch_batch(_session, symbols):
        calls.append(list(symbols))
        return {s: {"symbol": s, "iv_rank": "50"} for s in symbols}

    result = await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl", "msft"], ttl=900, fetch_batch_fn=fetch_batch, now=1000.0
    )
    assert result == {
        "AAPL": {"symbol": "AAPL", "iv_rank": "50"},
        "MSFT": {"symbol": "MSFT", "iv_rank": "50"},
    }
    assert calls == [["AAPL", "MSFT"]]


@pytest.mark.asyncio
async def test_a_warm_cache_within_ttl_skips_the_fetch_entirely(conn):
    calls = []

    async def fetch_batch(_session, symbols):
        calls.append(list(symbols))
        return {s: {"symbol": s} for s in symbols}

    await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl"], ttl=900, fetch_batch_fn=fetch_batch, now=1000.0
    )
    await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl"], ttl=900, fetch_batch_fn=fetch_batch, now=1100.0
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_only_stale_symbols_are_refetched_not_the_whole_watchlist(conn):
    calls = []

    async def fetch_batch(_session, symbols):
        calls.append(sorted(symbols))
        return {s: {"symbol": s} for s in symbols}

    await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl", "msft"], ttl=100, fetch_batch_fn=fetch_batch, now=1000.0
    )
    # aapl and msft cached at t=1000; nvda is new. At t=1050 only nvda is stale/missing.
    await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl", "msft", "nvda"], ttl=100, fetch_batch_fn=fetch_batch, now=1050.0
    )
    assert calls == [["AAPL", "MSFT"], ["NVDA"]]


@pytest.mark.asyncio
async def test_a_fetch_failure_leaves_the_symbol_absent_not_an_error(conn):
    async def fetch_batch(_session, _symbols):
        raise RuntimeError("no credentials")

    result = await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl"], ttl=900, fetch_batch_fn=fetch_batch, now=1000.0
    )
    assert result == {}


@pytest.mark.asyncio
async def test_a_symbol_missing_from_the_batch_response_is_simply_absent(conn):
    async def fetch_batch(_session, symbols):
        return {}  # e.g. the broker had nothing for this symbol

    result = await metrics_service.get_metrics(
        conn, _StubSession(), ["aapl"], ttl=900, fetch_batch_fn=fetch_batch, now=1000.0
    )
    assert result == {}


@pytest.mark.asyncio
async def test_empty_symbol_list_short_circuits_without_calling_fetch(conn):
    calls = []

    async def fetch_batch(_session, symbols):
        calls.append(symbols)
        return {}

    result = await metrics_service.get_metrics(
        conn, _StubSession(), [], ttl=900, fetch_batch_fn=fetch_batch, now=1000.0
    )
    assert result == {}
    assert calls == []
