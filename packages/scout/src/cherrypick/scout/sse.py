"""``GET /api/stream`` -- Server-Sent Events for live watchlist quote ticks.

`QuotePoller` runs one shared background task per app lifespan, but that task only exists **while at
least one SSE client is connected**: the first `subscribe()` starts it, the last `unsubscribe()`
cancels it. Zero clients means zero broker calls -- this is poll-and-push REST (one batched
`quote_service.get_quotes` call per tick), deliberately not a resident DXLink connection, matching
the plan's "streamer comes before API calls, whenever practical" posture: this whole surface only
exists while a human actually has the page open.

Every tick diffs against the last-sent snapshot and pushes only symbols whose quote actually changed,
so a quiet market doesn't spam idle browser tabs. A heartbeat comment keeps the connection alive
through proxies/browsers during a quiet tick.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from .services import quote_service
from .services import watchlist as _watchlist
from .services.session import BrokerSession

HEARTBEAT_SECONDS = 15.0

router = APIRouter()


class QuotePoller:
    """Fan-out to N connected SSE clients from one shared polling loop, started/stopped by
    subscriber count rather than app lifespan -- see the module docstring."""

    def __init__(self, broker_session: BrokerSession, watchlist_path: Path, interval: float = 5.0):
        self._broker_session = broker_session
        self._watchlist_path = watchlist_path
        self._interval = interval
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last: dict[str, dict] = {}

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
            if self._task is None:
                self._last = {}
                self._task = asyncio.create_task(self._run())
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            if not self._subscribers and self._task is not None:
                self._task.cancel()
                self._task = None

    async def stop(self) -> None:
        async with self._lock:
            self._subscribers.clear()
            if self._task is not None:
                self._task.cancel()
                self._task = None

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    async def _run(self) -> None:
        try:
            while True:
                symbols = _watchlist.load(self._watchlist_path)
                if symbols:
                    try:
                        quotes = await quote_service.get_quotes(self._broker_session, symbols)
                    except Exception:
                        quotes = {}
                    changed = {s: q for s, q in quotes.items() if self._last.get(s) != q}
                    if changed:
                        self._last.update(changed)
                        self._publish(changed)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    def _publish(self, changed: dict[str, dict]) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(changed)


async def _events(request: Request, poller: QuotePoller, queue: asyncio.Queue):
    """The per-connection event generator, factored out of the route so it can be driven directly
    in a test with a fake `request`/`queue` -- exercising it through a real streaming HTTP round
    trip would mean asserting on an intentionally never-ending response body."""
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                changed = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                yield {"event": "quotes", "data": json.dumps({"symbols": changed})}
            except TimeoutError:
                yield {"event": "heartbeat", "data": str(time.time())}
    finally:
        await poller.unsubscribe(queue)


@router.get("/api/stream")
async def stream(request: Request):
    poller: QuotePoller = request.app.state.quote_poller
    queue = await poller.subscribe()
    return EventSourceResponse(_events(request, poller, queue))
