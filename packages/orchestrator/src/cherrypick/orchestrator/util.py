"""Small shared helpers."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from cherrypick.core import looplock
from cherrypick.core.redact import (
    mask_account,  # noqa: F401 -- re-exported: accounts/liveops/reconcile import it here
)

# Windows: launch a *console* child (schtasks, git, dolt, …) without popping a console window when the
# parent is windowless (pythonw, as the scheduled tasks run). Pass as `subprocess.run(..., creationflags=
# CREATE_NO_WINDOW)`. 0 elsewhere (the subprocess default), so the same call is cross-platform-safe.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# POSIX: start a child in its OWN session (and so its own process group). Pass as `start_new_session=
# NEW_SESSION`. Without it every child shares the supervisor's group, and the tree kill's killpg fallback
# signals that group -- one console restart would take down the supervisor and every job with it
# (2026-10-08 OS audit). The Windows counterpart is CREATE_NEW_PROCESS_GROUP; False there.
NEW_SESSION = os.name != "nt"


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
    _replace_with_retry(tmp, path)


# On Windows a replace onto a file another process holds open (a reader without delete-sharing,
# antivirus scanning it) fails with PermissionError for a moment. Unretried, that one moment killed
# the supervisor: seven "FATAL: unhandled PermissionError" exits in supervisor.log, 2026-09-21 to
# 2026-10-08, every one in this replace.
_REPLACE_TRIES = 20
_REPLACE_PAUSE_S = 0.1


def _replace_with_retry(src, dst, *, sleep=None) -> None:
    import time as _time

    sleep = sleep or _time.sleep
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            sleep(_REPLACE_PAUSE_S)


pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


def port_owner_pid(port: int) -> int | None:
    """Which PID, if any, holds a LISTENing socket on 127.0.0.1:<port>.

    Best-effort and conservative: any failure to determine an owner (psutil absent, permission
    denied, parse failure) returns None rather than a guess. A caller deciding whether to kill
    something must never act on a guess — see `supervisor._reclaim_stuck_port`, the one place this
    is used to tell a resident job's own child apart from an unrelated process squatting on its port.
    """
    try:
        import psutil  # type: ignore

        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid:
                return conn.pid
        return None
    except ImportError:
        pass
    except Exception:
        return None

    if sys.platform.startswith("linux"):
        return _linux_port_owner(port)
    if sys.platform == "darwin":
        return _macos_port_owner(port)
    if os.name != "nt":
        return None
    try:
        import re
        import subprocess

        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        ).stdout
        for line in out.splitlines():
            m = re.match(r"\s*TCP\s+\S*:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$", line)
            if m and int(m.group(1)) == port:
                return int(m.group(2))
    except Exception:
        return None
    return None


# POSIX without psutil (2026-10-08 OS audit): the stuck-port reclaim never fired off Windows, so a
# stray process on the console's port left it in backoff for good -- the 2026-08-23 failure. Same
# contract as above: any doubt is None, never a guess.
def listen_inodes(proc_net_tcp: str, port: int) -> set[str]:
    """Socket inodes LISTENing on `port`, from the text of /proc/net/tcp or tcp6 (pure)."""
    inodes = set()
    for line in proc_net_tcp.splitlines()[1:]:
        f = line.split()
        if len(f) < 10 or f[3] != "0A":  # 0A = TCP_LISTEN
            continue
        try:
            if int(f[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(f[9])
        except (IndexError, ValueError):
            continue
    return inodes


def _linux_port_owner(port: int, proc: str = "/proc") -> int | None:
    try:
        inodes: set[str] = set()
        for name in ("tcp", "tcp6"):
            try:
                with open(os.path.join(proc, "net", name), encoding="utf-8") as fh:
                    inodes |= listen_inodes(fh.read(), port)
            except OSError:
                continue
        if not inodes:
            return None
        wanted = {f"socket:[{i}]" for i in inodes}
        for pid in (d for d in os.listdir(proc) if d.isdigit()):
            fd_dir = os.path.join(proc, pid, "fd")
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue  # another user's process, or gone
            for fd in fds:
                try:
                    if os.readlink(os.path.join(fd_dir, fd)) in wanted:
                        return int(pid)
                except OSError:
                    continue
    except Exception:
        return None
    return None


def first_pid(lsof_t_output: str) -> int | None:
    """The first PID of `lsof -t` output (one PID per line), or None (pure)."""
    for line in (lsof_t_output or "").splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def _macos_port_owner(port: int) -> int | None:
    try:
        import subprocess

        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        ).stdout
        return first_pid(out)
    except Exception:
        return None


def acquire_pid_lock(path, stale_seconds: int = 180) -> bool:
    """Single-instance guard: O_EXCL-create `path` holding this process's PID.

    Delegates to `cherrypick.core.looplock`, which carries MEIC's P&L-corruption lesson: a
    held-but-ALIVE lock is never stolen regardless of age, and the mtime fallback applies only when
    the holder is dead or unreadable. `pid_alive` is passed explicitly so tests monkeypatching this
    module's name still govern the lock."""
    return looplock.acquire(path, stale_seconds, alive=pid_alive)


def release_pid_lock(path) -> None:
    """Release a lock taken by `acquire_pid_lock`. Best-effort; never raises."""
    looplock.release(path)
