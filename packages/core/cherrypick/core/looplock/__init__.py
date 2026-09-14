"""Single-instance guards for the suite's loops: is a process alive, and who holds the lock.

Every module that runs a loop needs the same two answers, and before this each one carried its own
copy of both. That is how the "settled probe chain" below came to have FIVE distinct implementations
across eight copies, each docstring confidently describing a chain its own code did not run.

**The lock semantics here are the strong ones, and the distinction is load-bearing.** A held-but-
ALIVE lock is never stolen regardless of age; the mtime fallback applies only when the holder is dead
or its PID cannot be read. The weaker mtime-only design steals from a slow-but-healthy holder, which
is how two writers end up on one ledger — MEIC learned that as a P&L-corruption incident, and
`acquire`'s ordering is the lesson. A caller whose work can legitimately run long (earnings' entry
scan holds for ~25 minutes) is safe here without tuning `stale_seconds` at all, because liveness
answers first.

**A live PID is not proof the holder is alive — it can be a REUSED PID.** After a reboot every
holder is dead, and the OS hands its number to whatever starts next: on 2026-09-13 the supervisor's
lock pid (23372) came back as NordVPN.exe, `pid_alive` said True, and every restart probe for the
rest of the evening was refused with "already running" while nothing was running at all. The
liveness answer therefore has a second half: a holder whose process was CREATED after the lock file
was written cannot be the process that wrote it, and is treated as dead. `process_start_time` is
that probe; where it cannot answer, the strong (never-steal) behaviour stands.
"""

from __future__ import annotations

import os
import time

__all__ = ["DEFAULT_STALE_SECONDS", "acquire", "pid_alive", "pid_reused", "process_start_time", "release"]

# Only consulted when the holder is dead or unreadable, so this is a corrupt-lock backstop rather
# than a work-duration budget. It does not need to exceed how long a healthy holder may run.
DEFAULT_STALE_SECONDS = 180

# A real holder writes the lock AFTER it starts, so its creation time is strictly earlier than the
# file's mtime. A reused PID's process started hours (a reboot) or at least seconds later. The
# tolerance only absorbs clock granularity between the two timestamps.
PID_REUSE_TOLERANCE_SECONDS = 2.0

_FILETIME_EPOCH_DELTA_SECONDS = 11644473600  # 1601-01-01 -> 1970-01-01


def pid_alive(pid: int | None) -> bool:
    """Is `pid` a live process?

    psutil first when present, then the Win32 OpenProcess probe, then `os.kill(pid, 0)` last —
    never bare os.kill first, which is unreliable on Windows (it raises SystemError for some
    process states, and a spurious "dead" verdict here means a live holder's lock gets stolen).

    **psutil is not a declared dependency of any package in this suite and is normally absent**, so
    in practice the Win32/os.kill branches are what run. It is kept as a preference rather than
    removed because it is the most accurate probe if an environment does provide it — but do not
    read the chain as evidence that psutil is installed. That misreading is exactly what let eight
    copies of this function drift apart unnoticed.
    """
    if not pid or pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        if os.name == "nt":
            import ctypes

            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except PermissionError:
        # The process exists; we simply may not signal it.
        return True
    except (OSError, SystemError, ValueError):
        return False


def process_start_time(pid: int | None) -> float | None:
    """Unix-epoch creation time of process `pid`, or None when it cannot be determined.

    psutil when present, then Win32 GetProcessTimes, then Linux /proc. None (never a guess) for a
    dead PID, a permission failure, or a platform with no probe — the caller keeps the strong
    never-steal behaviour in that case.
    """
    if not pid or pid <= 0:
        return None
    try:
        import psutil  # type: ignore

        try:
            return float(psutil.Process(pid).create_time())
        except Exception:
            return None
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32
            # Explicit signatures: the default c_int return truncates a 64-bit HANDLE.
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.GetProcessTimes.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return None
            try:
                created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
                ok = k32.GetProcessTimes(
                    handle,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
                if not ok:
                    return None
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                return ticks / 1e7 - _FILETIME_EPOCH_DELTA_SECONDS
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
        # Fields after the parenthesised comm start at field 3 (state); starttime is field 22.
        start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="utf-8") as fh:
            btime = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def pid_reused(pid: int | None, lock_mtime: float) -> bool:
    """True when `pid` provably belongs to a process created AFTER the lock was written — i.e. the
    number was recycled (typically across a reboot) and the holder that wrote the lock is gone.
    False whenever the creation time cannot be determined: an unknown is never grounds to steal."""
    started = process_start_time(pid)
    if started is None:
        return False
    return started - lock_mtime > PID_REUSE_TOLERANCE_SECONDS


def acquire(path, stale_seconds: int = DEFAULT_STALE_SECONDS, *, alive=None) -> bool:
    """O_EXCL-create `path` holding this process's PID. True when this process now holds the lock.

    PID liveness is the PRIMARY check and `stale_seconds` is only the fallback — see the module
    docstring for why that order is the whole point of this function.

    `alive` overrides the liveness probe. Consumers pass their own module-level `_pid_alive` so a
    test that monkeypatches that name still governs the lock; without the injection point, folding
    these copies into core would quietly decouple every such test from the code it believes it is
    steering — the test would keep passing while testing nothing.
    """
    probe = alive or pid_alive
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass

    try:
        with open(path, encoding="utf-8") as fh:
            holder = int(fh.read().strip())
    except (OSError, ValueError):
        holder = None

    if holder is not None and probe(holder):
        try:
            lock_mtime = os.path.getmtime(path)
        except OSError:
            return False
        if not pid_reused(holder, lock_mtime):
            return False
        # The PID is live but the process behind it started after this lock was written: a
        # recycled number, not the holder. Fall through and reclaim exactly as for a dead holder.
    try:
        # A readable-but-dead holder is stolen immediately; an unreadable one waits out the mtime.
        if holder is not None or time.time() - os.path.getmtime(path) > stale_seconds:
            os.unlink(path)
            return acquire(path, stale_seconds, alive=alive)
    except OSError:
        pass
    return False


def release(path) -> None:
    """Release a lock taken by `acquire`. Best-effort; never raises."""
    try:
        os.unlink(str(path))
    except OSError:
        pass
