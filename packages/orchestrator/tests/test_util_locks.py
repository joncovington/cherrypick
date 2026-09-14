"""The shared PID-lock helper (util.acquire_pid_lock) and atomic JSON writes.

The lock ports MEIC's once-lock semantics — the load-bearing property is that a held-but-ALIVE lock
is never stolen regardless of age (age-only staleness once let a slow settlement pass lose its lock
mid-run and two processes settled the same trades). The mtime fallback applies only when the
holder's PID is unreadable.
"""

from __future__ import annotations

import json
import os
import time

from cherrypick.core import looplock

from cherrypick.orchestrator import util


def test_acquire_writes_own_pid(tmp_path):
    lock = tmp_path / "x.lock"
    assert util.acquire_pid_lock(lock)
    assert int(lock.read_text()) == os.getpid()
    util.release_pid_lock(lock)
    assert not lock.exists()


def test_live_holder_is_never_stolen_regardless_of_age(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text(str(os.getpid()))  # a live process (this one) holds it
    # Far past the staleness window, but after this process started -- an mtime older than the
    # holder itself is the recycled-PID case, covered next.
    written = looplock.process_start_time(os.getpid()) + 0.5
    os.utime(lock, (written, written))
    time.sleep(1.5)
    assert not util.acquire_pid_lock(lock, stale_seconds=1)
    assert int(lock.read_text()) == os.getpid()  # untouched


def test_recycled_pid_holder_is_reclaimed(tmp_path):
    """A live PID whose process started after the lock was written is not the holder (2026-09-13:
    the supervisor's lock PID came back as NordVPN after a reboot and blocked every restart)."""
    lock = tmp_path / "x.lock"
    lock.write_text(str(os.getpid()))
    before_this_process = looplock.process_start_time(os.getpid()) - 3_600
    os.utime(lock, (before_this_process, before_this_process))
    assert util.acquire_pid_lock(lock, stale_seconds=86_400)
    assert int(lock.read_text()) == os.getpid()


def test_dead_holder_is_reclaimed(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("999999999")  # not a real PID
    assert util.acquire_pid_lock(lock)
    assert int(lock.read_text()) == os.getpid()


def test_unreadable_pid_falls_back_to_mtime(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("garbage")
    # fresh + unreadable: assume a holder mid-write, do not steal
    assert not util.acquire_pid_lock(lock, stale_seconds=300)
    # stale + unreadable: reclaim
    old = time.time() - 600
    os.utime(lock, (old, old))
    assert util.acquire_pid_lock(lock, stale_seconds=300)


def test_pid_alive_self_and_bogus():
    assert util.pid_alive(os.getpid())
    assert not util.pid_alive(999999999)
    assert not util.pid_alive(0)
    assert not util.pid_alive(None)


def test_atomic_write_json_round_trip_and_replace(tmp_path):
    p = tmp_path / "sub" / "state.json"
    util.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    util.atomic_write_json(p, {"a": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 2}
    assert not p.with_name(p.name + ".tmp").exists()
