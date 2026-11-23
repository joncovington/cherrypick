"""What every module's paper loop does the same way: where it logs and beats, the lock that keeps one
loop per ledger, and the two ways the supervisor runs it (`--once` ticks, or one resident `--interval`
loop until the close).

Each module's `paper_loop.py` keeps what is its own: the tick, the arguments, the ledger. The names
other code and tests reach for (`log_file`, `_log`, `_acquire_loop_lock`, ...) stay in each module as
one-line delegates, so a test that replaces one of them still steers the loop; the drivers below take
`acquire`, `release` and `log` as arguments for the same reason, and a module hands them its own
module-level names at call time.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

SKIPPED = {"ok": True, "skipped": "another paper loop is already running"}


class PaperLoop:
    """One module's loop identity: its log, heartbeat and single-instance lock.

    `data_dir` is a callable, resolved on every use and never at import, so a test that redirects the
    home or the ledger path takes effect (the flies lesson: a module-level constant captures the real
    home before any test can redirect it).
    """

    def __init__(self, module: str, data_dir: Callable[[], str]):
        self.module = module
        self._data_dir = data_dir
        self.logger = logging.getLogger(f"{module}_paper_loop")

    def stream_cache_path(self, config: dict) -> str:
        """The suite's canonical shared stream cache, read-only here: config first, then the managed
        home. Portable paths only."""
        from cherrypick.core import streamcache as _streamcache

        return str(_streamcache.cache_path(config))

    def log_file(self):
        return _home.logs_dir(self.module) / f"{self.module}_paper.log"

    def log(self, message: str) -> None:
        _logs.configure(self.logger, self.log_file())
        self.logger.info(message)

    def heartbeat_file(self):
        return _home.heartbeat_path(self.module)

    def beat(self) -> None:
        """Publish liveness: the loop reached the top of a tick. The supervisor restarts a resident
        job whose liveness goes quiet, and a loop that writes only event-driven log lines looks wedged
        in a week with no position. So liveness is published, at the TOP of the tick, before any gate:
        it means "the loop is turning over" and nothing about what the tick then decided. A failed
        write is swallowed; a missing file is not judged silent."""
        _home.beat(self.module)

    def data_dir(self) -> str:
        return self._data_dir()

    def lock_path(self) -> str:
        return os.path.join(self._data_dir(), "paper_loop.lock")

    def acquire(self, stale_seconds: int = 180, alive: Callable[[int], bool] | None = None) -> bool:
        """The single-instance guard shared by `--interval` and `--once`, so the supervised resident
        loop and an off-session or manual `--once` can never iterate one ledger at once. A live
        holder is never stolen, whatever its age (`looplock` holds the semantics). `alive` is the
        liveness probe a module passes so a test that patches its `_pid_alive` still steers the
        lock; without one, `looplock`'s own probe is read at call time."""
        return looplock.acquire(self.lock_path(), stale_seconds, alive=alive)

    def release(self) -> None:
        looplock.release(self.lock_path())


def run_once_locked(
    run: Callable[[], Any], *, acquire: Callable[[], bool], release: Callable[[], None]
) -> int:
    """`--once`: take the lock (or say another loop holds it and exit 0), print `run()` as JSON, release."""
    if not acquire():
        print(json.dumps(SKIPPED))
        return 0
    try:
        print(json.dumps(run(), indent=2, default=str))
        return 0
    finally:
        release()


def run_resident(
    interval: int,
    *,
    tick: Callable[[], Any],
    keep_going: Callable[[], bool],
    acquire: Callable[[], bool],
    release: Callable[[], None],
    log: Callable[[str], Any],
    cache_path: str,
    on_lock_miss: str | None = None,
    after_start: Callable[[], Any] | None = None,
    sleep: Callable[[float], Any] | None = None,
) -> int:
    """`--interval`: hold the lock, tick every `interval` seconds while `keep_going()`, release.

    A tick that raises costs that tick, never the session: it is logged and the loop continues.
    `on_lock_miss` is an extra log line for a loop that finds the lock held; `after_start` runs once
    after the "loop starting" line (a module's cadence note, its schema-drift warning).
    """
    if not acquire():
        if on_lock_miss:
            log(on_lock_miss)
        print(json.dumps(SKIPPED))
        return 0
    try:
        log(f"loop starting, interval {interval}s, cache {cache_path}")
        if after_start:
            after_start()
        while keep_going():
            try:
                tick()
            except Exception as exc:  # noqa: BLE001 — a transient failure costs one tick, not the session
                log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
            (sleep or time.sleep)(interval)
        log("session closed")
        return 0
    finally:
        release()
