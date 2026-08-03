import asyncio

import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import candle_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _isolated_locks(monkeypatch):
    """Each test gets its own lock table -- the real module dict is process-global, and a test
    reusing a "AAPL:1d" key from an earlier test would (harmlessly, but confusingly) share a lock."""
    monkeypatch.setattr(candle_service, "_locks", {})


def _bars(*, start_t=1_700_000_000, n=3):
    return [
        {"t": start_t + i * 86400, "o": 1.0 + i, "h": 2.0 + i, "l": 0.5 + i, "c": 1.5 + i, "v": 100.0}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_cold_cache_seeds_from_dolt_once(conn, monkeypatch):
    calls = []

    async def fake_dolt(_cfg, symbol):
        calls.append(symbol)
        return _bars(n=3)

    async def fake_dxlink(*_a, **_kw):
        return None

    async def fake_synth(*_a, **_kw):
        return None

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)
    monkeypatch.setattr(candle_service, "_synth_from_snapshot", fake_synth)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    now = 1_700_000_000 + 2 * 86400  # right at the last Dolt bar -- not stale yet
    result = await candle_service.get_candles(conn, object(), cfg, "aapl", now=now)

    assert calls == ["AAPL"]
    assert result["ok"] is True
    assert result["symbol"] == "AAPL"
    assert len(result["bars"]) == 3
    assert result["stale"] is False


@pytest.mark.asyncio
async def test_a_warm_cache_within_ttl_never_touches_dolt_or_dxlink(conn, monkeypatch):
    dolt_calls, dxlink_calls = [], []

    async def fake_dolt(_cfg, symbol):
        dolt_calls.append(symbol)
        return _bars(n=1, start_t=1000)

    async def fake_dxlink(*_a, **_kw):
        dxlink_calls.append(1)
        return None

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1500.0)

    assert dolt_calls == ["AAPL"]  # only the initial seed
    assert dxlink_calls == []


@pytest.mark.asyncio
async def test_a_stale_cache_tops_up_via_dxlink(conn, monkeypatch):
    async def fake_dolt(_cfg, symbol):
        return _bars(n=1, start_t=1000)

    tail_calls = []

    async def fake_dxlink(_session, symbol, start):
        tail_calls.append((symbol, start))
        return _bars(n=1, start_t=100000)

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    result = await candle_service.get_candles(conn, object(), cfg, "AAPL", now=200000.0)

    assert len(tail_calls) == 1
    # Honest: this response IS the just-topped-up read, and the flag reflects the staleness check.
    assert result["stale"] is True
    bars_t = [b["t"] for b in result["bars"]]
    assert 100000 in bars_t


@pytest.mark.asyncio
async def test_a_failed_dxlink_falls_back_to_snapshot_synthesis(conn, monkeypatch):
    async def fake_dolt(_cfg, symbol):
        return _bars(n=1, start_t=1000)

    async def fake_dxlink(*_a, **_kw):
        return None

    synth_calls = []

    async def fake_synth(_session, symbol):
        synth_calls.append(symbol)
        return [{"t": 500000, "o": 9, "h": 9, "l": 9, "c": 9, "v": None}]

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)
    monkeypatch.setattr(candle_service, "_synth_from_snapshot", fake_synth)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    result = await candle_service.get_candles(conn, object(), cfg, "AAPL", now=500000.0)

    assert synth_calls == ["AAPL"]
    assert any(b["c"] == 9 for b in result["bars"])


@pytest.mark.asyncio
async def test_a_failed_attempt_is_floored_and_not_retried_immediately(conn, monkeypatch):
    async def fake_dolt(_cfg, symbol):
        return _bars(n=1, start_t=1000)

    attempt_calls = []

    async def fake_dxlink(*_a, **_kw):
        attempt_calls.append(1)
        return None

    async def fake_synth(*_a, **_kw):
        return None

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)
    monkeypatch.setattr(candle_service, "_synth_from_snapshot", fake_synth)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)  # seeds
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=100000.0)  # stale, attempts, fails
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=100005.0)  # still within retry floor

    assert len(attempt_calls) == 1


@pytest.mark.asyncio
async def test_dolt_unreachable_falls_through_to_dxlink(conn, monkeypatch):
    async def fake_dolt(_cfg, symbol):
        return None  # Dolt down

    async def fake_dxlink(_session, symbol, start):
        return _bars(n=1, start_t=42)

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    result = await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    assert any(b["t"] == 42 for b in result["bars"])


@pytest.mark.asyncio
async def test_concurrent_requests_for_the_same_symbol_seed_dolt_only_once(conn, monkeypatch):
    calls = []
    started = asyncio.Event()

    async def fake_dolt(_cfg, symbol):
        calls.append(symbol)
        started.set()
        await asyncio.sleep(0.02)
        return _bars(n=1, start_t=1000)

    async def fake_dxlink(*_a, **_kw):
        return None

    monkeypatch.setattr(candle_service, "_fetch_dolt_ohlcv", fake_dolt)
    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    await asyncio.gather(
        candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0),
        candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0),
    )
    assert calls == ["AAPL"]  # the second request found the lock held and just read the seeded cache
