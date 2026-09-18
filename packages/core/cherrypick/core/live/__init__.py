"""cherrypick.core.live -- the per-day arm record and dead-man's switch a live loop runs under.

A live loop in this suite is armed FOR ONE DAY by a human, through a command that asks for a
literal confirmation and then writes one file: ``state/<module>-live-arm.json``. That file is the
whole armed signal, read by three parties that cannot import each other -- the module's own tick
(self-disarm), the orchestrator's supervisor (job enablement, within one pass) and its watchdog
(the dead-man backstop). Nothing here places an order or reads a broker; it is file arithmetic.

Hoisted from flies' live loop on 2026-09-18 when bwb became the second module to arm this way.
Everything the record MEANS lives here so the second module cannot drift from the first: the
filename convention (which the orchestrator's `supervisor.arm_record_path` mirrors off its own
patchable state dir), what a record contains, the two ways a tick decides it must disarm, and the
rule that under a live supervisor arming is a record write and nothing else. flies' legacy
schtasks fallback stays in flies; it is a transition-window path no second module needs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home

__all__ = [
    "arm_record_name",
    "arm_record_path",
    "write_arm_record",
    "arm_record_date",
    "should_disarm",
    "supervisor_heartbeat_fresh",
    "arm",
    "disarm",
]

SUPERVISOR_HEARTBEAT = "supervisor.last.json"


def arm_record_name(module: str) -> str:
    """``<module>-live-arm.json`` -- the one spelling the module and the orchestrator share."""
    return f"{module}-live-arm.json"


def arm_record_path(module: str) -> Path:
    """The live ARM RECORD: "armed for today" as a file in the shared state dir. Present and dated
    today enables the ``<module>-live`` supervisor job; deleting it disarms within one pass. Only
    the module's human-confirmed arm command ever writes it."""
    return _home.state_dir() / arm_record_name(module)


def write_arm_record(
    module: str, *, date: str, armed_by: str, at: str, confirmation: str = "literal-YES"
) -> Path:
    """Write the record. ``date`` is the session (ET) it arms; ``armed_by`` names the command that
    asked for the confirmation, so the record says who authorised it, not just when."""
    path = arm_record_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"date": date, "at": at, "armed_by": armed_by, "confirmation": confirmation}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    return path


def arm_record_date(module: str, *, legacy_paths: Iterable[str | Path] = ()) -> str | None:
    """The session the record arms, or None. ``legacy_paths`` are read-only fallbacks for a record
    written at a pre-cutover location (flies' ``data/flies/live_armed.json``), never written."""
    for path in (arm_record_path(module), *legacy_paths):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("date")
        except (OSError, ValueError):
            continue
    return None


def should_disarm(
    armed_for: str | None, *, today: str, now_min: int, disarm_min: int, disarm_label: str
) -> str | None:
    """The dead-man's switch, pure: a reason when a live tick must disarm itself, else None.

    Two reasons and no third: the record is not for today (armed on a previous day and the
    machine slept through the disarm window, or never armed through the confirming command at
    all), or the clock is past ``disarm_time``. A loop calls this FIRST on every tick, before any
    broker read, so a stale arm can never place an order."""
    if armed_for != today:
        return f"arm stamp is {armed_for!r}, today is {today} -- arming is per-day"
    if now_min >= disarm_min:
        return f"past disarm time ({disarm_label})"
    return None


def supervisor_heartbeat_fresh(max_age_seconds: int = 90) -> bool:
    """Is the orchestrator's supervisor daemon driving this box? A file read, never an import.
    Fresh means arming is a record write and the supervisor fires the ticks."""
    try:
        with open(_home.state_dir() / SUPERVISOR_HEARTBEAT, encoding="utf-8") as f:
            ts = json.load(f).get("ts")
        then = datetime.fromisoformat(str(ts))
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        return (datetime.now(UTC) - then).total_seconds() <= max_age_seconds
    except (OSError, ValueError, TypeError):
        return False


def arm(
    module: str,
    *,
    date: str,
    at: str,
    armed_by: str,
    spawn_first_tick: Callable[[], None] | None = None,
    heartbeat_fresh: Callable[[], bool] | None = None,
) -> dict:
    """Arm ``module`` for ``date`` under a live supervisor: write the record, fire one immediate
    tick so arming does not wait a full interval, and say so. Refuses (``ok: False``) when no
    supervisor heartbeat is fresh -- the supervisor is what turns the record into ticks, so a
    record without one arms nothing and must not claim to."""
    fresh = heartbeat_fresh() if heartbeat_fresh is not None else supervisor_heartbeat_fresh()
    if not fresh:
        return {
            "ok": False,
            "error": "no supervisor running -- the arm record only arms when the supervisor derives the job",
        }
    path = write_arm_record(module, date=date, at=at, armed_by=armed_by)
    if spawn_first_tick is not None:
        try:
            spawn_first_tick()
        except OSError:
            pass  # the next scheduled tick covers it
    return {
        "ok": True,
        "driver": "supervisor",
        "cadence": f"every tick_interval (supervisor job {module}-live)",
        "armed_for": date,
        "detail": f"arm record written: {path}",
    }


def disarm(module: str, *, legacy_paths: Iterable[str | Path] = ()) -> dict:
    """Delete the arm record (and any legacy-location copy). The authoritative act: the
    supervisor disables the job within one pass. Honest about nothing having been armed."""
    removed = False
    for path in (arm_record_path(module), *legacy_paths):
        try:
            os.unlink(path)
            removed = True
        except OSError:
            pass
    return {
        "ok": removed,
        "arm_record_removed": removed,
        "detail": "disarmed (arm record removed)" if removed else "nothing was armed",
    }
