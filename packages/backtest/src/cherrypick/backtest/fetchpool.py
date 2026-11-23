"""The worker loop every vendor fetch shares: `workers` requests in flight on the account's one
session, the next one started as each finishes, the store written on the calling thread only.

A fetch hands this its requests, how to start one (`submit`) and what to do with one that finished
(`handle`); the loop owns the starting, the waiting and the stopping. What a result means (kept,
recorded as a refusal, counted) stays with the fetch."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

#: What `handle` returns for a request it has dealt with: carry on.
OK = "ok"
#: A fatal refusal: start nothing new, but let the requests already in flight finish and be handled.
DRAIN = "drain"
#: A fatal refusal: cancel what has not started, drop what is in flight unhandled, and end the loop.
ABORT = "abort"


def run_pool(
    keys: Iterable[Any],
    submit: Callable[[ThreadPoolExecutor, Any], Future],
    handle: Callable[[Any, Future], str | None],
    *,
    workers: int = 1,
    stop: Callable[[], bool] | None = None,
    after_batch: Callable[[], None] | None = None,
) -> bool:
    """Run every key of `keys` through `submit(executor, key)`, at most `workers` at a time, calling
    `handle(key, future)` on this thread as each finishes (it reads `future.result()` itself, so an
    error it does not catch ends the run). `handle` returns `OK` (or None), `DRAIN` or `ABORT`.

    `stop()`, when given, is asked before each new request: true starts nothing more, lets those in
    flight finish and be handled, and makes the return value true. `after_batch()` runs after each
    group of finished requests, and once more as an `ABORT` ends the loop. Returns whether `stop`
    ended it."""
    pending = iter(keys)
    stopped = False
    draining = False
    with ThreadPoolExecutor(max(1, workers)) as ex:
        running: dict[Future, Any] = {}

        def submit_next() -> bool:
            nonlocal stopped
            if stop is not None and stop():
                stopped = True
                return False
            key = next(pending, None)
            if key is None:
                return False
            running[submit(ex, key)] = key
            return True

        for _ in range(max(1, workers)):
            if not submit_next():
                break
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in finished:
                outcome = handle(running.pop(fut), fut)
                if outcome == ABORT:
                    for other in running:
                        other.cancel()
                    if after_batch is not None:
                        after_batch()
                    return stopped
                if outcome == DRAIN:
                    draining = True
                    continue
                if not draining:
                    submit_next()
            if after_batch is not None:
                after_batch()
    return stopped
