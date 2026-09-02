"""OS scheduler wrappers — Windows Task Scheduler (`schtasks`) and a POSIX cron backend.

The public functions (`create_minute_task`, `create_daily_task`, `exists`, `query_verbose`, `delete`,
`run_now`, `build_tr`) dispatch by platform: Windows uses `schtasks` (mirroring MEICAgent's paper_loop
`/F /IT` flags — interactive token, runs when the user is logged on); POSIX manages the user's crontab,
tagging each cherrypick-owned line with a `# cherrypick:<name>` marker so it can be found/updated/removed
idempotently.

The cron line construction and crontab editing are pure functions (`_minute_schedule`,
`_daily_schedule`, `_cron_line`, `_cron_upsert`, `_cron_remove`, `_cron_has`) so they're unit-tested
cross-platform; only the thin `crontab -l` / `crontab -` I/O is platform-bound. End-to-end cron
*execution* (environment, notifications) still wants validation on a real POSIX host.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .util import CREATE_NO_WINDOW

_IS_WINDOWS = os.name == "nt"


class UnsupportedPlatform(RuntimeError):
    pass


def build_tr(exe: str, script: str, *args: str) -> str:
    """Build a scheduler command string with the exe + script quoted. Usable as a `schtasks /TR`
    value and as a POSIX shell command (the same quoting is valid in both)."""
    parts = [f'"{exe}"', f'"{script}"', *args]
    return " ".join(parts)


# =========================================================================== POSIX cron backend (pure)
_CRON_PREFIX = "# cherrypick:"


def _cron_marker(name: str) -> str:
    return f"{_CRON_PREFIX}{name}"


def _minute_schedule(interval_minutes: int) -> str:
    """A cron schedule firing every `interval_minutes`. cron's `*/N` covers the common cadences the
    suite uses (2/10/30). N must be 1..59; larger intervals need a daily/hour schedule instead."""
    n = int(interval_minutes)
    if not 1 <= n <= 59:
        raise ValueError(f"cron minute interval must be 1..59, got {n} (use a daily task for longer)")
    return f"*/{n} * * * *"


def _windowed_minute_schedule(interval_minutes: int, start_hhmm: str, end_hhmm: str) -> str:
    """A cron schedule firing every `interval_minutes` but only between two clock times.

    For the pre-open supervision window, which is deliberately tight and short-lived: a global
    `*/2` would multiply every tick's work all day to cover 35 minutes. Only supports a window
    inside a single hour, which is all that case needs — spanning hours would need a second cron
    line, and a caller wanting that should register two tasks rather than get a silently wrong one.
    """
    n = int(interval_minutes)
    if not 1 <= n <= 59:
        raise ValueError(f"cron minute interval must be 1..59, got {n}")
    sh, sm = (int(x) for x in start_hhmm.split(":"))
    eh, em = (int(x) for x in end_hhmm.split(":"))
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        raise ValueError(f"invalid window {start_hhmm!r}..{end_hhmm!r}")
    if sh != eh:
        raise ValueError(f"window must stay inside one hour, got {start_hhmm}..{end_hhmm}")
    if em <= sm:
        raise ValueError(f"window end must follow its start, got {start_hhmm}..{end_hhmm}")
    return f"{sm}-{em}/{n} {sh} * * *"


def _daily_schedule(at_hhmm: str) -> str:
    """A cron schedule firing daily at HH:MM."""
    hh, mm = at_hhmm.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid daily time {at_hhmm!r}")
    return f"{m} {h} * * *"


def _monthly_schedule(day: int, at_hhmm: str) -> str:
    """A cron schedule firing on `day` of every month at HH:MM."""
    hh, mm = at_hhmm.split(":")
    h, m, d = int(hh), int(mm), int(day)
    if not (0 <= h <= 23 and 0 <= m <= 59 and 1 <= d <= 28):
        raise ValueError(f"invalid monthly schedule day={day!r} time={at_hhmm!r} (day must be 1..28)")
    return f"{m} {h} {d} * *"


def _cron_line(schedule: str, command: str, name: str) -> str:
    """One crontab line: schedule + command (output discarded) + the ownership marker."""
    return f"{schedule} {command} >/dev/null 2>&1 {_cron_marker(name)}"


def _cron_remove(text: str, name: str) -> str:
    """Drop any cherrypick-owned line(s) for `name`; leave everything else untouched."""
    marker = _cron_marker(name)
    kept = [ln for ln in text.splitlines() if not ln.rstrip().endswith(marker)]
    return ("\n".join(kept) + "\n") if kept else ""


def _cron_upsert(text: str, name: str, line: str) -> str:
    """Replace `name`'s managed line (if present) with `line`, else append it."""
    base = _cron_remove(text, name).rstrip("\n")
    body = (base + "\n" + line) if base else line
    return body + "\n"


