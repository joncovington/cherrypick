"""The --once concurrency lock: a stale-by-AGE lock used to be stolen unconditionally, so a
still-running-but-slow iteration (the 16:00 settlement pass, at scale) could have its lock taken
by the next --once while it was still writing -- two processes settling the same trades
concurrently. These tests pin the fix: a lock held by a live PID is never stolen regardless of
age, and one held by a dead/unreadable PID is."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import paper_loop  # noqa: E402


def _write_lock(path: Path, pid: int, age_seconds: float = 0.0):
    path.write_text(str(pid))
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))


def test_live_holder_never_stolen_even_when_old(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)
    _write_lock(lock, os.getpid(), age_seconds=999)  # our own PID: always alive, far past 180s

    assert paper_loop._acquire_once_lock() is False
    assert lock.read_text() == str(os.getpid())  # untouched


def test_dead_holder_is_stolen_regardless_of_age(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)
    monkeypatch.setattr(paper_loop, "_pid_alive", lambda pid: False)
    _write_lock(lock, pid=999999, age_seconds=1.0)  # fresh by age, but provably dead

    assert paper_loop._acquire_once_lock() is True
    assert lock.read_text() == str(os.getpid())


def test_unreadable_lock_falls_back_to_age(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)
    lock.write_text("not-a-pid")
    old = time.time() - 200
    os.utime(lock, (old, old))

    assert paper_loop._acquire_once_lock() is True  # no readable PID, but old enough by age


def test_unreadable_lock_not_stolen_when_fresh(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)
    lock.write_text("not-a-pid")

    assert paper_loop._acquire_once_lock() is False


def test_no_lock_acquires_cleanly(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)

    assert paper_loop._acquire_once_lock() is True
    assert lock.exists()
    paper_loop._release_once_lock()
    assert not lock.exists()


def test_heartbeat_refreshes_mtime_without_disturbing_holder(tmp_path, monkeypatch):
    lock = tmp_path / "paper_loop.once.lock"
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", lock)
    _write_lock(lock, os.getpid(), age_seconds=100)
    before = os.path.getmtime(lock)
    time.sleep(0.01)

    paper_loop._heartbeat_lock()

    assert os.path.getmtime(lock) > before
    assert lock.read_text() == str(os.getpid())  # PID content unchanged, only mtime touched


def test_heartbeat_is_a_noop_without_a_lock_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_loop, "_LOCK_FILE", tmp_path / "does-not-exist.lock")
    paper_loop._heartbeat_lock()  # must not raise
