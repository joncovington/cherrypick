"""The one broker session scout holds — REST/request-driven, consistent with the suite's "only the
streamer talks to the broker" rule being a *streaming-path* rule (earnings/meic already hold REST
sessions the same way this does).

One process-wide `cherrypick.core.auth.session.SessionManager` over the shared `cherrypick-broker`
keyring service, behind an `asyncio.Lock` so concurrent requests serialize onto one session rather
than racing session construction. tastytrade's SDK is async-native (``Session`` holds an
``httpx.AsyncClient``) so calls are awaited directly -- no `asyncio.to_thread` needed for those.
A 250 ms inter-call politeness spacing is enforced before every call, and a 401-shaped failure resets
the cached session and retries once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cherrypick.core.auth import CredentialStore, SessionManager
from cherrypick.core.auth.credentials import SHARED_SERVICE

T = TypeVar("T")

POLITENESS_SECONDS = 0.25


def _looks_like_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text or "invalid_grant" in text


class BrokerSession:
    """Wraps a lazy `SessionManager` with serialized access, politeness spacing, and one 401 retry."""

    def __init__(self, manager: SessionManager | None = None, politeness_seconds: float = POLITENESS_SECONDS):
        self._manager = manager or SessionManager(CredentialStore(SHARED_SERVICE))
        self._lock = asyncio.Lock()
        self._politeness = politeness_seconds
        self._last_call = 0.0

    async def _throttle(self) -> None:
        wait = self._politeness - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    def get_raw_session(self) -> Any:
        """The underlying tastytrade `Session`, unwrapped -- for callers that need to construct
        something like a `DXLinkStreamer` themselves (a streaming connection isn't a single
        awaited request/response, so it doesn't fit `call()`'s throttle-and-retry shape). Still
        raises `CredentialError` the same way `call()` would if credentials are missing."""
        return self._manager.get_session()

    async def call(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Run an async tastytrade SDK call against the shared session: ``await fn(session, *args,
        **kwargs)``. Retries once, after resetting the cached session, on a 401-shaped failure."""
        async with self._lock:
            await self._throttle()
            session = self._manager.get_session()
            try:
                return await fn(session, *args, **kwargs)
            except Exception as exc:
                if not _looks_like_auth_error(exc):
                    raise
                self._manager.reset_session()
                await self._throttle()
                session = self._manager.get_session()
                return await fn(session, *args, **kwargs)
