import asyncio
from datetime import UTC, datetime, timedelta

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
async def test_cold_cache_seeds_the_full_backfill_window_from_dxlink(conn, monkeypatch):
    calls = []

    async def fake_dxlink(_session, symbol, start):
        calls.append((symbol, start))
        return _bars(n=3)

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    now = 1_700_000_000 + 2 * 86400
    cfg = {"refresh": {"candles_ttl_seconds": 3600, "candles_backfill_days": 365}}
    result = await candle_service.get_candles(conn, object(), cfg, "aapl", now=now)

    assert len(calls) == 1
    symbol, start = calls[0]
    assert symbol == "AAPL"
    # The seed must request the whole configured window, not a few-week tail.
    expected_start = datetime.now(tz=UTC).date() - timedelta(days=365)
    assert start == expected_start
    assert result["ok"] is True
    assert len(result["bars"]) == 3


@pytest.mark.asyncio
async def test_a_warm_cache_within_ttl_never_touches_dxlink(conn, monkeypatch):
    dxlink_calls = []

    async def fake_dxlink(_session, symbol, start):
        dxlink_calls.append(symbol)
        return _bars(n=1, start_t=1000)

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1500.0)

    assert dxlink_calls == ["AAPL"]  # only the initial seed


@pytest.mark.asyncio
async def test_a_stale_cache_tops_up_incrementally_from_the_last_real_bar(conn, monkeypatch):
    calls = []

    async def fake_dxlink(_session, symbol, start):
        calls.append(start)
        if len(calls) == 1:
            return _bars(n=1, start_t=1000)
        return _bars(n=1, start_t=100000)

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0)
    result = await candle_service.get_candles(conn, object(), cfg, "AAPL", now=200000.0)

    assert len(calls) == 2
    # The top-up asks for the day after the last cached bar, never the whole window again.
    assert calls[1] == datetime.fromtimestamp(1000, tz=UTC).date() + timedelta(days=1)
    # Honest: this response IS the just-topped-up read, and the flag reflects the staleness check.
    assert result["stale"] is True
    assert 100000 in [b["t"] for b in result["bars"]]


@pytest.mark.asyncio
async def test_a_failed_dxlink_falls_back_to_snapshot_synthesis(conn, monkeypatch):
    async def fake_dxlink(*_a, **_kw):
        return None

    synth_calls = []

    async def fake_synth(_session, symbol):
        synth_calls.append(symbol)
        return [{"t": 500000, "o": 9, "h": 9, "l": 9, "c": 9, "v": None}]

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)
    monkeypatch.setattr(candle_service, "_synth_from_snapshot", fake_synth)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    result = await candle_service.get_candles(conn, object(), cfg, "AAPL", now=500000.0)

    assert synth_calls == ["AAPL"]
    assert any(b["c"] == 9 for b in result["bars"])


@pytest.mark.asyncio
async def test_a_failed_attempt_is_floored_and_not_retried_immediately(conn, monkeypatch):
    attempt_calls = []

    async def fake_dxlink(*_a, **_kw):
        attempt_calls.append(1)
        return None

    async def fake_synth(*_a, **_kw):
        return None

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)
    monkeypatch.setattr(candle_service, "_synth_from_snapshot", fake_synth)

    cfg = {"refresh": {"candles_ttl_seconds": 10}}
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=100000.0)  # attempts, fails
    await candle_service.get_candles(conn, object(), cfg, "AAPL", now=100005.0)  # within retry floor

    assert len(attempt_calls) == 1


@pytest.mark.asyncio
async def test_concurrent_requests_for_the_same_symbol_seed_only_once(conn, monkeypatch):
    calls = []

    async def fake_dxlink(_session, symbol, start):
        calls.append(symbol)
        await asyncio.sleep(0.02)
        return _bars(n=1, start_t=1000)

    monkeypatch.setattr(candle_service, "_dxlink_tail", fake_dxlink)

    cfg = {"refresh": {"candles_ttl_seconds": 3600}}
    await asyncio.gather(
        candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0),
        candle_service.get_candles(conn, object(), cfg, "AAPL", now=1000.0),
    )
    assert calls == ["AAPL"]  # the second request found the lock held and just read the seeded cache


class _FakeCandleEvent:
    def __init__(self, time, open_, high, low, close, volume=None):
        self.time = time
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


class _FakeStreamer:
    """Minimal stand-in for `DXLinkStreamer`: yields a fixed event queue, then blocks (simulating an
    idle feed) until the caller's idle timeout fires."""

    def __init__(self, events):
        self._events = list(events)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def subscribe_candle(self, _symbols, interval, start_time=None):
        pass

    async def unsubscribe_candle(self, _symbol, interval=None):
        pass

    async def get_event(self, _event_class):
        if self._events:
            return self._events.pop(0)
        await asyncio.sleep(10)  # simulate an idle feed -- the caller's own timeout cuts this short


@pytest.mark.asyncio
async def test_dxlink_tail_discards_a_zero_filled_placeholder_candle(monkeypatch):
    """DXLink pushes a zero-filled placeholder for the still-forming current-day candle before any
    real trade has printed. This is a regression test for a real bug caught in a live smoke test: the
    placeholder was accepted as a genuine bar, writing a spot of 0.0 into the cache and breaking every
    downstream strike-selection calculation that assumed a positive price."""
    monkeypatch.setattr(candle_service, "_DXLINK_IDLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(candle_service, "_DXLINK_HARD_TIMEOUT_SECONDS", 0.2)

    events = [
        _FakeCandleEvent(time=1_700_000_000_000, open_=0, high=0, low=0, close=0, volume=0),
        _FakeCandleEvent(time=1_700_086_400_000, open_=100.0, high=101.0, low=99.0, close=100.5, volume=1000),
    ]

    import sys
    import types

    fake_tastytrade = types.ModuleType("tastytrade")
    fake_tastytrade.DXLinkStreamer = lambda _session: _FakeStreamer(events)
    fake_dxfeed = types.ModuleType("tastytrade.dxfeed")
    fake_dxfeed.Candle = object
    monkeypatch.setitem(sys.modules, "tastytrade", fake_tastytrade)
    monkeypatch.setitem(sys.modules, "tastytrade.dxfeed", fake_dxfeed)

    class _FakeSession:
        def get_raw_session(self):
            return object()

    from datetime import date

    bars = await candle_service._dxlink_tail(_FakeSession(), "AAPL", date(2027, 1, 1))
    assert bars is not None
    assert len(bars) == 1
    assert bars[0]["c"] == 100.5
