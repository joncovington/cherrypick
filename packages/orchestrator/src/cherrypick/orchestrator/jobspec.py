"""Supervisor job specs and pure schedule math.

The supervisor daemon (`orchestrator/supervisor.py`) replaces ~13 Windows Task Scheduler entries
with one process. Everything *decidable* lives here as pure functions over (spec, state, now) so the
schedule semantics — ET wall-clock, trading-day/window gates, missed-fire policy, DST behavior —
are unit-testable with fake clocks and never depend on the daemon's own loop.

Derivation (`derive_jobs`) reads the SAME config blocks `tasks.registry_snapshot` reads — the job
table is a projection of config, never a parallel source of truth. Each block derives inside its own
try/except so one malformed block disables one job, never the daemon.

Missed-fire policy (the schtasks behaviors we must consciously replace):
- interval jobs: `next_run` is set from the actual start time, so after sleep/hibernate the job
  fires ONCE immediately and then resumes cadence — never a burst of catch-up fires.
- daily/monthly jobs: fire-once-if-missed inside a per-job catchup window (judged on the ET
  calendar date), else record `missed` and skip to the next occurrence. Catchup windows are
  conservative and per-job: a 15:45 earnings entry fired at 17:00 would trade a dead session.
- DST: all schedule math is ET wall-clock (`timeutil.now_et`). Fall-back's repeated hour can't
  double-fire a daily job (the fired-date stamp is per calendar date); spring-forward's missing
  hour resolves to the first following instant. No suite job is scheduled inside 02:00–03:00.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from . import config as cfgmod
from . import timeutil

KIND_INTERVAL = "interval"
KIND_DAILY = "daily"
KIND_MONTHLY = "monthly"
KIND_RESIDENT = "resident"

# Per-job catchup windows (minutes) for daily/monthly jobs — how late a missed fixed-time fire may
# still fire after wake/restart. Entry is tight (aligned under the 35-min entry-SLA grace so the
# watchdog still owns that alarm); archive is a week (idempotent, only touches finished months).
CATCHUP_MINUTES = {
    "entry": 30,
    "exit": 120,
    "symbol-watch": 150,  # 06:30 scheduled; still useful until ~09:00
    "reconcile": 240,
    "log-archive": 7 * 24 * 60,
}


@dataclass(frozen=True)
class JobSpec:
    """One supervisor-owned job. `argv` is the FULL command (interpreter first), spawned headless.

    kind:
      interval — spawn `argv` every `interval_seconds` (subject to window/trading-day gates)
      daily    — spawn once per ET calendar day at `at_et`
      monthly  — spawn once per month on `day_of_month` at `at_et`
      resident — keep `argv` running while the window is active; restart on death, and on
                 silence when `silence_file` goes quiet for `silence_seconds`
    """

    id: str
    argv: tuple[str, ...]
    kind: str
    cwd: str | None = None
    interval_seconds: int = 0
    at_et: str | None = None  # "HH:MM" for daily/monthly
    day_of_month: int = 1
    window_start: str | None = None  # "HH:MM" ET; with window_end bounds interval/resident jobs
    window_end: str | None = None
    window_invert: bool = False  # fire only OUTSIDE the window (flies off-session tick)
    trading_days_only: bool = False
    catchup_minutes: int = 0
    silence_file: str | None = None
    silence_seconds: int = 0
    enabled: bool = True
    enabled_reason: str = ""
    tags: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        """Validate at construction so a malformed config block fails inside `derive_jobs`'s
        per-job try/except (one bad block → one job in `errors`), not later inside the daemon loop."""
        if self.kind not in (KIND_INTERVAL, KIND_DAILY, KIND_MONTHLY, KIND_RESIDENT):
            raise ValueError(f"unknown job kind {self.kind!r}")
        if self.kind in (KIND_INTERVAL, KIND_RESIDENT) and int(self.interval_seconds) <= 0:
            raise ValueError(f"{self.id}: interval_seconds must be positive")
        if self.kind in (KIND_DAILY, KIND_MONTHLY):
            _hhmm(str(self.at_et or ""))
        if self.kind == KIND_MONTHLY and not 1 <= int(self.day_of_month) <= 28:
            raise ValueError(f"{self.id}: day_of_month must be 1..28 so it exists in every month")
        if bool(self.window_start) != bool(self.window_end):
            raise ValueError(f"{self.id}: window_start and window_end must be set together")
        if self.window_start and self.window_end:
            if _hhmm(self.window_start) >= _hhmm(self.window_end):
                raise ValueError(f"{self.id}: window end must follow its start")

    def describe(self) -> str:
        if self.kind == KIND_INTERVAL:
            base = f"every {self.interval_seconds}s"
        elif self.kind == KIND_DAILY:
            base = f"daily {self.at_et} ET"
        elif self.kind == KIND_MONTHLY:
            base = f"monthly day {self.day_of_month} {self.at_et} ET"
        else:
            base = "resident"
        if self.window_start and self.window_end:
            side = "outside" if self.window_invert else "within"
            base += f" {side} {self.window_start}-{self.window_end} ET"
        if self.trading_days_only:
            base += ", trading days"
        return base


# --------------------------------------------------------------------------- pure schedule math
def _hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid HH:MM {value!r}")
    return h, m


def in_window(spec: JobSpec, now: datetime) -> bool:
    """Is `now` (ET) inside the job's clock window? No window declared → always True.
    `window_invert` flips the answer (the flies off-session tick runs only OUTSIDE RTH)."""
    if not spec.window_start or not spec.window_end:
        return True
    sh, sm = _hhmm(spec.window_start)
    eh, em = _hhmm(spec.window_end)
    t = now.time()
    inside = (t.hour, t.minute) >= (sh, sm) and (t.hour, t.minute) <= (eh, em)
    return not inside if spec.window_invert else inside


def _gates_pass(spec: JobSpec, now: datetime, holidays: set[str] | None) -> tuple[bool, str]:
    if not spec.enabled:
        return False, spec.enabled_reason or "disabled"
    if spec.trading_days_only and not timeutil.is_trading_day(now, holidays):
        return False, "not a trading day"
    if not in_window(spec, now):
        return False, "outside window"
    return True, ""


def should_start(
    spec: JobSpec, state: dict[str, Any], now: datetime, holidays: set[str] | None = None
) -> tuple[bool, str, dict[str, Any]]:
    """Decide whether to spawn `spec` now. Returns (start, reason, state_patch).

    `state` is the job's persisted registry entry; the caller applies `state_patch` whether or not
    the job starts (it carries fired/missed stamps). Pure — the supervisor owns spawning and the
    overlap guard (a job whose previous run is still alive is never re-evaluated here).
    """
    ok, why = _gates_pass(spec, now, holidays)
    if not ok:
        return False, why, {}

    if spec.kind == KIND_INTERVAL:
        now_epoch = now.timestamp()
        if now_epoch < float(state.get("next_run_epoch") or 0.0):
            return False, "not due", {}
        return True, "due", {"next_run_epoch": now_epoch + spec.interval_seconds}

    if spec.kind == KIND_DAILY:
        return _fixed_time_decision(spec, state, now, key=now.strftime("%Y-%m-%d"), key_field="last_fire_day")

    if spec.kind == KIND_MONTHLY:
        if now.day < spec.day_of_month:
            return False, f"before day {spec.day_of_month}", {}
        sched_day_now = now.replace(day=spec.day_of_month)
        return _fixed_time_decision(
            spec, state, sched_day_now, key=now.strftime("%Y-%m"), key_field="last_fire_month", now=now
        )

    return False, f"kind {spec.kind} is not schedulable here", {}


def _fixed_time_decision(
    spec: JobSpec,
    state: dict[str, Any],
    sched_base: datetime,
    *,
    key: str,
    key_field: str,
    now: datetime | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Shared daily/monthly logic: fire once per `key` (ET date / month) at `at_et`, within the
    catchup window; past it, stamp the occurrence as missed so it stops being evaluated."""
    now = now or sched_base
    if state.get(key_field) == key:
        return False, "already fired", {}
    h, m = _hhmm(spec.at_et or "00:00")
    sched = sched_base.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < sched:
        return False, f"before {spec.at_et} ET", {}
    if spec.catchup_minutes and now > sched + timedelta(minutes=spec.catchup_minutes):
        return (
            False,
            f"missed ({spec.at_et} + {spec.catchup_minutes}m catchup passed)",
            {key_field: key, "missed": now.isoformat()},
        )
    return True, "due", {key_field: key, "missed": None}


