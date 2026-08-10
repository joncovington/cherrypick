"""Small shared helpers."""

from __future__ import annotations

import json
import os
from typing import Any

# Windows: launch a *console* child (schtasks, git, dolt, …) without popping a console window when the
# parent is windowless (pythonw, as the scheduled tasks run). Pass as `subprocess.run(..., creationflags=
# CREATE_NO_WINDOW)`. 0 elsewhere (the subprocess default), so the same call is cross-platform-safe.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def first_json(text: str | None) -> dict[str, Any]:
    """Parse the first JSON object from command output.

    Some module CLIs print a JSON status line followed by extra log/diagnostic lines (e.g.
    streamer.py --status). A plain json.loads on the whole buffer then raises "Extra data". This
    tries the whole buffer first, then falls back to the first line that parses as a JSON object.
    Returns {} when nothing parses.
    """
    if not text:
        return {}
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            val = json.loads(line)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            continue
    return {}


def mask_account(value: Any) -> str:
    """Mask an account number to its last 4 digits (`****1234`) — the suite-wide rule for anything that
    surfaces in logs/output. `****` when there are fewer than 4 characters (or the value is empty/None),
    so a full account number is never emitted."""
    s = str(value or "").strip()
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def rotate_if_large(path, max_bytes: int = 5_000_000, keep: int = 3) -> bool:
    """Size-based rotation for the orchestrator's own append logs (watchdog/notify).

    Nothing else rotates these: logrotate deliberately refuses active `.log` files, so
    they grew without bound and were re-read on every dashboard render. When `path`
    exceeds `max_bytes`, shift `path.N` -> `path.N+1` (dropping the oldest past `keep`)
    and move the live file to `path.1`. The rotated `*.log.N` backups are exactly what
    `cherrypick archive` already collects into the monthly zips. Best-effort: any OSError
    (e.g. a concurrent holder on Windows) skips this rotation — the next write retries.
    """
    import os as _os
    from pathlib import Path as _Path

    path = _Path(path)
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return False
        for i in range(keep - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                _os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        _os.replace(path, path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


def read_json(path, default=None) -> Any:
    """Best-effort JSON file read: the parsed value, or `default` ({} if omitted) on any
    miss/parse failure. The one implementation of the pattern watchdog, dashboard, and
    trade_notifier each hand-rolled."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def atomic_write_json(path, obj: Any) -> None:
    """Write JSON via a sibling temp file + `os.replace`, so a reader never sees a half-written
    file. The supervisor rewrites its heartbeat and job registry every few seconds while watchdog
    ticks read them concurrently; a plain `open(..., 'w')` leaves a window where the file is
    truncated-but-unwritten and `read_json` returns {} — indistinguishable from a dead supervisor."""
    from pathlib import Path as _Path

    path = _Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
    os.replace(tmp, path)


def pid_alive(pid: int | None) -> bool:
    """Is `pid` a live process? The probe chain the streamer/gex/flies daemons already settled on:
    psutil, then the Win32 OpenProcess probe, then os.kill(pid, 0) as a last resort — never bare
    os.kill first, which is unreliable on Windows (raises SystemError for some process states)."""
    if not pid or pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, SystemError):
            return False


def acquire_pid_lock(path, stale_seconds: int = 180) -> bool:
    """Single-instance guard: O_EXCL-create `path` holding this process's PID.

    Ports MEIC's `_acquire_once_lock` semantics (the P&L-corruption lesson): a held-but-ALIVE lock
    is never stolen, regardless of age — PID liveness is the primary check, and the `stale_seconds`
    mtime fallback applies only when the holder's PID can't be read (corrupt/truncated write) or the
    holder is dead. Returns True when this process now holds the lock."""
    import time as _time

    path = str(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            with open(path, encoding="utf-8") as fh:
                holder_pid = int(fh.read().strip())
        except (OSError, ValueError):
            holder_pid = None
        if holder_pid is not None and pid_alive(holder_pid):
            return False
        try:
            if holder_pid is not None or _time.time() - os.path.getmtime(path) > stale_seconds:
                os.unlink(path)
                return acquire_pid_lock(path, stale_seconds)
        except OSError:
            pass
        return False


def release_pid_lock(path) -> None:
    """Release a lock taken by `acquire_pid_lock`. Best-effort; never raises."""
    try:
        os.unlink(str(path))
    except OSError:
        pass
