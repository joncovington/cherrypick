"""The paper loops' shared scaffolding: where a loop logs and beats, the one-loop-per-ledger lock, and
the two drivers the supervisor's `--once` and `--interval` run through.

A module's `paper_loop.py` keeps its own `_acquire_loop_lock`, `_release_loop_lock` and `_log` and hands
them to the drivers, so a test that replaces one of them still steers the loop; these tests do the
same."""

import json
import os

import pytest

from cherrypick.core import looplock, paperloop


class _Stop(BaseException):
    """Raised from a fake sleep to end a resident loop the way a kill would."""


@pytest.fixture
def loop(tmp_path):
    return paperloop.PaperLoop("demo", lambda: str(tmp_path / "data"))


# ----------------------------------------------------------------------------- the loop's identity


def test_the_lock_lives_beside_the_ledger(loop, tmp_path):
    assert loop.lock_path() == os.path.join(str(tmp_path / "data"), "paper_loop.lock")


def test_the_data_dir_is_resolved_on_every_use_not_at_import(tmp_path):
    where = {"dir": str(tmp_path / "a")}
    loop = paperloop.PaperLoop("demo", lambda: where["dir"])
    first = loop.lock_path()
    where["dir"] = str(tmp_path / "b")
    assert loop.lock_path() != first


def test_the_logger_and_log_file_are_named_for_the_module(loop):
    assert loop.logger.name == "demo_paper_loop"
    assert loop.log_file().name == "demo_paper.log"


def test_one_loop_holds_the_lock_and_another_cannot_take_it(loop):
    assert loop.acquire() is True
    try:
        assert loop.acquire() is False  # this very process is alive: never stolen, whatever the age
    finally:
        loop.release()
    assert loop.acquire() is True
    loop.release()


def test_a_module_can_hand_in_its_own_liveness_probe(loop):
    assert loop.acquire() is True
    try:
        # a dead holder's lock is taken over once it is past the stale age
        assert loop.acquire(0, alive=lambda pid: False) is True
    finally:
        loop.release()


def test_acquire_without_a_probe_reads_the_looplock_probe_at_call_time(loop, monkeypatch):
    assert loop.acquire() is True
    try:
        monkeypatch.setattr(looplock, "pid_alive", lambda pid: False)
        assert loop.acquire(0) is True
    finally:
        loop.release()


# ----------------------------------------------------------------------------- --once


def _locks(held=False):
    state = {"released": 0, "held": held}

    def acquire():
        return not state["held"]

    def release():
        state["released"] += 1

    return state, acquire, release


def test_once_prints_the_result_as_indented_json_and_releases(capsys):
    state, acquire, release = _locks()
    assert paperloop.run_once_locked(lambda: {"b": 1, "a": 2}, acquire=acquire, release=release) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"b": 1, "a": 2}
    assert out.startswith('{\n  "b"')  # indent=2, key order kept
    assert state["released"] == 1


def test_once_releases_even_when_the_tick_raises(capsys):
    state, acquire, release = _locks()

    def boom():
        raise RuntimeError("tick failed")

    with pytest.raises(RuntimeError):
        paperloop.run_once_locked(boom, acquire=acquire, release=release)
    assert state["released"] == 1


def test_once_does_not_run_or_release_when_the_lock_is_held(capsys):
    state, acquire, release = _locks(held=True)
    ran = []
    assert paperloop.run_once_locked(lambda: ran.append(1), acquire=acquire, release=release) == 0
    assert json.loads(capsys.readouterr().out) == paperloop.SKIPPED
    assert ran == [] and state["released"] == 0


# ----------------------------------------------------------------------------- --interval


def _resident(ticks_before_stop=3, *, held=False, fail_first=False, keep_going=None, **extra):
    state, acquire, release = _locks(held)
    state.update(ticks=0, logs=[], slept=[])

    def tick():
        state["ticks"] += 1
        if fail_first and state["ticks"] == 1:
            raise RuntimeError("boom")

    def sleep(seconds):
        state["slept"].append(seconds)
        if state["ticks"] >= ticks_before_stop:
            raise _Stop

    state["call"] = lambda: paperloop.run_resident(
        7,
        tick=tick,
        keep_going=keep_going or (lambda: True),
        acquire=acquire,
        release=release,
        log=state["logs"].append,
        cache_path="CACHE",
        sleep=sleep,
        **extra,
    )
    return state


def test_resident_ticks_each_interval_logs_its_start_and_releases_on_exit():
    state = _resident()
    with pytest.raises(_Stop):
        state["call"]()
    assert state["ticks"] == 3 and state["slept"] == [7, 7, 7]
    assert state["logs"] == ["loop starting, interval 7s, cache CACHE"]
    assert state["released"] == 1


def test_resident_ends_when_the_session_does_and_says_so(capsys):
    seen = iter([True, True, False])
    state = _resident(ticks_before_stop=99, keep_going=lambda: next(seen))
    assert state["call"]() == 0
    assert state["ticks"] == 2
    assert state["logs"] == ["loop starting, interval 7s, cache CACHE", "session closed"]
    assert state["released"] == 1


def test_a_failing_tick_costs_that_tick_and_not_the_session():
    state = _resident(fail_first=True)
    with pytest.raises(_Stop):
        state["call"]()
    assert state["ticks"] == 3
    assert "iteration error (continuing): RuntimeError: boom" in state["logs"]


def test_resident_runs_after_start_once_after_the_start_line():
    calls = []
    state = _resident(after_start=lambda: calls.append(1))
    with pytest.raises(_Stop):
        state["call"]()
    assert calls == [1]


def test_resident_quietly_declines_when_the_lock_is_held(capsys):
    state = _resident(held=True, on_lock_miss="another paper loop holds the lock — exiting")
    assert state["call"]() == 0
    assert json.loads(capsys.readouterr().out) == paperloop.SKIPPED
    assert state["ticks"] == 0 and state["released"] == 0
    assert state["logs"] == ["another paper loop holds the lock — exiting"]


def test_resident_with_no_miss_message_logs_nothing_when_declined(capsys):
    state = _resident(held=True)
    assert state["call"]() == 0
    assert state["logs"] == []
