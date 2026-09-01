"""The lock's ordering is the thing under test: liveness first, mtime only as a fallback."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import time

import pytest

from cherrypick.core import looplock


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "nested" / "loop.lock"


# --------------------------------------------------------------------------- pid_alive


def test_pid_alive_on_a_live_process(live_pid):
    assert looplock.pid_alive(live_pid) is True


def test_pid_alive_on_a_reaped_process(dead_pid):
    assert looplock.pid_alive(dead_pid) is False


def test_pid_alive_rejects_nonsense():
    assert looplock.pid_alive(0) is False
    assert looplock.pid_alive(-1) is False
    assert looplock.pid_alive(None) is False


def test_pid_alive_survives_a_missing_psutil(monkeypatch):
    """psutil is absent in this suite, so the fallback chain is the real code path."""
    import builtins

    real_import = builtins.__import__

    def no_psutil(name, *a, **kw):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_psutil)
    assert looplock.pid_alive(os.getpid()) is True


# --------------------------------------------------------------------------- acquire / release


def test_acquire_creates_missing_parent_directories(lock_path):
    assert looplock.acquire(lock_path) is True
    assert lock_path.exists()
    assert lock_path.read_text().strip() == str(os.getpid())


def test_second_acquire_is_refused_while_the_holder_lives(lock_path):
    assert looplock.acquire(lock_path) is True
    assert looplock.acquire(lock_path) is False


def test_release_lets_the_next_caller_in(lock_path):
    looplock.acquire(lock_path)
    looplock.release(lock_path)
    assert looplock.acquire(lock_path) is True


def test_release_of_an_absent_lock_never_raises(lock_path):
    looplock.release(lock_path)  # must not raise


def test_a_live_holder_is_never_stolen_however_old(lock_path, live_pid):
    """The whole reason this module exists.

    A slow-but-healthy holder keeps its lock regardless of age. The weak mtime-only design steals
    here, which puts two writers on one ledger -- the P&L-corruption failure MEIC recorded.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(live_pid))
    ancient = time.time() - 86_400
    os.utime(lock_path, (ancient, ancient))

    assert looplock.acquire(lock_path, stale_seconds=1) is False
    assert lock_path.read_text().strip() == str(live_pid)


def test_a_dead_holder_is_stolen_immediately_not_after_the_timeout(lock_path, dead_pid):
    """Liveness answers first, so a crashed holder does not wedge the loop for stale_seconds."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(dead_pid))
    os.utime(lock_path, None)  # fresh mtime: only the liveness check can free this

    assert looplock.acquire(lock_path, stale_seconds=86_400) is True
    assert lock_path.read_text().strip() == str(os.getpid())


def test_an_unreadable_holder_waits_out_the_mtime(lock_path):
    """A corrupt/truncated write has no PID to probe, so the mtime backstop is what applies."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not-a-pid")
    os.utime(lock_path, None)

    assert looplock.acquire(lock_path, stale_seconds=86_400) is False

    ancient = time.time() - 86_400
    os.utime(lock_path, (ancient, ancient))
    assert looplock.acquire(lock_path, stale_seconds=60) is True


def test_an_empty_lock_file_is_treated_as_unreadable(lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("")
    os.utime(lock_path, None)
    assert looplock.acquire(lock_path, stale_seconds=86_400) is False


# --------------------------------------------------------------------------- real-process fixtures


@pytest.fixture
def live_pid():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


@pytest.fixture
def dead_pid():
    """A genuinely reaped PID.

    The handle must be released explicitly: while Popen holds it, Windows keeps the PID resolvable
    and OpenProcess still succeeds on the exited process, so a naive fixture reports it as ALIVE.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    pid = proc.pid
    proc.__exit__(None, None, None)
    del proc
    gc.collect()
    return pid


def test_the_liveness_probe_is_injectable(lock_path):
    """Consumers pass their own `_pid_alive` so module-level monkeypatching still steers the lock.

    Without this the eight consumer copies would fold into core and every test that patches
    `<module>._pid_alive` would keep passing while no longer touching the code it believes it is
    steering -- a test that cannot fail is worse than no test.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("4242")
    os.utime(lock_path, None)

    assert looplock.acquire(lock_path, stale_seconds=86_400, alive=lambda _pid: True) is False
    assert looplock.acquire(lock_path, stale_seconds=86_400, alive=lambda _pid: False) is True