def resident_should_run(spec: JobSpec, now: datetime, holidays: set[str] | None = None) -> tuple[bool, str]:
    """Should a resident job's child be up right now? (Restart/backoff/silence are the daemon's.)"""
    return _gates_pass(spec, now, holidays)


# --------------------------------------------------------------------------- derivation from config
def _run_py(pythonw: str, launcher: str, *args: str) -> tuple[str, ...]:
    return (pythonw, launcher, *args)


def _module_tick_argv(paper: dict[str, Any]) -> list[str] | None:
    """The argv (sans interpreter) for one supervisor-fired paper tick. `tick_argv` when declared;
    else `once_argv` with `--force` stripped — MEIC's once_argv carries `--force` for the manual
    'run one now' path, and a scheduled tick must never bypass the RTH/trading-day gates (the
    Saturday-settlement lesson in flies' config notes)."""
    tick = paper.get("tick_argv")
    if tick:
        return list(tick)
    once = paper.get("once_argv")
    if once:
        return [a for a in once if a != "--force"]
    return None


def arm_record_valid(arm: dict[str, Any] | None, live_cfg: dict[str, Any], now: datetime) -> tuple[bool, str]:
    """Is the flies live arm record live RIGHT NOW? Valid = dated today (ET) and before
    disarm_time + grace. The supervisor only ever READS the record — arming authority stays with
    the module's human-confirmed command; a stale/absent record simply disables the job."""
    if not arm:
        return False, "not armed (no arm record)"
    today = now.strftime("%Y-%m-%d")
    if str(arm.get("date")) != today:
        return False, f"arm record is for {arm.get('date')}, not today"
    disarm = str(arm.get("disarm_time") or live_cfg.get("disarm_time") or "17:00")
    grace = int(live_cfg.get("disarm_grace_minutes", 30))
    h, m = _hhmm(disarm)
    cutoff = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(minutes=grace)
    if now >= cutoff:
        return False, f"past disarm {disarm} (+{grace}m grace)"
    return True, f"armed for {today}"


