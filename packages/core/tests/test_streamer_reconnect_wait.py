"""The wait before a reconnect: it doubles on each drop and starts over after a connection that lived.

On 2026-11-03 the wait went back to the base only when the stream ended cleanly, which it does only on
a stop, so it grew for the process's whole life: four pre-market broker restarts left the 09:34 ET one,
after nearly two healthy hours, waiting 32s. A connection killed by the subscription rate limit dies
seconds in, and that loop must still back off.
"""

from __future__ import annotations

import asyncio

from cherrypick.core import streamer as st
from cherrypick.core.streamer import ChainStreamer, reconnect_wait

CUT = RuntimeError("Fatal streamer error: Your subscription rate is too high")


def test_a_connection_that_lived_starts_the_wait_over():
    assert reconnect_wait(32.0, st._RECONNECT_HEALTHY_S, False) == st._RECONNECT_BASE
    assert reconnect_wait(60.0, 7000.0, False) == st._RECONNECT_BASE


def test_a_connection_that_died_quickly_or_never_came_up_keeps_the_doubling():
    assert reconnect_wait(16.0, st._RECONNECT_HEALTHY_S - 1, False) == 16.0
    assert reconnect_wait(16.0, None, False) == 16.0


def test_a_rate_cut_keeps_the_doubling_however_long_the_connection_lived():
    assert reconnect_wait(16.0, 7000.0, True) == 16.0


def _run(tmp_path, monkeypatch, script):
    """Drive `run_async` through `script`: one (seconds the connection lives, error or None) per
    connection; None connects and stops. Returns the waits it slept."""
    eng = ChainStreamer(session_factory=lambda: None, db_path=tmp_path / "c.db", symbols=["SPX"])
    now = [0.0]
    eng._clock = lambda: now[0]
    waits = []
    steps = list(script)

    async def fake_run_stream(state):
        lives, err = steps.pop(0)
        if lives is not None:
            state.connected_at = eng._clock()
            now[0] += lives
        if err is None:
            state.stop_event.set()
            return
        raise err

    async def fake_sleep(delay):
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr(eng, "_run_stream", fake_run_stream)
    monkeypatch.setattr("cherrypick.core.streamer.asyncio.sleep", fake_sleep)
    asyncio.run(eng.run_async())
    return waits


def test_the_loop_doubles_on_quick_deaths_and_starts_over_after_a_healthy_connection(tmp_path, monkeypatch):
    drop = RuntimeError("Service Restart")
    waits = _run(
        tmp_path,
        monkeypatch,
        [(10, drop), (10, drop), (None, drop), (10, drop), (7000, drop), (10, drop), (0, None)],
    )
    assert waits == [2.0, 4.0, 8.0, 16.0, 2.0, 4.0]


def test_the_loop_keeps_doubling_through_a_rate_cut_on_a_long_connection(tmp_path, monkeypatch):
    drop = RuntimeError("Service Restart")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [CUT])  # noqa: F821
    waits = _run(tmp_path, monkeypatch, [(10, drop), (7000, group), (0, None)])
    assert waits == [2.0, 4.0]


def test_a_connection_that_never_came_up_is_not_credited_with_the_one_before(tmp_path, monkeypatch):
    drop = RuntimeError("Service Restart")
    waits = _run(tmp_path, monkeypatch, [(7000, drop), (None, drop), (0, None)])
    assert waits == [2.0, 4.0], "the failed connect after a healthy one keeps doubling"


def test_the_wait_never_passes_the_ceiling(tmp_path, monkeypatch):
    drop = RuntimeError("x")
    waits = _run(tmp_path, monkeypatch, [(1, drop)] * 8 + [(0, None)])
    assert max(waits) == st._RECONNECT_MAX and waits[-1] == st._RECONNECT_MAX
