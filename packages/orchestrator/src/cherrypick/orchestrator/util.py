"""Small shared helpers."""

from __future__ import annotations

import json
import os
from typing import Any

from cherrypick.core import looplock

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
