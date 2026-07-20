"""Real-time GEX push server (design: docs/websocket-push-design.md).

A `websockets` server on serve.port+1 that runs one build->compare->broadcast
loop and pushes the same payload the HTTP `/api/gex` route returns — only when
the strike-window data changes, at most once per `push_min_interval_seconds`.
The HTTP server stays the fallback; this never replaces it.
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets

import service as _service
from config import default_symbol, push_min_interval, ws_port

log = logging.getLogger(__name__)


def broadcast_key(payload: dict) -> tuple:
    """Cheap change signature; excludes spot_history and timestamps."""
    if not payload.get("ok"):
        return ("err", payload.get("error"))
    series = tuple(
        (s.get("strike"), s.get("net_gex"), s.get("call_oi"), s.get("put_oi"),
         s.get("call_vol"), s.get("put_vol"))
        for s in payload.get("series", [])
    )
    return (payload.get("underlying_price"), payload.get("expiration"),
            payload.get("source"), series)


class GexPushServer:
    def __init__(self, cfg: dict, build=_service.build_gex) -> None:
        self._cfg = cfg
        self._build = build
        self._interval = push_min_interval(cfg)
        self.clients: dict[str, set] = {}
        self._last_key: dict[str, tuple | None] = {}
        self._last_json: dict[str, str] = {}

    def _default_symbol(self) -> str:
        return default_symbol(self._cfg)

    def _build_for(self, symbol: str) -> str | None:
        """Build, update last-key/json; return JSON to send if changed, else None."""
        try:
            payload = self._build(self._cfg, symbol)
        except Exception:
            log.exception("gex build failed for %s; skipping tick", symbol)
            return None
        key = broadcast_key(payload)
        if key == self._last_key.get(symbol):
            return None
        self._last_key[symbol] = key
        self._last_json[symbol] = json.dumps(payload)
        return self._last_json[symbol]

    async def _send(self, ws, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception:
            for group in self.clients.values():
                group.discard(ws)

    async def tick(self) -> None:
        for symbol in list(self.clients):
            group = self.clients.get(symbol)
            if not group:
                continue
            msg = self._build_for(symbol)
            if msg is None:
                continue
            for ws in list(group):
                await self._send(ws, msg)

    async def handle(self, ws) -> None:
        """One client: register under its symbol, snapshot, then follow symbol
        switches until it disconnects."""
        symbol = self._default_symbol()
        self.clients.setdefault(symbol, set()).add(ws)
        await self._snapshot(ws, symbol)
        try:
            async for raw in ws:
                try:
                    req = json.loads(raw)
                    new_sym = str(req["symbol"]).strip().upper()
                except (ValueError, KeyError, TypeError):
                    continue
                if new_sym == symbol:
                    continue
                self.clients.get(symbol, set()).discard(ws)
                symbol = new_sym
                self.clients.setdefault(symbol, set()).add(ws)
                await self._snapshot(ws, symbol)
        finally:
            self.clients.get(symbol, set()).discard(ws)

    async def _snapshot(self, ws, symbol: str) -> None:
        """Send the newcomer current state: rebuild first (refreshing the cache),
        falling back to the last cached payload only if nothing changed."""
        msg = self._build_for(symbol) or self._last_json.get(symbol)
        if msg is not None:
            await self._send(ws, msg)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.tick()

    async def _run(self, host: str) -> None:
        async with websockets.serve(self.handle, host, ws_port(self._cfg)):
            await self._loop()

    def start(self, host: str) -> None:
        """Run the push server (blocks); call on a dedicated thread."""
        asyncio.run(self._run(host))
