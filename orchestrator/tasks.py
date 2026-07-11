"""Windows Task Scheduler wrappers.

Mirrors the flags MEICAgent's paper_loop.py uses for its self-healing task (`/F /IT`, interactive
token, runs when the user is logged on) so Cherrypick-owned tasks share the same proven behaviour.
Structured so a POSIX backend (cron/launchd) can be added later behind the same functions; today the
functions raise a clear, actionable error on non-Windows rather than silently doing nothing.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


class UnsupportedPlatform(RuntimeError):
    pass


def _require_windows() -> None:
    if os.name != "nt":
        raise UnsupportedPlatform(
            "Cherrypick scheduled tasks are Windows-only for now. On macOS/Linux, run the "
            "watchdog and paper engines via cron/launchd/systemd (see ROADMAP.md)."
        )


def build_tr(exe: str, script: str, *args: str) -> str:
    """Build a schtasks /TR command string with each token quoted."""
    parts = [f'"{exe}"', f'"{script}"', *args]
    return " ".join(parts)


def exists(name: str) -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(["schtasks", "/Query", "/TN", name],
                       capture_output=True, text=True)
    return r.returncode == 0


def query_verbose(name: str) -> dict[str, Any]:
    """Return parsed key fields for a task (Status, Last Result, Last/Next Run Time)."""
    if not exists(name):
        return {"exists": False}
    r = subprocess.run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"],
                       capture_output=True, text=True)
    fields: dict[str, Any] = {"exists": True}
    for line in (r.stdout or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key in ("Status", "Last Result", "Last Run Time", "Next Run Time", "Scheduled Task State"):
            fields[key] = val
    return fields


def create_minute_task(name: str, tr: str, interval_minutes: int, run_now: bool = True) -> dict[str, Any]:
    _require_windows()
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/TR", tr,
         "/SC", "MINUTE", "/MO", str(interval_minutes), "/F", "/IT"],
        capture_output=True, text=True,
    )
    ok = r.returncode == 0
    if ok and run_now:
        subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True, text=True)
    return {"ok": ok, "task": name, "detail": (r.stdout or r.stderr).strip()}


def create_daily_task(name: str, tr: str, at_hhmm: str) -> dict[str, Any]:
    _require_windows()
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/TR", tr,
         "/SC", "DAILY", "/ST", at_hhmm, "/F", "/IT"],
        capture_output=True, text=True,
    )
    return {"ok": r.returncode == 0, "task": name, "detail": (r.stdout or r.stderr).strip()}


def run_now(name: str) -> dict[str, Any]:
    _require_windows()
    r = subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr).strip()}


def delete(name: str) -> dict[str, Any]:
    _require_windows()
    subprocess.run(["schtasks", "/End", "/TN", name], capture_output=True, text=True)
    r = subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr).strip()}
