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


class _FakeEarnings:
    expected_report_date = None
    time_of_day = None
    consensus_estimate = None
    actual_eps = None
    estimated = None


class _FakeMarketMetricInfo:
    """A minimal stand-in for `tastytrade.metrics.MarketMetricInfo` -- just the attributes
    `_serialize` actually reads."""

    def __init__(self, *, iv_rank="0.50", iv_30d=27.16, liquidity_rating=4):
        self.symbol = "AAPL"
        self.implied_volatility_index_rank = iv_rank
        self.implied_volatility_percentile = None
        self.liquidity_rating = liquidity_rating
        self.liquidity_rank = None
        self.beta = None
        self.price_earnings_ratio = None
        self.dividend_yield = None
        self.implied_volatility_30_day = iv_30d
        self.earnings = None
        self.updated_at = None


def test_serialize_normalizes_iv_30d_from_a_percentage_to_a_fraction():
    """Regression test for a real bug caught in a live smoke test: `implied_volatility_30_day`
    arrives from the SDK as a percentage-point number (27.16 meaning 27.16%), not a 0..1 fraction
    like `iv_rank`. Feeding the un-normalized value straight into Black-Scholes sigma inflated the
    expected move ~100x and silently failed every screener candidate."""
    info = _FakeMarketMetricInfo(iv_30d=27.16)
    result = metrics_service._serialize(info)
    assert result["iv_30d"] == pytest.approx(0.2716)


def test_serialize_leaves_iv_rank_as_is_since_the_sdk_already_returns_a_fraction():
    info = _FakeMarketMetricInfo(iv_rank="0.547191662")
    result = metrics_service._serialize(info)
    assert result["iv_rank"] == "0.547191662"


def test_serialize_iv_30d_is_none_when_the_sdk_has_nothing():
    info = _FakeMarketMetricInfo(iv_30d=None)
    result = metrics_service._serialize(info)
    assert result["iv_30d"] is None


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
