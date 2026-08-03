import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout import sse as _sse
from cherrypick.scout.app import create_app
from cherrypick.scout.services import watchlist as _watchlist
from cherrypick.scout.services.session import BrokerSession

PORT = 5057


class _FakeManager:
    def get_session(self):
        return "session"

    def reset_session(self):
        pass


def _poller(wl_path, interval=0.01):
    return _sse.QuotePoller(BrokerSession(manager=_FakeManager(), politeness_seconds=0), wl_path, interval)


@pytest.mark.asyncio
async def test_subscribe_starts_the_poll_loop_and_publishes_changes(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, ["AAPL"])

    calls = []

    async def fake_get_quotes(_session, symbols, **_kwargs):
        calls.append(list(symbols))
        return {"AAPL": {"last": 105.0}}

    monkeypatch.setattr(_sse.quote_service, "get_quotes", fake_get_quotes)

    poller = _poller(wl_path)
    queue = await poller.subscribe()
    try:
        changed = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert changed == {"AAPL": {"last": 105.0}}
        assert calls == [["AAPL"]]
    finally:
        await poller.unsubscribe(queue)
    assert poller.client_count == 0


@pytest.mark.asyncio
async def test_unchanged_quotes_are_not_republished(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, ["AAPL"])

    async def fake_get_quotes(_session, symbols, **_kwargs):
        return {"AAPL": {"last": 105.0}}  # identical on every tick

    monkeypatch.setattr(_sse.quote_service, "get_quotes", fake_get_quotes)

    poller = _poller(wl_path)
    queue = await poller.subscribe()
    try:
        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert first == {"AAPL": {"last": 105.0}}
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.2)
    finally:
        await poller.unsubscribe(queue)


@pytest.mark.asyncio
async def test_last_unsubscribe_cancels_the_poll_task(tmp_path):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, [])

    poller = _poller(wl_path)
    q1 = await poller.subscribe()
    q2 = await poller.subscribe()
    assert poller.client_count == 2

    await poller.unsubscribe(q1)
    assert poller.client_count == 1  # still one subscriber -- loop keeps running

    await poller.unsubscribe(q2)
    assert poller.client_count == 0


@pytest.mark.asyncio
async def test_an_empty_watchlist_never_calls_the_broker(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, [])

    async def fail_get_quotes(*_a, **_kw):
        raise AssertionError("must not be called with an empty watchlist")

    monkeypatch.setattr(_sse.quote_service, "get_quotes", fail_get_quotes)

    poller = _poller(wl_path)
    queue = await poller.subscribe()
    await asyncio.sleep(0.05)
    assert queue.empty()
    await poller.unsubscribe(queue)


@pytest.mark.asyncio
async def test_a_broken_broker_call_degrades_to_no_tick_rather_than_crashing(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, ["AAPL"])

    async def broken_get_quotes(*_a, **_kw):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(_sse.quote_service, "get_quotes", broken_get_quotes)

    poller = _poller(wl_path)
    queue = await poller.subscribe()
    await asyncio.sleep(0.05)
    assert queue.empty()  # no tick published, but the loop is still alive
    assert poller.client_count == 1
    await poller.unsubscribe(queue)


@pytest.mark.asyncio
async def test_stop_cancels_the_task_and_clears_every_subscriber(tmp_path):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, [])

    poller = _poller(wl_path)
    await poller.subscribe()
    await poller.subscribe()
    await poller.stop()
    assert poller.client_count == 0


# --------------------------------------------------------------------------- per-connection generator


class _FakeRequest:
    """A minimal stand-in for `Request`: `is_disconnected()` flips to True after `disconnect_after`
    calls, simulating the client closing the connection after N checks."""

    def __init__(self, disconnect_after: int):
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after


@pytest.mark.asyncio
async def test_events_yields_a_quotes_event_and_unsubscribes_on_disconnect(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, [])
    poller = _poller(wl_path)
    queue = await poller.subscribe()
    await queue.put({"AAPL": {"last": 105.0}})

    events = _sse._events(_FakeRequest(disconnect_after=1), poller, queue)
    first = await events.__anext__()
    assert first["event"] == "quotes"
    assert json.loads(first["data"]) == {"symbols": {"AAPL": {"last": 105.0}}}

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()
    assert poller.client_count == 0  # the generator's finally block unsubscribed on disconnect


@pytest.mark.asyncio
async def test_events_sends_a_heartbeat_when_the_queue_is_quiet(tmp_path, monkeypatch):
    wl_path = tmp_path / "watchlist.json"
    _watchlist.save(wl_path, [])
    poller = _poller(wl_path)
    queue = await poller.subscribe()

    monkeypatch.setattr(_sse, "HEARTBEAT_SECONDS", 0.01)
    events = _sse._events(_FakeRequest(disconnect_after=1), poller, queue)
    first = await events.__anext__()
    assert first["event"] == "heartbeat"
    await poller.unsubscribe(queue)


# --------------------------------------------------------------------------- route wiring


def test_stream_route_is_registered_and_gated_by_the_security_middleware(managed_home):
    """A real HTTP round trip against the actual app, without ever reading the (never-ending)
    stream body: a bad Host header is refused before the route -- and therefore the poller --
    is ever reached, which is the one part of `/api/stream` a normal `client.get()` can assert on
    without hanging on an intentionally infinite response."""
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)
    with TestClient(app) as client:
        resp = client.get("/api/stream", headers={"Host": "evil.example.com"})
        assert resp.status_code == 403
        assert app.state.quote_poller.client_count == 0
