"""The shared fetch worker loop: how many run at once, what `stop` ends, and what a fatal refusal
does under `DRAIN` (finish what is in flight, start nothing) and `ABORT` (drop what is in flight)."""

from __future__ import annotations

import threading

import pytest

from cherrypick.backtest.fetchpool import ABORT, DRAIN, OK, run_pool


def _echo(ex, key):
    return ex.submit(lambda: key)


def test_every_key_is_handled_once_on_the_calling_thread():
    seen, threads = [], set()

    def handle(key, fut):
        seen.append((key, fut.result()))
        threads.add(threading.current_thread())
        return OK

    assert run_pool(range(7), _echo, handle, workers=3) is False
    assert sorted(seen) == [(k, k) for k in range(7)]
    assert threads == {threading.current_thread()}


def test_no_more_than_workers_run_at_once():
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}
    gate = threading.Event()

    def work(key):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        gate.wait(0.05)
        with lock:
            live["now"] -= 1
        return key

    run_pool(range(8), lambda ex, key: ex.submit(work, key), lambda k, f: OK, workers=2)
    assert live["peak"] <= 2


def test_stop_starts_nothing_more_but_handles_what_is_in_flight():
    handled, asked = [], []

    def stop():
        asked.append(1)
        return len(handled) >= 2  # after two are handled, start no more

    def handle(key, fut):
        handled.append(key)
        return OK

    assert run_pool(range(10), _echo, handle, workers=1, stop=stop) is True
    assert handled == [0, 1]  # one at a time: the third was never started


def test_drain_lets_the_others_finish_and_starts_nothing_new():
    started, handled = [], []
    fatal_seen = threading.Event()

    def submit(ex, key):
        started.append(key)
        # the first finishes at once; the others wait until its fatal result has been handled
        return ex.submit(lambda: key if key == 0 else fatal_seen.wait(5) and key)

    def handle(key, fut):
        handled.append(key)
        if key == 0:
            fatal_seen.set()
            return DRAIN
        return OK

    run_pool(range(10), submit, handle, workers=3)
    assert started == [0, 1, 2]  # the three first started; nothing after the fatal one
    assert sorted(handled) == [0, 1, 2]  # the other two were still handled


def test_abort_cancels_and_drops_what_is_in_flight_and_runs_after_batch_once():
    started, handled, batches = [], [], []
    release = threading.Event()

    def submit(ex, key):
        started.append(key)
        return ex.submit(lambda: key if key == 0 else release.wait(5) and key)

    def handle(key, fut):
        handled.append(key)
        release.set()  # let the dropped one end, so the pool can close
        return ABORT

    run_pool(range(10), submit, handle, workers=2, after_batch=lambda: batches.append(1))
    assert handled == [0]  # the other in-flight one was dropped unhandled
    assert started == [0, 1]
    assert batches == [1]


def test_an_error_the_handler_does_not_catch_ends_the_run():
    def submit(ex, key):
        return ex.submit(lambda: 1 / 0)

    def handle(key, fut):
        fut.result()
        return OK

    with pytest.raises(ZeroDivisionError):
        run_pool(range(3), submit, handle, workers=1)