def derive_jobs(
    cfg: dict[str, Any],
    *,
    pythonw: str,
    launcher: str,
    now: datetime,
    arm_records: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[JobSpec], dict[str, str]]:
    """Project config into the supervisor's job table. Returns (jobs, errors) where `errors` maps a
    job id to why its derivation failed (that job is omitted; everything else derives normally).

    Disabled-by-config jobs are INCLUDED with enabled=False + a reason, so status/doctor can keep
    the off-by-choice-is-healthy distinction the schtasks registry had.
    """
    jobs: list[JobSpec] = []
    errors: dict[str, str] = {}
    arm_records = arm_records or {}

    def add(job_id: str, build) -> None:
        try:
            spec = build()
            if spec is not None:
                jobs.append(spec)
        except Exception as exc:  # one bad block disables one job, never the daemon
            errors[job_id] = f"{type(exc).__name__}: {exc}"

    # --- watchdog (full tick, unchanged cadence)
    wd = cfg.get("watchdog", {}) or {}
    add(
        "watchdog",
        lambda: JobSpec(
            id="watchdog",
            argv=_run_py(pythonw, launcher, "watchdog"),
            kind=KIND_INTERVAL,
            interval_seconds=int(wd.get("interval_minutes", 10)) * 60,
        ),
    )

    # --- streamer-health (replaces the cherrypick-preopen windowed task; whole-session coverage)
    sh = wd.get("streamer_health", {}) or {}
    add(
        "streamer-health",
        lambda: JobSpec(
            id="streamer-health",
            argv=_run_py(pythonw, launcher, "streamer-health"),
            kind=KIND_INTERVAL,
            interval_seconds=int(sh.get("interval_seconds", 60)),
            window_start=sh.get("start", "09:00"),
            window_end=sh.get("end", "16:00"),
            trading_days_only=True,
            enabled=sh.get("enabled", True),
            enabled_reason="" if sh.get("enabled", True) else "disabled in config (watchdog.streamer_health)",
        ),
    )

    # --- trade-notify (files-only; 30s default so fills reach the user fast)
    tn = cfg.get("trade_notify", {}) or {}
    add(
        "trade-notify",
        lambda: JobSpec(
            id="trade-notify",
            argv=_run_py(pythonw, launcher, "notify-trades"),
            kind=KIND_INTERVAL,
            interval_seconds=int(tn.get("interval_seconds") or int(tn.get("interval_minutes", 0)) * 60 or 30),
            enabled=bool(tn.get("task_name") or tn.get("enabled", False) or tn.get("interval_seconds")),
            enabled_reason="" if tn else "no trade_notify config",
        ),
    )

    # --- follow-notify (network → its own job, never on the watchdog tick)
    ff = cfgmod.follow_feed_settings(cfg)
    add(
        "follow-notify",
        lambda: JobSpec(
            id="follow-notify",
            argv=_run_py(pythonw, launcher, "notify-follow"),
            kind=KIND_INTERVAL,
            interval_seconds=int(ff["interval_minutes"]) * 60,
            enabled=ff["enabled"],
            enabled_reason="" if ff["enabled"] else "disabled in config (follow_feed)",
        ),
    )

    # --- desk-notify (broker call + webhook → its own job, never on the watchdog tick)
    dn = cfgmod.desk_notify_settings(cfg)
    add(
        "desk-notify",
        lambda: JobSpec(
            id="desk-notify",
            argv=_run_py(pythonw, launcher, "notify-desk"),
            kind=KIND_INTERVAL,
            interval_seconds=int(dn["interval_minutes"]) * 60,
            enabled=dn["enabled"],
            enabled_reason="" if dn["enabled"] else "disabled in config (desk_notify)",
        ),
    )

    # --- per-module jobs
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {}) or {}
        kind = paper.get("kind")
        root = str(cfgmod.module_root(mcfg, name))

        if kind == "self_healing":
            tick = _module_tick_argv(paper)
            if tick:
                _add_self_healing_jobs(add, name, mcfg, paper, tick, pythonw, root)
        elif kind == "cherrypick_scheduled":
            add(
                f"{name}-entry",
                lambda name=name, paper=paper: JobSpec(
                    id=f"{name}-entry",
                    argv=_run_py(pythonw, launcher, f"run-{name}-entry"),
                    kind=KIND_DAILY,
                    at_et=paper["entry_time"],
                    catchup_minutes=CATCHUP_MINUTES["entry"],
                ),
            )
            add(
                f"{name}-exit",
                lambda name=name, paper=paper: JobSpec(
                    id=f"{name}-exit",
                    argv=_run_py(pythonw, launcher, f"run-{name}-exit"),
                    kind=KIND_DAILY,
                    at_et=paper["exit_time"],
                    catchup_minutes=CATCHUP_MINUTES["exit"],
                ),
            )

        svc = paper.get("dolt_service")
        if svc:
            add(
                f"{name}-dolt",
                lambda name=name, svc=svc: JobSpec(
                    id=f"{name}-dolt",
                    argv=_run_py(pythonw, launcher, "ensure-dolt"),
                    kind=KIND_INTERVAL,
                    interval_seconds=int(svc.get("interval_minutes", 5)) * 60,
                ),
            )

        # LIVE loop (flies): enabled iff the module's arm record is valid right now. The job spec
        # itself is derived every pass, so arming/disarming takes effect within one loop pass.
        live = mcfg.get("live")
        if live and live.get("task_name"):
            armed, why = arm_record_valid(arm_records.get(name), live, now)
            tick_argv = live.get("tick_argv") or ["-m", f"cherrypick.{name}.live_loop", "--once", "--live"]
            add(
                f"{name}-live",
                lambda name=name, live=live, armed=armed, why=why, tick_argv=tick_argv, root=root: JobSpec(
                    id=f"{name}-live",
                    argv=(pythonw, *tick_argv),
                    kind=KIND_INTERVAL,
                    cwd=root,
                    interval_seconds=int(live.get("tick_interval_seconds", 60)),
                    enabled=armed,
                    enabled_reason=why,
                    tags=("live",),
                ),
            )

    # --- daily/monthly suite jobs
    sw = cfgmod.symbol_watch_settings(cfg)
    add(
        "symbol-watch",
        lambda: JobSpec(
            id="symbol-watch",
            argv=_run_py(pythonw, launcher, "run-earnings-symbol-watch"),
            kind=KIND_DAILY,
            at_et=sw["at"],
            catchup_minutes=CATCHUP_MINUTES["symbol-watch"],
            enabled=sw["enabled"],
            enabled_reason="" if sw["enabled"] else "disabled in config (symbol_watch)",
        ),
    )
    rs = cfgmod.reconcile_schedule_settings(cfg)
    add(
        "reconcile",
        lambda: JobSpec(
            id="reconcile",
            argv=_run_py(pythonw, launcher, "reconcile", "--scheduled"),
            kind=KIND_DAILY,
            at_et=rs["at"],
            catchup_minutes=CATCHUP_MINUTES["reconcile"],
            enabled=rs["enabled"],
            enabled_reason="" if rs["enabled"] else "disabled in config (reconcile.schedule)",
        ),
    )
    la = cfgmod.archive_settings(cfg)
    add(
        "log-archive",
        lambda: JobSpec(
            id="log-archive",
            argv=_run_py(pythonw, launcher, "archive"),
            kind=KIND_MONTHLY,
            at_et=la["at"],
            day_of_month=la["day"],
            catchup_minutes=CATCHUP_MINUTES["log-archive"],
            enabled=la["enabled"],
            enabled_reason="" if la["enabled"] else "disabled in config (log_archive)",
        ),
    )

    return jobs, errors


