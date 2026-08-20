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
"""

from __future__ import annotations

import os
import time

__all__ = ["DEFAULT_STALE_SECONDS", "acquire", "pid_alive", "release"]

# Only consulted when the holder is dead or unreadable, so this is a corrupt-lock backstop rather
# than a work-duration budget. It does not need to exceed how long a healthy holder may run.
DEFAULT_STALE_SECONDS = 180


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
        return False
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