def _cron_has(text: str, name: str) -> bool:
    marker = _cron_marker(name)
    return any(ln.rstrip().endswith(marker) for ln in text.splitlines())


def _cron_command_for(text: str, name: str) -> str | None:
    """The command portion (schedule and marker stripped) of `name`'s managed line, for run_now."""
    marker = _cron_marker(name)
    for ln in text.splitlines():
        if ln.rstrip().endswith(marker):
            body = ln.rstrip()[: -len(marker)].rstrip()
            # strip the leading 5 cron fields
            return body.split(None, 5)[5] if len(body.split(None, 5)) == 6 else None
    return None


def _crontab_read() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return r.stdout if r.returncode == 0 else ""


def _crontab_write(text: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["crontab", "-"], input=text, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _cron_create(name: str, schedule: str, command: str) -> dict[str, Any]:
    line = _cron_line(schedule, command, name)
    ok, detail = _crontab_write(_cron_upsert(_crontab_read(), name, line))
    return {"ok": ok, "task": name, "detail": detail or f"cron: {line}"}


# =========================================================================== public API (dispatches)
def exists(name: str) -> bool:
    if _IS_WINDOWS:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", name],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode == 0
    return _cron_has(_crontab_read(), name)


def query_verbose(name: str) -> dict[str, Any]:
    """Return parsed key fields for a task.

    ONE schtasks spawn, not two: existence is read off /Query /V's own return code
    (non-zero = no such task) instead of a separate exists() pre-check — the pre-check
    doubled the per-task spawn count on every dashboard render (~7 tasks per tick)."""
    if not _IS_WINDOWS:
        marker = _cron_marker(name)
        line = next((ln for ln in _crontab_read().splitlines() if ln.rstrip().endswith(marker)), "")
        if not line:
            return {"exists": False}
        return {"exists": True, "backend": "cron", "schedule": " ".join(line.split()[:5])}
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        return {"exists": False}
    fields: dict[str, Any] = {"exists": True}
    for line in (r.stdout or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key in ("Status", "Last Result", "Last Run Time", "Next Run Time", "Scheduled Task State"):
            fields[key] = val
    return fields


def last_run_info(name: str) -> dict[str, Any] | None:
    """The scheduler's own record of when `name` last ran and whether it succeeded — an
    authoritative liveness signal a caller never has to ask the task itself to produce (no log line,
    no marker file: the OS already tracks this for every scheduled task). Returns
    `{"last_run_time": <ISO 8601 with offset>, "last_task_result": <int>}`, or `None` if unavailable
    (task doesn't exist, has never run, or the query failed) — a caller must fall back to some other
    signal in that case.

    Windows only. POSIX cron has no run-history concept at all (it just fires the command; there's
    no built-in record of whether or when it last ran) — callers on that platform fall back to
    whatever they used before this existed (e.g. a log-freshness check)."""
    if not _IS_WINDOWS:
        return None
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$i=Get-ScheduledTaskInfo -TaskName '{name}';"
        "if ($null -eq $i.LastRunTime) { exit 1 };"
        "[PSCustomObject]@{"
        "LastRunTime=$i.LastRunTime.ToString('o');"
        "LastTaskResult=$i.LastTaskResult"
        "} | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        return {"last_run_time": data.get("LastRunTime"), "last_task_result": data.get("LastTaskResult")}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def allow_on_battery(name: str) -> dict[str, Any]:
    """Clear the two battery guards Task Scheduler sets by default — `DisallowStartIfOnBatteries` and
    `StopIfGoingOnBatteries` — so an unattended task still runs on a laptop that isn't plugged in.
    `schtasks` can't set these, so we patch via the ScheduledTasks PowerShell module (preserving all
    other settings). Windows-only and best-effort: a failure here never invalidates task creation."""
    if not _IS_WINDOWS:
        return {"ok": True, "detail": "n/a (posix)"}
    ps = (
        f"$ErrorActionPreference='Stop';"
        f"$s=(Get-ScheduledTask -TaskName '{name}').Settings;"
        f"$s.DisallowStartIfOnBatteries=$false;$s.StopIfGoingOnBatteries=$false;"
        f"Set-ScheduledTask -TaskName '{name}' -Settings $s | Out-Null"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        return {"ok": r.returncode == 0, "detail": (r.stderr.strip()[:200] or "battery guards cleared")}
    except OSError as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def create_minute_task(name: str, tr: str, interval_minutes: int, run_now: bool = True) -> dict[str, Any]:
    if not _IS_WINDOWS:
        res = _cron_create(name, _minute_schedule(interval_minutes), tr)
        if res["ok"] and run_now:
            _run_command(tr)
        return res
    r = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            name,
            "/TR",
            tr,
            "/SC",
            "MINUTE",
            "/MO",
            str(interval_minutes),
            "/F",
            "/IT",
        ],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = r.returncode == 0
    if ok:
        allow_on_battery(name)
        if run_now:
            subprocess.run(
                ["schtasks", "/Run", "/TN", name],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
    return {"ok": ok, "task": name, "detail": (r.stdout or r.stderr).strip()}


def create_windowed_minute_task(
    name: str, tr: str, interval_minutes: int, start_hhmm: str, end_hhmm: str
) -> dict[str, Any]:
    """Register a task firing every `interval_minutes` between two LOCAL clock times, daily.

    Windows gets `/SC MINUTE /MO n /ST <start> /ET <end>`; POSIX gets a minute-range cron line. Not
    day-of-week filtered on either backend: `schtasks /SC MINUTE` has no day filter, and every
    command in this suite already self-gates on `is_trading_day`, so a weekend firing is a no-op
    that costs a process start. Filtering on one platform only would make the two backends disagree
    about something the command already handles.

    Never `run_now`. Its whole purpose is to fire inside a window; running it at install time —
    which is usually the middle of the afternoon — would just be a stray tick.
    """
    if not _IS_WINDOWS:
        return _cron_create(name, _windowed_minute_schedule(interval_minutes, start_hhmm, end_hhmm), tr)
    r = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            name,
            "/TR",
            tr,
            "/SC",
            "MINUTE",
            "/MO",
            str(interval_minutes),
            "/ST",
            start_hhmm,
            "/ET",
            end_hhmm,
            "/F",
            "/IT",
        ],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = r.returncode == 0
    if ok:
        allow_on_battery(name)
    return {"ok": ok, "task": name, "detail": (r.stdout or r.stderr).strip()}


def create_daily_task(name: str, tr: str, at_hhmm: str) -> dict[str, Any]:
    if not _IS_WINDOWS:
        return _cron_create(name, _daily_schedule(at_hhmm), tr)
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/TR", tr, "/SC", "DAILY", "/ST", at_hhmm, "/F", "/IT"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = r.returncode == 0
    if ok:
        allow_on_battery(name)
    return {"ok": ok, "task": name, "detail": (r.stdout or r.stderr).strip()}


def create_monthly_task(name: str, tr: str, day: int, at_hhmm: str) -> dict[str, Any]:
    """Register a task firing on `day` of every month at `at_hhmm` (local time). Windows uses
    `schtasks /SC MONTHLY /D <day>`; POSIX uses a cron `m h D * *` line. Day is clamped to 1..28 by
    `_monthly_schedule` so it exists in every month."""
    if not _IS_WINDOWS:
        return _cron_create(name, _monthly_schedule(day, at_hhmm), tr)
    r = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            name,
            "/TR",
            tr,
            "/SC",
            "MONTHLY",
            "/D",
            str(int(day)),
            "/ST",
            at_hhmm,
            "/F",
            "/IT",
        ],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = r.returncode == 0
    if ok:
        allow_on_battery(name)
    return {"ok": ok, "task": name, "detail": (r.stdout or r.stderr).strip()}


def _run_command(command: str) -> None:
    """Fire a cron-managed command once now (POSIX has no `schtasks /Run`)."""
    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        pass


def run_now(name: str) -> dict[str, Any]:
    if not _IS_WINDOWS:
        cmd = _cron_command_for(_crontab_read(), name)
        if not cmd:
            return {"ok": False, "detail": f"no cron entry for {name}"}
        _run_command(cmd)
        return {"ok": True, "detail": "launched"}
    r = subprocess.run(
        ["schtasks", "/Run", "/TN", name], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )
    return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr).strip()}