def _add_self_healing_jobs(add, name, mcfg, paper, tick, pythonw, root) -> None:
    """Jobs for a self-healing (continuous-tick) module. Cadence >= 60s stays spawn-per-tick — each
    run a short-lived process that reliably completes (MEIC's fragile-daemon lesson). A sub-minute
    cadence instead runs the module's own resident `--interval N` mode IN-SESSION (restart-on-death
    + silence supervision replaces per-tick crash isolation), with a 60s `--once` spawn job outside
    the session so settlement, retries, and the idle heartbeat keep the exact shape they have today.
    """
    interval = int(paper.get("tick_interval_seconds", 120))
    log_name = paper.get("log")
    silence_file = str(cfgmod.module_logs_dir(name) / log_name) if log_name else None

    if interval >= 60:
        add(
            f"{name}-paper",
            lambda: JobSpec(
                id=f"{name}-paper",
                argv=(pythonw, *tick),
                kind=KIND_INTERVAL,
                cwd=root,
                interval_seconds=interval,
            ),
        )
        return

    resident_argv = [a for a in tick if a != "--once"] + ["--interval", str(interval)]
    add(
        f"{name}-paper",
        lambda: JobSpec(
            id=f"{name}-paper",
            argv=(pythonw, *resident_argv),
            kind=KIND_RESIDENT,
            cwd=root,
            interval_seconds=interval,
            # In-session only: the module's own --interval loop is RTH-scoped, and every in-session
            # iteration writes at least one log line per symbol, which is what silence watches.
            window_start=paper.get("resident_start", "09:30"),
            window_end=paper.get("resident_end", "16:00"),
            trading_days_only=True,
            silence_file=silence_file,
            silence_seconds=int(paper.get("silence_seconds", 120)),
        ),
    )
    add(
        f"{name}-paper-offsession",
        lambda: JobSpec(
            id=f"{name}-paper-offsession",
            argv=(pythonw, *tick),
            kind=KIND_INTERVAL,
            cwd=root,
            interval_seconds=60,
            window_start=paper.get("resident_start", "09:30"),
            window_end=paper.get("resident_end", "16:00"),
            window_invert=True,
        ),
    )


__all__ = [
    "JobSpec",
    "KIND_INTERVAL",
    "KIND_DAILY",
    "KIND_MONTHLY",
    "KIND_RESIDENT",
    "CATCHUP_MINUTES",
    "derive_jobs",
    "should_start",
    "resident_should_run",
    "in_window",
    "arm_record_valid",
    "replace",
]
