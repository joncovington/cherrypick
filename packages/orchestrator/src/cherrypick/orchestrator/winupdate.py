"""Windows Update's automatic-restart window against the suite's working hours (2026-10-09).

Windows installs updates on its own and restarts outside its "active hours" -- by default whenever it
judges the PC idle, and with nobody logged on (the optional service mode) nothing holds a restart
back. A restart on top of the morning collectors or a live session costs that run. Windows Home has
no policy to stop automatic updates, so the remedy is the active hours: Settings -> Windows Update ->
Advanced options -> Active hours, set manually to cover the suite's day (the maximum span is 18 h).

This reads the setting (registry, no administrator rights needed) and compares it with the span the
suite's own schedule needs -- from its earliest daily job to its latest, in Eastern time, converted
to this machine's clock. Read-only; it never changes Windows settings. `doctor` and `install` report
it.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_UX_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
_MARGIN_MIN = 30  # past the latest job's start: it has to finish too
# Overnight maintenance that a restart only delays: each catches up in its window or the next night.
# Counted, they would stretch "the suite's day" past the 18 hours Windows allows active hours to span.
RESTART_TOLERANT = frozenset({"suite-backup", "log-archive"})


def read_active_hours() -> dict[str, Any] | None:
    """{start, end, smart, early_updates} from the registry (local hours 0-23), or None off Windows
    or when unreadable."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _UX_KEY) as k:

            def val(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return None

            start, end = val("ActiveHoursStart"), val("ActiveHoursEnd")
            if start is None or end is None:
                return None
            return {
                "start": int(start),
                "end": int(end),
                "smart": bool(val("SmartActiveHoursState")),
                "early_updates": bool(val("IsContinuousInnovationOptedIn")),
            }
    except OSError:
        return None


def protected_span_et(job_times_et: list[str]) -> tuple[int, int] | None:
    """(first minute, last minute) of the ET day the schedule needs: the earliest job start to the
    latest plus a margin. None when there are no fixed-time jobs. Pure."""
    mins = sorted(int(t[:2]) * 60 + int(t[3:5]) for t in job_times_et if len(t) >= 5 and t[2] == ":")
    if not mins:
        return None
    return mins[0], min(mins[-1] + _MARGIN_MIN, 24 * 60 - 1)


def exposed_minutes(span_et: tuple[int, int], active: dict[str, Any], et_minus_local_h: int) -> list[int]:
    """The ET minutes (on the hour) of `span_et` that fall OUTSIDE Windows' active hours -- when an
    automatic restart may land. Active hours run [start, end) in local hours and wrap past midnight;
    start == end means 24 h. Pure."""
    start, end = int(active["start"]), int(active["end"])

    def active_at(local_hour: int) -> bool:
        if start == end:
            return True
        return start <= local_hour < end if start < end else (local_hour >= start or local_hour < end)

    first, last = span_et
    out = []
    for m in range(first - first % 60, last + 1, 60):
        local = ((m // 60) - et_minus_local_h) % 24
        if not active_at(local):
            out.append(m)
    return out


def job_times(jobs: dict[str, dict[str, Any]]) -> list[str]:
    """The ET start ("HH:MM") of every enabled daily or monthly job a restart would cost, from the
    supervisor's registry rows (`schedule`: "daily 07:00 ET, ..." / "monthly day 1 03:30 ET"). Pure."""
    import re

    out = []
    for jid, st in jobs.items():
        if jid in RESTART_TOLERANT or not st.get("enabled", True):
            continue
        m = re.match(r"(?:daily|monthly day \d+) (\d{2}:\d{2}) ET", str(st.get("schedule") or ""))
        if m:
            out.append(m.group(1))
    return out


def times_from_specs(specs) -> list[str]:
    """`job_times` from derived JobSpecs instead of registry rows -- for a first install, before
    the supervisor has written its registry. Pure."""
    return [
        s.at_et
        for s in specs
        if s.kind in ("daily", "monthly") and s.enabled and s.at_et and s.id not in RESTART_TOLERANT
    ]


def check_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """`check` over the schedule this config derives (no registry needed)."""
    from . import config as cfgmod
    from . import jobspec, supervisor, timeutil

    specs, _ = jobspec.derive_jobs(
        cfg,
        pythonw=cfgmod.pythonw_exe(),
        launcher=str(supervisor._LAUNCHER),
        now=timeutil.now_et(cfg.get("timezone", "America/New_York")),
        arm_records=supervisor.read_arm_records(cfg),
    )
    return check(times_from_specs(specs))


def check(
    job_times_et: list[str],
    now: datetime | None = None,
    *,
    active: dict[str, Any] | None = None,
    et_minus_local_h: int | None = None,
) -> dict[str, Any]:
    """{status: ok|warn|unknown, detail, ...} for doctor and install. `active` and the hour
    difference are read from this machine unless given (tests)."""
    active = active if active is not None else read_active_hours()
    if active is None:
        return {"status": "unknown", "detail": "Windows Update active hours could not be read"}
    span = protected_span_et(job_times_et)
    if span is None:
        return {"status": "ok", "detail": "no fixed-time jobs to protect"}
    now = now or datetime.now().astimezone()
    if et_minus_local_h is None:
        et_minus_local_h = round(
            (now.astimezone(ET).utcoffset() - now.astimezone().utcoffset()).total_seconds() / 3600
        )
    et_minus_local = et_minus_local_h
    exposed = exposed_minutes(span, active, et_minus_local)

    def hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    local_span = f"{active['start']:02d}:00-{active['end']:02d}:00 local"
    if not exposed and not active["smart"]:
        return {
            "status": "ok",
            "detail": f"active hours {local_span} cover the suite's day ({hhmm(span[0])}-{hhmm(span[1])} ET)",
        }
    need_start = (span[0] // 60 - et_minus_local) % 24
    need_end = (span[1] // 60 + 1 - et_minus_local) % 24
    parts = []
    if exposed:
        parts.append(
            f"Windows may restart for updates at {', '.join(hhmm(m) for m in exposed[:6])}"
            f"{' ...' if len(exposed) > 6 else ''} ET, inside the suite's day "
            f"({hhmm(span[0])}-{hhmm(span[1])} ET)"
        )
    if active["smart"]:
        parts.append("active hours are set to adjust automatically, so they can move")
    return {
        "status": "warn",
        "detail": "; ".join(parts)
        + f". Set Settings > Windows Update > Advanced options > Active hours to Manually, "
        f"{need_start:02d}:00 to {need_end:02d}:00 (this PC's clock)"
        + (
            " -- longer than the 18 h Windows allows, so cover the market and morning first"
            if (need_end - need_start) % 24 > 18
            else ""
        )
        + ".",
        "exposed_et": [hhmm(m) for m in exposed],
    }
