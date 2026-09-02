"""A stop the process performs on itself, so the log records who asked.

A signal is the obvious mechanism and it does not work on this platform. `os.kill(pid, SIGTERM)` on
win32 is `TerminateProcess`: no handler runs, no `finally` runs. `run_daemon`'s own
"cherrypick-streamer stopped." line had never once been written in 39,000 lines of streamer log,
which is why an unexplained 2026-09-02 02:37 restart -- the one whose reconnect snapshot poisoned
curve's regime basis -- could not be attributed after the fact.

A file the engine polls records the stop from the inside.
"""

import asyncio
import json
import logging

from cherrypick.core.streamer import ChainStreamer, _State


def _engine(tmp_path, **kw):
    return ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["SPX"],
        stop_file=tmp_path / "streamer.stop",
        stop_poll_s=0.01,
        **kw,
    )


def _state(engine, tmp_path):
    from cherrypick.core import streamcache

    return _State(streamcache.connect(tmp_path / "cache.db"), ["SPX"])


def test_the_stop_file_stops_the_engine(tmp_path):
    engine = _engine(tmp_path)
    state = _state(engine, tmp_path)
    (tmp_path / "streamer.stop").write_text(json.dumps({"reason": "cherrypick-streamer --stop"}))

    asyncio.run(asyncio.wait_for(engine._watch_stop_file(state), timeout=5))

    assert state.stop_event.is_set()


def test_the_reason_reaches_the_log(tmp_path, caplog):
    """The point of the file over a signal: a signal carries no reason, so the log could say a
    process ended but never who ended it."""
    engine = _engine(tmp_path, logger=logging.getLogger("stopfile-test"))
    state = _state(engine, tmp_path)
    (tmp_path / "streamer.stop").write_text(json.dumps({"reason": "watchdog: stalled feed"}))

    with caplog.at_level(logging.INFO, logger="stopfile-test"):
        asyncio.run(asyncio.wait_for(engine._watch_stop_file(state), timeout=5))

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Stop requested" in logged
    assert "watchdog: stalled feed" in logged, "the request's reason is the whole point"


def test_no_stop_file_means_no_stop(tmp_path):
    """The watcher must not end the stream on its own. Nothing has been requested here."""
    engine = _engine(tmp_path)
    state = _state(engine, tmp_path)

    async def run_briefly():
        task = asyncio.create_task(engine._watch_stop_file(state))
        await asyncio.sleep(0.15)
        task.cancel()

    asyncio.run(run_briefly())
    assert not state.stop_event.is_set()


def test_an_unreadable_stop_file_never_kills_the_stream(tmp_path, caplog):
    """Telemetry rules apply to the stop check too: a directory where a file was expected is a
    warning, not the end of the producer."""
    engine = _engine(tmp_path, logger=logging.getLogger("stopfile-test2"))
    state = _state(engine, tmp_path)
    (tmp_path / "streamer.stop").mkdir()  # exists() is true, read_text() raises

    async def run_briefly():
        task = asyncio.create_task(engine._watch_stop_file(state))
        await asyncio.sleep(0.15)
        task.cancel()

    with caplog.at_level(logging.WARNING, logger="stopfile-test2"):
        asyncio.run(run_briefly())

    assert not state.stop_event.is_set()
    assert "Stop-file check failed" in " ".join(r.getMessage() for r in caplog.records)


def test_an_engine_without_a_stop_file_is_unaffected(tmp_path):
    """The parameter is opt-in. Every existing caller passes nothing and gets no watcher."""
    engine = ChainStreamer(session_factory=lambda: None, db_path=tmp_path / "cache.db", symbols=["SPX"])
    assert engine.stop_file is None
