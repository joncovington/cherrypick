"""Holds: a process someone stopped on purpose, which nothing may restart until it is started again.

Before this there was no way to STOP a supervised job: the supervisor brings a dead child straight
back, and the watchdog restarts a dead streamer or service in session. So "stopping" meant a kill
that undid itself a second later, and every restart was a kill someone had to find the PID for.

One file, `state/holds.json`, `{name: {"by", "at"}}`, read by everything that restarts anything:
  * the supervisor stops a held job's child (as a requested exit) and does not launch it;
  * the watchdog does not auto-restart a held streamer or service;
  * the watchdog reports every hold, so a forgotten `stop` cannot quietly cost a session;
  * `install` clears them all -- "turn the suite on" means everything.

`run.py stop <name>` sets one and `run.py start <name>` clears it (see cli.py). A name is a
supervisor job id (`console`, `flies-paper`), `streamer`, or a service id (`gex-recorder`).

Stdlib + local files only, like the supervisor that reads it every pass. A missing or corrupt file
reads as "nothing held": the failure mode of a bad read must be a process that runs, never one that
silently stays down.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config as cfgmod
from .util import atomic_write_json, read_json

HOLDS_FILE = "holds.json"


def path() -> Path:
    return cfgmod.STATE_DIR / HOLDS_FILE


def all_holds() -> dict[str, dict[str, Any]]:
    data = read_json(path())
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def is_held(name: str) -> dict[str, Any] | None:
    """The hold on `name` ({"by", "at"}), or None."""
    return all_holds().get(name)


def hold(name: str, by: str) -> None:
    holds = all_holds()
    holds[name] = {"by": by, "at": time.time()}
    atomic_write_json(path(), holds)


def release(name: str) -> bool:
    """Clear the hold on `name`; True if there was one."""
    holds = all_holds()
    if holds.pop(name, None) is None:
        return False
    atomic_write_json(path(), holds)
    return True


def release_all() -> list[str]:
    """Clear every hold; returns the names that were held."""
    names = sorted(all_holds())
    if names:
        atomic_write_json(path(), {})
    return names


def describe(name: str, h: dict[str, Any]) -> str:
    """`console (held by run.py stop, 12 min)`, for findings and `ps`."""
    try:
        mins = max(0, int((time.time() - float(h.get("at"))) // 60))
        age = f"{mins} min" if mins < 120 else f"{mins // 60} h"
    except (TypeError, ValueError):
        age = "age unknown"
    return f"{name} (held by {h.get('by') or 'unknown'}, {age})"