def registry_snapshot(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every cherrypick-managed task name -> its `query_verbose()` state.

    One source of truth for "what tasks exist and their state" — shared by `cherrypick status` and the
    dashboard's System panel so they can't drift. Local OS scheduler queries only (no broker/network).
    """
    from . import config as cfgmod  # local import: avoids a cycle at module load

    out: dict[str, dict[str, Any]] = {}
    for mcfg in cfgmod.enabled_modules(cfg).values():
        paper = mcfg.get("paper", {})
        for tkey in ("task_name", "entry_task_name", "exit_task_name"):
            if paper.get(tkey):
                out[paper[tkey]] = query_verbose(paper[tkey])
        svc_task = paper.get("dolt_service", {}).get("task_name")
        if svc_task:
            out[svc_task] = query_verbose(svc_task)
    for section in ("watchdog", "trade_notify"):
        tn = cfg.get(section, {}).get("task_name")
        if tn:
            out[tn] = query_verbose(tn)
    la = cfgmod.archive_settings(cfg)
    if la["enabled"]:
        out[la["task_name"]] = query_verbose(la["task_name"])
    sw = cfgmod.symbol_watch_settings(cfg)
    if sw["enabled"]:
        out[sw["task_name"]] = query_verbose(sw["task_name"])
    return out


def legacy_task_names(cfg: dict[str, Any]) -> list[str]:
    """Every per-job task name the pre-supervisor install registered, by its resolved config name —
    the supervisor cutover's deletion list, and doctor's drift check ('a legacy task still
    registered means the cutover was incomplete and something may double-fire'). The module-
    registered names (meic/flies paper, flies live) come from the same config keys the modules use.
    """
    from . import config as cfgmod  # local import: avoids a cycle at module load

    names: list[str] = []
    for mcfg in cfgmod.enabled_modules(cfg).values():
        paper = mcfg.get("paper", {}) or {}
        for tkey in ("task_name", "entry_task_name", "exit_task_name"):
            if paper.get(tkey):
                names.append(paper[tkey])
        svc_task = (paper.get("dolt_service") or {}).get("task_name")
        if svc_task:
            names.append(svc_task)
        live_task = (mcfg.get("live") or {}).get("task_name")
        if live_task:
            names.append(live_task)
    for section in ("watchdog", "trade_notify"):
        tn = (cfg.get(section, {}) or {}).get("task_name")
        if tn:
            names.append(tn)
    names.append(cfgmod.preopen_settings(cfg)["task_name"])
    names.append(cfgmod.archive_settings(cfg)["task_name"])
    names.append(cfgmod.reconcile_schedule_settings(cfg)["task_name"])
    names.append(cfgmod.symbol_watch_settings(cfg)["task_name"])
    # The pre-supervisor follow-feed task names ("cherrypick-follow-notify", "cherrypick-lossdog-
    # notify") stay on the deletion list as literals: their settings functions left with the feed
    # notifiers (moved to the standalone follow-feed-notifier repo, 2026-08-21), but a pre-cutover
    # box may still hold the tasks, and an undeleted one would double-fire against the standalone.
    names.extend(["cherrypick-follow-notify", "cherrypick-lossdog-notify"])
    return names


def delete(name: str) -> dict[str, Any]:
    """Remove a task. Deleting a task that isn't registered is a **successful no-op**, not a failure.

    `install` calls this unconditionally to clear stale fixed-time tasks (the EOD digest and insight are
    watchdog-fired now), and `schtasks /Delete` exits non-zero on an absent task — so reporting the raw
    return code made every clean install report `ok: false` for those two, which in turn made install's
    top-level `ok` permanently false and unable to signal a real failure. Test existence first rather
    than matching the error text, which is localized.
    """
    if not _IS_WINDOWS:
        ok, detail = _crontab_write(_cron_remove(_crontab_read(), name))
        return {"ok": ok, "detail": detail or f"cron: removed {name}"}
    if not exists(name):
        return {"ok": True, "detail": f"not registered: {name}"}
    subprocess.run(
        ["schtasks", "/End", "/TN", name], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", name, "/F"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr).strip()}
