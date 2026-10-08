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

import os
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
    # A missed night is worth running late in the day rather than not at all; the online backup API
    # makes a copy under a writing loop safe.
    "suite-backup": 20 * 60,
    # Generous: the map only changes when a contract rolls (monthly), and a missed refresh
    # degrades safely -- the recorder drops its futures readings rather than sampling a
    # rolled-off contract -- so catching one up late is strictly better than skipping it.
    "futures-contracts": 8 * 60,
    # The guard check is worth something only before entries open: a late one still reports, but
    # past 09:25 it can no longer warn ahead of the bell, so it stops catching up there.
    "guard-mutants": 35,
    # Very generous: the calendar reaches weeks ahead, so a late pull is harmless while a skipped
    # one eventually starves the scanner. Catching up beats waiting for tomorrow.
    "earnings-dolt-pull": 12 * 60,
    # pmcc's earnings calendar lapses a week before the Dolt horizon, so a day's miss is harmless and
    # catching up through the morning keeps the open's entries answerable.
    "pmcc-earnings-refresh": 6 * 60,
    # A missed review is worth catching up on: the fact set is the input to every read surface,
    # and a session with no artifact is a hole in the trend rather than a late report.
    "review-provisional": 180,
    "review-final": 240,
    "review-narrative": 240,
    # Tight on purpose, unlike the review's: a pre-open pack caught up at 11:00 describes a market
    # that already opened. Late enough to still fire after a 09:00 wake, dead by mid-morning.
    "morning-factpack": 90,
    "morning-narrative": 90,
    # The market files: the evening fetch is good until the pack builds at 08:30, and the pre-pack
    # retry is worth nothing after it.
    # The end-of-day landing reads the local Dolt clones; a late one is the same landing, and the
    # stage and rotation engines read it after the close, so it catches up well into the day.
    "technicals-land": 12 * 60,
    "technicals-report": 12 * 60,
    # Dividend histories change weekly at most; a late fetch is the same fetch.
    "technicals-dividends": 12 * 60,
    # A reading of the day's close: a run after the next open would record the next session instead,
    # which the file keys correctly but is not the day that was missed.
    "technicals-iv-rank": 600,
    # Ahead of the 06:15 landing; a late fetch is the same fetch, and the landing after it picks it up.
    "technicals-index-bars": 12 * 60,
    "market-files": 600,
    "market-files-retry": 40,
    # Priced at the close for the next morning's pack; a late run still prices that close.
    "earnings-moves": 600,
    # Only worth fetching while the morning's narrative is still to be written.
    "fetch-headlines": 10,
    # A light checkpoint describes the session as it stands, so catching one up past the next slot
    # would produce two checkpoints describing nearly the same afternoon. The deep slot is
    # different: it issues the next session's advice, so it stays worth firing until late evening.
    "advisor-light": 45,
    "advisor-deep": 300,
    # The nightly regime-cuts artifact (flies, meic; 2026-09-19). The deep pack reads it at 17:00,
    # but one caught up at 20:00 still serves the console and the next pack; past that the next
    # session rewrites it.
    "regime-cuts": 240,
    # The vendor-edition collector (the market-report fixture feed). An edition fetched late is
    # still the same edition, so the morning pair catches up to mid-morning; the retry is the
    # second chance, not a second request (a day already saved is skipped). The evening chart
    # capture keys its files by the session of the latest bar, so a late one still files right.
    "report-edition": 180,
    "report-edition-retry": 180,
    "report-charts": 300,
    # The stock-universe builder. A measurement must land inside regular hours, and the script
    # refuses outside its window, so a missed slot catches up only while the window is still open;
    # the evening harvest is idempotent (orders merge by id) and good any time before the next day.
    "universe-measure-1": 90,
    "universe-measure-2": 60,
    "universe-daily": 300,
    "universe-watchlist": 300,
    # Broker-cash reconciliation of a settled live session. Generous, because the whole point is
    # that it must not depend on anyone arming the live loop: a box asleep until mid-afternoon
    # should still reconcile that morning's pending dates, and an unreconciled date is only
    # reachable for as long as `pending_reconciliation`'s lookback keeps listing it.
    "fee-reconcile": 480,
    # Until midnight and no further: the model is for the NEXT session, and the scheduler keys a
    # daily job on the ET date, so a fit caught up after midnight would be a new day's job anyway.
    # A missed night costs one session run as the default's twin, which the choice record says.
    "selector-fit": 7 * 60,
    # A close card caught up mid-evening still describes the settled day correctly; past that the
    # next morning's cards take over.
    "status-digest-close": 90,
    # QuikOptions (docs/quikoptions-plan.md): the capture is the session's final page, worth taking
    # all evening; scoring and the post follow it; the confirmation and its post only make sense
    # the morning after, before the next session's capture replaces the question.
    "quikoptions-capture": 300,
    "quikoptions-score": 300,
    "quikoptions-post": 240,
    "quikoptions-confirm": 300,
    "quikoptions-morning": 240,
    "quikoptions-weekly": 300,
}

# Mirrors packages/advisor/src/cherrypick/advisor/factpack.py's LIGHT_SLOTS. Not imported -- this
# package drives the advisor by subprocess, never by import (see packages/advisor/CLAUDE.md's
# fence) -- so the two lists are kept in sync by hand, the same way advisor_checkpoint.py carries
# its own copy rather than importing the package.
ADVISOR_LIGHT_SLOTS = ("open", "am1", "am2", "midday", "pm1", "pm2", "close")


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
    # None → the supervisor's own default cap (10 min). A per-job override for the one case that
    # earns it: a resident job under active development, restarted by hand far more often than a
    # crash backoff was ever sized for. Never widens the general "no broken command hot-loops"
    # guarantee -- it only ever lowers the cap, and a job with no override behaves exactly as before.
    backoff_cap_seconds: int | None = None
    # A resident job that binds a known TCP port. None for every job but the console today. Lets the
    # supervisor tell "my own child, restarting" apart from "something else already holds this port" —
    # see `supervisor._reclaim_stuck_port`. Never guessed from argv; only set where the job's own
    # config exposes the port it will bind.
    port: int | None = None
    reclaim_stuck_port: bool = True

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
        if self.backoff_cap_seconds is not None and int(self.backoff_cap_seconds) <= 0:
            raise ValueError(f"{self.id}: backoff_cap_seconds must be positive")

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


def _suite_script(launcher: str, name: str) -> str:
    """A script from the monorepo's own `scripts/` directory, located from the launcher.

    Derived from the launcher rather than from a package path on purpose: these scripts deliberately
    are NOT packages (that fence is what keeps an AI call unimportable by a trading loop), so there
    is nothing importable to resolve them from.

    It walks up rather than assuming a depth. The launcher is `<repo>/packages/orchestrator/run.py`
    and `scripts/` sits at the repo root, two levels above -- a plain `dirname(launcher)/scripts`
    pointed at `packages/orchestrator/scripts`, which has never existed. That went unnoticed because
    the one job using it (review-narrative) is off by default, so the bad path was never spawned.
    The last-resort fallback keeps the old shape for an unrecognised layout.
    """
    directory = os.path.dirname(os.path.abspath(launcher))
    for _ in range(4):
        candidate = os.path.join(directory, "scripts", name)
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return os.path.join(os.path.dirname(launcher), "scripts", name)


def _narrative_script(launcher: str) -> str:
    return _suite_script(launcher, "eod_narrative.py")


def _advisor_script(launcher: str) -> str:
    return _suite_script(launcher, "advisor_checkpoint.py")


def _morning_narrative_script(launcher: str) -> str:
    return _suite_script(launcher, "morning_narrative.py")


def _futures_contracts_script(launcher: str) -> str:
    return _suite_script(launcher, "refresh_futures_contracts.py")


def _market_files_script(launcher: str) -> str:
    return _suite_script(launcher, "fetch_market_files.py")


def _dolt_data_script(launcher: str) -> str:
    return _suite_script(launcher, "refresh_dolt_data.py")


def _pmcc_earnings_script(launcher: str) -> str:
    return _suite_script(launcher, "pmcc_earnings_refresh.py")


def _vendor_collector_script(launcher: str) -> str:
    return _suite_script(launcher, "fetch_vendor_edition.py")


# How long a QuikOptions step waits for the one before it (`--wait`): past the capture's jitter and
# its paced run, so a wake that fires the whole evening at once still runs it in order.
QUIKOPTIONS_WAIT_MINUTES = 45


def _quikoptions_script(launcher: str, name: str) -> str:
    return _suite_script(launcher, "fetch_quikoptions.py" if name == "fetch" else f"quikoptions_{name}.py")


def _universe_script(launcher: str) -> str:
    return _suite_script(launcher, "build_stock_universe.py")


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

    # follow-notify and lossdog-notify were derived here until 2026-08-21 — both feed notifiers
    # moved to the standalone follow-feed-notifier repo, scheduled by the OS Task Scheduler.
    # `_prune_retired` drops their registry rows once config stops carrying the blocks.

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

    # --- status-digest (webhook push → its own job, never on the watchdog tick, like desk-notify)
    sd = cfgmod.status_digest_settings(cfg)
    add(
        "status-digest",
        lambda: JobSpec(
            id="status-digest",
            argv=_run_py(pythonw, launcher, "notify-status"),
            kind=KIND_INTERVAL,
            interval_seconds=int(sd["interval_minutes"]) * 60,
            window_start=sd["start"],
            window_end=sd["end"],
            trading_days_only=True,
            enabled=sd["enabled"],
            enabled_reason="" if sd["enabled"] else "disabled in config (status_digest)",
        ),
    )
    add(
        "status-digest-close",
        lambda: JobSpec(
            id="status-digest-close",
            argv=_run_py(pythonw, launcher, "notify-status", "--close"),
            kind=KIND_DAILY,
            at_et=sd["close_at"],
            catchup_minutes=CATCHUP_MINUTES["status-digest-close"],
            trading_days_only=True,
            enabled=sd["enabled"],
            enabled_reason="" if sd["enabled"] else "disabled in config (status_digest)",
        ),
    )

    # --- flies-intraday-agent: the agent's paper decisions (packages/flies/docs/intraday-agent-plan.md)
    flies_on = "flies" in cfgmod.enabled_modules(cfg)
    add(
        "flies-intraday-agent",
        lambda: JobSpec(
            id="flies-intraday-agent",
            # scripts/, not a package: it calls a model, so a failure costs a decision file and
            # never an entry -- with no fresh decision the intraday-agent arm runs the fixed rule.
            # The script exits at once unless flies' intraday_agent block is enabled, and it holds
            # its own call budget (trigger band, min gap, per-session cap), so a minute's cadence
            # is a check, not a call.
            argv=(pythonw, _suite_script(launcher, "flies_intraday_agent.py"), "--target", "paper"),
            kind=KIND_INTERVAL,
            interval_seconds=60,
            window_start="09:30",
            window_end="16:00",
            trading_days_only=True,
            enabled=flies_on,
            enabled_reason="" if flies_on else "flies module disabled",
            tags=("ai",),
        ),
    )
    add(
        "flies-intraday-agent-live",
        lambda: JobSpec(
            id="flies-intraday-agent-live",
            # The same script for the live arm. It exits at once unless TODAY's arm record names an
            # agent mode (chosen in /live-flies-start after the YES) that config allows, so on an
            # unarmed day this is a check that costs nothing. Its decision is advice the live loop
            # reads in that mode only: shadow records it, gates sets the trend gate. Never an order.
            argv=(pythonw, _suite_script(launcher, "flies_intraday_agent.py"), "--target", "live"),
            kind=KIND_INTERVAL,
            interval_seconds=60,
            window_start="09:30",
            window_end="16:00",
            trading_days_only=True,
            enabled=flies_on,
            enabled_reason="" if flies_on else "flies module disabled",
            tags=("ai", "live"),
        ),
    )
    # --- flies-payoff-post: the settled payoff chart to Discord, once per ledger per session
    fp = cfgmod.flies_payoff_post_settings(cfg)
    add(
        "flies-payoff-post",
        lambda: JobSpec(
            id="flies-payoff-post",
            # scripts/, not a package: it drives a browser and posts to a webhook, so a failure
            # costs a picture and never a settlement. Idempotent per ledger per session, so the
            # window repeats it until live's official print lands.
            argv=(pythonw, _suite_script(launcher, "flies_payoff_post.py"))
            + tuple(arg for m in fp["modes"] for arg in ("--mode", m)),
            kind=KIND_INTERVAL,
            interval_seconds=fp["interval_minutes"] * 60,
            window_start=fp["start"],
            window_end=fp["end"],
            trading_days_only=True,
            enabled=fp["enabled"],
            enabled_reason="" if fp["enabled"] else "disabled in config (flies_payoff_post)",
        ),
    )
    # --- flies-payoff-intraday: the same chart while the book is open, hourly through the session
    fpi = fp["intraday"]
    add(
        "flies-payoff-intraday",
        lambda: JobSpec(
            id="flies-payoff-intraday",
            # The same script with --intraday: no settlement wait, one post per ledger per ET hour
            # (its own marker file), and it stands down once a ledger has settled.
            argv=(pythonw, _suite_script(launcher, "flies_payoff_post.py"), "--intraday")
            + tuple(arg for m in fp["modes"] for arg in ("--mode", m)),
            kind=KIND_INTERVAL,
            interval_seconds=fpi["interval_minutes"] * 60,
            window_start=fpi["start"],
            window_end=fpi["end"],
            trading_days_only=True,
            enabled=fpi["enabled"],
            enabled_reason="" if fpi["enabled"] else "disabled in config (flies_payoff_post.intraday)",
        ),
    )

    # --- console (the suite's read surface): the one job with no window at all
    con = cfgmod.console_settings(cfg)
    if not con["enabled"]:
        con_reason = "disabled in config (console)"
    elif not con["server_entry"].exists():
        # An unbuilt checkout is off-by-choice-is-healthy, not a job to restart every 30s. The fix is
        # `pnpm install && pnpm build` in packages/console, and the reason says so.
        con_reason = "console not built (packages/console: pnpm install && pnpm build)"
    else:
        con_reason = ""
    add(
        "console",
        lambda con=con, con_reason=con_reason: JobSpec(
            id="console",
            argv=(pythonw, str(con["launcher"]), "dashboard", "--serve"),
            kind=KIND_RESIDENT,
            cwd=str(con["root"]),
            # Resident jobs are re-evaluated on the ~1s loop; this is the silence budget, not a cadence.
            interval_seconds=con["silence_seconds"],
            # No window and no trading-day gate, deliberately: a read surface that is only up during
            # RTH cannot be used to read the session that just ended.
            silence_file=str(cfgmod.console_heartbeat_path()),
            silence_seconds=con["silence_seconds"],
            enabled=not con_reason,
            enabled_reason=con_reason,
            backoff_cap_seconds=con["dev_backoff_seconds"],
            port=con["port"],
            reclaim_stuck_port=con["reclaim_stuck_port"],
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

        # The nightly regime-cuts artifact, for any module that declares it (2026-09-19). Driven off
        # the module's own paper block -- `regime_cuts_at` and `regime_cuts_argv` -- so a third
        # module is covered the moment it declares them, and a module that does not (calendars,
        # pmcc, curve, bwb: no regime substrate) gets no job. Timing: after the module settles and
        # after review-provisional (16:30), before advisor-deep (17:00 by default); a `deep_at`
        # moved earlier than the cut reads last night's artifact, which the pack labels stale.
        cuts_at = paper.get("regime_cuts_at")
        cuts_argv = paper.get("regime_cuts_argv")
        if cuts_at and cuts_argv:
            add(
                f"{name}-regime-cuts",
                lambda name=name, root=root, cuts_at=cuts_at, cuts_argv=cuts_argv: JobSpec(
                    id=f"{name}-regime-cuts",
                    argv=(pythonw, *cuts_argv),
                    kind=KIND_DAILY,
                    cwd=root,
                    at_et=str(cuts_at),
                    catchup_minutes=CATCHUP_MINUTES["regime-cuts"],
                    trading_days_only=True,
                ),
            )

        # The selector's nightly model (2026-10-04, flies): fitted after the session settles and the
        # regime cuts are written, for the next session, which pins it at its first tick. Declared in
        # the module's own paper block like the cuts; a module with no selector declares no keys.
        fit_at = paper.get("selector_fit_at")
        fit_argv = paper.get("selector_fit_argv")
        if fit_at and fit_argv:
            add(
                f"{name}-selector-fit",
                lambda name=name, root=root, fit_at=fit_at, fit_argv=fit_argv: JobSpec(
                    id=f"{name}-selector-fit",
                    argv=(pythonw, *fit_argv),
                    kind=KIND_DAILY,
                    cwd=root,
                    at_et=str(fit_at),
                    catchup_minutes=CATCHUP_MINUTES["selector-fit"],
                    trading_days_only=True,
                ),
            )

        # Broker-cash reconciliation of settled LIVE sessions (2026-09-22). Scheduled rather than
        # ridden on the live tick, because it must run whether or not anyone armed live trading:
        # the tick path only executed under `--live`, the dead-man's switch returned before
        # reaching it, and the supervisor disables the live job entirely once disarmed -- so a
        # session traded on Tuesday went unreconciled forever if Wednesday was never armed.
        # Unconditional on arming by design; a module with no live path simply declares no keys.
        recon_at = paper.get("fee_reconcile_at")
        recon_argv = paper.get("fee_reconcile_argv")
        if recon_at and recon_argv:
            add(
                f"{name}-fee-reconcile",
                lambda name=name, root=root, recon_at=recon_at, recon_argv=recon_argv: JobSpec(
                    id=f"{name}-fee-reconcile",
                    argv=(pythonw, *recon_argv),
                    kind=KIND_DAILY,
                    cwd=root,
                    at_et=str(recon_at),
                    catchup_minutes=CATCHUP_MINUTES["fee-reconcile"],
                    trading_days_only=True,
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
    rv = cfgmod.review_settings(cfg)
    add(
        "review-provisional",
        lambda: JobSpec(
            id="review-provisional",
            argv=_run_py(pythonw, launcher, "review", "--provisional"),
            kind=KIND_DAILY,
            at_et=rv["provisional_at"],
            catchup_minutes=CATCHUP_MINUTES["review-provisional"],
            # A daily job fires every calendar day. Without this a Saturday pass writes a fact set
            # for a session that never happened -- the same weekend problem the deleted suite EOD
            # tasks needed a guard for, and the reason they needed one at all.
            trading_days_only=True,
            enabled=rv["enabled"],
            enabled_reason="" if rv["enabled"] else "disabled in config (review)",
        ),
    )
    add(
        "review-final",
        lambda: JobSpec(
            id="review-final",
            argv=_run_py(pythonw, launcher, "review", "--final"),
            kind=KIND_DAILY,
            at_et=rv["final_at"],
            catchup_minutes=CATCHUP_MINUTES["review-final"],
            # A daily job fires every calendar day. Without this a Saturday pass writes a fact set
            # for a session that never happened -- the same weekend problem the deleted suite EOD
            # tasks needed a guard for, and the reason they needed one at all.
            trading_days_only=True,
            enabled=rv["enabled"],
            enabled_reason="" if rv["enabled"] else "disabled in config (review)",
        ),
    )
    add(
        "review-narrative",
        lambda: JobSpec(
            id="review-narrative",
            # scripts/, not a package: the trading loops import packages/*, so nothing importable
            # by a loop can reach an AI call. The supervisor only ever SPAWNS this, the way it
            # spawns any job -- it is not on the watchdog's health tick, which is what the retired
            # eod-insight trigger was.
            argv=(pythonw, _narrative_script(launcher))
            + (("--file-issues",) if rv.get("file_issues") else ()),
            kind=KIND_DAILY,
            at_et=rv["narrative_at"],
            catchup_minutes=CATCHUP_MINUTES["review-narrative"],
            trading_days_only=True,
            enabled=rv["enabled"] and rv["narrative"],
            enabled_reason=(
                "" if (rv["enabled"] and rv["narrative"]) else "disabled in config (review.narrative)"
            ),
            tags=("ai",),
        ),
    )
    tc = cfgmod.technicals_settings(cfg)
    # The Dolt server is kept alive by the earnings module's own job, so technicals needs earnings
    # running -- and through enabled_modules, a machine with Dolt set up (capabilities.dolt).
    dolt_on = "earnings" in cfgmod.enabled_modules(cfg)
    tc_on = tc["enabled"] and dolt_on
    add(
        "technicals-land",
        lambda: JobSpec(
            id="technicals-land",
            # A package command, not a script: it reads the LOCAL dolt sql-server only (the
            # clones are pulled by the 05:30 earnings-dolt-pull script) and writes its own store.
            # That server is kept alive by the earnings module's own job, so without earnings
            # there is nothing to read and the job would fail every morning.
            argv=(pythonw, "-m", "cherrypick.technicals", "land"),
            kind=KIND_DAILY,
            at_et=tc["land_at"],
            catchup_minutes=CATCHUP_MINUTES["technicals-land"],
            trading_days_only=False,
            enabled=tc_on,
            enabled_reason=(
                ""
                if tc_on
                else "disabled in config (technicals)"
                if not tc["enabled"]
                else "earnings module disabled (no dolt sql-server to read)"
            ),
        ),
    )
    add(
        "technicals-report",
        lambda: JobSpec(
            id="technicals-report",
            # Reads only the technicals store and the market-report files; writes report-<session>.json.
            argv=(pythonw, "-m", "cherrypick.technicals", "report"),
            kind=KIND_DAILY,
            at_et=tc["report_at"],
            catchup_minutes=CATCHUP_MINUTES["technicals-report"],
            trading_days_only=False,
            enabled=tc_on,
            enabled_reason=""
            if tc_on
            else "disabled with technicals-land (it reads what the landing writes)",
        ),
    )
    div_on = tc["enabled"] and tc["dividends"]
    add(
        "technicals-dividends",
        lambda: JobSpec(
            id="technicals-dividends",
            # A script, not package code: it reads the broker (tastytrade's dividend history) with the
            # shared credential, read-only, one symbol a second. technicals stays network-free and
            # reads the file this writes.
            argv=(pythonw, _suite_script(launcher, "fetch_dividends.py")),
            kind=KIND_DAILY,
            at_et=tc["dividends_at"],
            catchup_minutes=CATCHUP_MINUTES["technicals-dividends"],
            trading_days_only=False,
            enabled=div_on,
            enabled_reason="" if div_on else "disabled in config (technicals.dividends)",
        ),
    )
    ixb_on = tc["enabled"] and tc["index_bars"]
    add(
        "technicals-index-bars",
        lambda: JobSpec(
            id="technicals-index-bars",
            # A script, not package code: it reads the broker (DXLink daily candles for the cash
            # indexes Dolt does not carry, SPX) with the shared credential, read-only. technicals
            # reads the file it writes.
            argv=(pythonw, _suite_script(launcher, "fetch_index_bars.py")),
            kind=KIND_DAILY,
            at_et=tc["index_bars_at"],
            catchup_minutes=CATCHUP_MINUTES["technicals-index-bars"],
            trading_days_only=False,
            enabled=ixb_on,
            enabled_reason="" if ixb_on else "disabled in config (technicals.index_bars)",
        ),
    )
    ivr_on = tc["enabled"] and tc["iv_rank"]
    add(
        "technicals-iv-rank",
        lambda: JobSpec(
            id="technicals-iv-rank",
            # A script, not package code: it reads the broker (tastytrade's market metrics) with the
            # shared credential, read-only, 50 symbols a request. technicals reads the file it writes.
            argv=(pythonw, _suite_script(launcher, "fetch_iv_rank.py")),
            kind=KIND_DAILY,
            at_et=tc["iv_rank_at"],
            catchup_minutes=CATCHUP_MINUTES["technicals-iv-rank"],
            trading_days_only=True,
            enabled=ivr_on,
            enabled_reason="" if ivr_on else "disabled in config (technicals.iv_rank)",
        ),
    )
    mv = cfgmod.morning_settings(cfg)
    files_on = mv["enabled"] and mv["files"]
    for job_id, at in (("market-files", mv["files_at"]), ("market-files-retry", mv["files_retry_at"])):
        add(
            job_id,
            lambda job_id=job_id, at=at: JobSpec(
                id=job_id,
                # A script, not a package: overview is network-free, so the files it reads are
                # fetched out here and a failed fetch leaves yesterday's files, never a broken one.
                argv=(pythonw, _market_files_script(launcher), "fetch"),
                kind=KIND_DAILY,
                at_et=at,
                catchup_minutes=CATCHUP_MINUTES[job_id],
                trading_days_only=True,
                enabled=files_on,
                enabled_reason="" if files_on else "disabled in config (morning.files)",
            ),
        )
    hl_on = mv["enabled"] and mv["headlines"]
    add(
        "fetch-headlines",
        lambda: JobSpec(
            id="fetch-headlines",
            # A script: it reads public RSS feeds (titles and links only); the narrative reads its file.
            argv=(pythonw, _suite_script(launcher, "fetch_headlines.py")),
            kind=KIND_DAILY,
            at_et=mv["headlines_at"],
            catchup_minutes=CATCHUP_MINUTES["fetch-headlines"],
            trading_days_only=True,
            enabled=hl_on,
            enabled_reason="" if hl_on else "disabled in config (morning.headlines)",
        ),
    )
    em_on = mv["enabled"] and mv["earnings_moves"]
    add(
        "earnings-moves",
        lambda: JobSpec(
            id="earnings-moves",
            # A script: it prices straddles through the broker (read-only); the pack reads its file.
            argv=(pythonw, _suite_script(launcher, "fetch_earnings_moves.py")),
            kind=KIND_DAILY,
            at_et=mv["earnings_moves_at"],
            catchup_minutes=CATCHUP_MINUTES["earnings-moves"],
            trading_days_only=True,
            enabled=em_on,
            enabled_reason="" if em_on else "disabled in config (morning.earnings_moves)",
        ),
    )
    add(
        "morning-factpack",
        lambda: JobSpec(
            id="morning-factpack",
            argv=_run_py(pythonw, launcher, "morning"),
            kind=KIND_DAILY,
            at_et=mv["factpack_at"],
            catchup_minutes=CATCHUP_MINUTES["morning-factpack"],
            # A daily job fires every calendar day; without this a Saturday pass writes a pack for
            # a session that never happened -- same weekend lesson as the review jobs.
            trading_days_only=True,
            enabled=mv["enabled"],
            enabled_reason="" if mv["enabled"] else "disabled in config (morning)",
        ),
    )
    add(
        "morning-narrative",
        lambda: JobSpec(
            id="morning-narrative",
            # scripts\, not a package -- same fence as review-narrative: nothing a loop can import
            # is allowed to reach an AI call. This one additionally does web lookups for the macro
            # calendar, which is one more reason it can only ever be a spawned script.
            argv=(pythonw, _morning_narrative_script(launcher)),
            kind=KIND_DAILY,
            at_et=mv["narrative_at"],
            catchup_minutes=CATCHUP_MINUTES["morning-narrative"],
            trading_days_only=True,
            enabled=mv["enabled"] and mv["narrative"],
            enabled_reason=(
                "" if (mv["enabled"] and mv["narrative"]) else "disabled in config (morning.narrative)"
            ),
            tags=("ai",),
        ),
    )
    add(
        "pmcc-earnings-refresh",
        lambda: JobSpec(
            id="pmcc-earnings-refresh",
            # A script, not package code: it reads the local Dolt calendar, which nothing on a pmcc
            # loop path may. It rewrites pmcc's declared `earnings` block (the `earnings_span`
            # refusal, 2026-10-06) through the config editor. 06:00, after the 05:30 Dolt pull and
            # before the open. A pmcc config declaring no calendar makes it a no-op.
            argv=(pythonw, _pmcc_earnings_script(launcher)),
            kind=KIND_DAILY,
            at_et="06:00",
            catchup_minutes=CATCHUP_MINUTES["pmcc-earnings-refresh"],
            trading_days_only=False,
            enabled="pmcc" in cfgmod.enabled_modules(cfg),
            enabled_reason="" if "pmcc" in cfgmod.enabled_modules(cfg) else "pmcc module disabled",
        ),
    )
    add(
        "earnings-dolt-pull",
        lambda: JobSpec(
            id="earnings-dolt-pull",
            # A script, not package code: it reaches DoltHub, and nothing on a decision path may.
            # This is the job whose ABSENCE stopped earnings paper trading for eleven sessions in
            # August 2026 -- the sql-server job kept the server alive while the DATA silently aged
            # out of its forward calendar. Not trading-days-only: pulling on a weekend is free and
            # means Monday opens with a current calendar.
            #
            # 05:30, an hour AHEAD of earnings' own 06:30 pre-market forward scan, and the ordering
            # is the whole point rather than a preference. The scan reads this data to decide which
            # names are even eligible that night, so a pull that lands after it means the scan walked
            # yesterday's calendar -- the exact staleness this job exists to prevent, just one day
            # smaller. Landed at 06:30 on 2026-08-24 and moved 2026-08-25: same minute as the scan,
            # so it also had the two contending for Dolt while the clones were being rewritten.
            argv=(pythonw, _dolt_data_script(launcher)),
            kind=KIND_DAILY,
            at_et="05:30",
            catchup_minutes=CATCHUP_MINUTES["earnings-dolt-pull"],
            trading_days_only=False,
            enabled="earnings" in cfgmod.enabled_modules(cfg),
            enabled_reason=(
                ""
                if "earnings" in cfgmod.enabled_modules(cfg)
                else "earnings module disabled, or Dolt not set up (capabilities.dolt)"
            ),
        ),
    )
    mr = cfgmod.market_report_settings(cfg)
    if mr["collector"]:
        mr_reason = ""
    elif mr["collector_switch"]:
        mr_reason = "market_report.collector is on, but " + ", ".join(mr["collector_missing"]) + " not set"
    else:
        mr_reason = "disabled in config (market_report.collector)"
    for job_id, at, sub in (
        ("report-edition", mr["edition_at"], "edition"),
        ("report-edition-retry", mr["edition_retry_at"], "edition"),
        ("report-charts", mr["charts_at"], "charts"),
    ):
        add(
            job_id,
            lambda job_id=job_id, at=at, sub=sub: JobSpec(
                id=job_id,
                # A script, not a package: it signs in to a third-party site. It paces itself (one
                # session, 20-60 s between pages, a 24-hour cooldown after any 429/403) and stops at
                # a challenge rather than retrying, so this schedule is the only thing that repeats it.
                argv=(pythonw, _vendor_collector_script(launcher), sub),
                kind=KIND_DAILY,
                at_et=at,
                catchup_minutes=CATCHUP_MINUTES[job_id],
                trading_days_only=True,
                enabled=mr["collector"],
                enabled_reason=mr_reason,
            ),
        )
    add(
        "report-session-alert",
        lambda: JobSpec(
            id="report-session-alert",
            # Offline: reads the collector's own state file and, while an expired session has a
            # fetch waiting, repeats the warning once an hour (the script keeps the hour, so this
            # interval only bounds how late a reminder can be). Every day, not trading days only:
            # an outage that starts Friday evening is still one on Saturday.
            argv=(pythonw, _vendor_collector_script(launcher), "session-alert"),
            kind=KIND_INTERVAL,
            interval_seconds=600,
            enabled=mr["collector"],
            enabled_reason=mr_reason,
        ),
    )
    uv_reason = "" if mr["universe"] else "disabled in config (market_report.universe)"
    uv_jobs = [
        (f"universe-measure-{i}", at, "measure") for i, at in enumerate(mr["universe_measure_at"][:2], 1)
    ]
    for job_id, at, sub in (*uv_jobs, ("universe-daily", mr["universe_daily_at"], "daily")):
        add(
            job_id,
            lambda job_id=job_id, at=at, sub=sub: JobSpec(
                id=job_id,
                # A script, not a package: it reads the broker (REST snapshots, never the streamer,
                # whose budget cannot carry several hundred names) and the tastylive follow feed.
                # Read-only; it paces itself and refuses to measure outside regular hours.
                argv=(pythonw, _universe_script(launcher), sub),
                kind=KIND_DAILY,
                at_et=at,
                catchup_minutes=CATCHUP_MINUTES[job_id],
                trading_days_only=True,
                enabled=mr["universe"],
                enabled_reason=uv_reason,
            ),
        )
    qo = cfgmod.quikoptions_settings(cfg)
    qo_off = "disabled in config (quikoptions.enabled)"
    post_off = "disabled in config (quikoptions.post, which needs quikoptions.enabled)"
    capture_argv = (
        "hot-options",
        "--jitter",
        str(qo["jitter_minutes"]),
        *(() if qo["headed"] else ("--headless",)),
    )
    wait = ("--wait", str(QUIKOPTIONS_WAIT_MINUTES))
    for job_id, at, script, args, on, reason in (
        # Scripts, not packages: the capture signs in to a third-party site (paced, one session, a
        # 24-hour cooldown after a refusal), scoring and confirming read the broker (read-only market
        # data), and the posts push a webhook. A day with no capture leaves the rest nothing to do.
        ("quikoptions-capture", qo["at"], "fetch", capture_argv, qo["enabled"], qo_off),
        (
            "quikoptions-score",
            qo["score_at"],
            "flow",
            ("score", "--require-today", *wait),
            qo["enabled"],
            qo_off,
        ),
        ("quikoptions-confirm", qo["confirm_at"], "flow", ("confirm",), qo["enabled"], qo_off),
        ("quikoptions-post", qo["post_at"], "post", wait, qo["post"], post_off),
        (
            "quikoptions-morning",
            qo["morning_at"],
            "post",
            ("--kind", "morning", *wait),
            qo["post"] and qo["post_morning"],
            post_off if not qo["post"] else "disabled in config (quikoptions.post_morning)",
        ),
        (
            "quikoptions-weekly",
            qo["weekly_at"],
            "post",
            ("--kind", "weekly"),
            qo["post"] and qo["post_weekly"],
            post_off if not qo["post"] else "disabled in config (quikoptions.post_weekly)",
        ),
    ):
        add(
            job_id,
            lambda job_id=job_id, at=at, script=script, args=args, on=on, reason=reason: JobSpec(
                id=job_id,
                argv=(pythonw, _quikoptions_script(launcher, script), *args),
                kind=KIND_DAILY,
                at_et=at,
                catchup_minutes=CATCHUP_MINUTES[job_id],
                trading_days_only=True,
                enabled=on,
                enabled_reason="" if on else reason,
            ),
        )
    wl_on = mr["universe"] and mr["universe_watchlist"]
    add(
        "universe-watchlist",
        lambda: JobSpec(
            id="universe-watchlist",
            # The suite's one scheduled write to the broker account: the universe mirrored to a
            # private watchlist, never an order. Its own switch on top of `universe`; the script
            # only replaces the list it created and refuses to empty or halve it in one sync.
            argv=(pythonw, _universe_script(launcher), "watchlist", "--apply"),
            kind=KIND_DAILY,
            at_et=mr["universe_watchlist_at"],
            catchup_minutes=CATCHUP_MINUTES["universe-watchlist"],
            trading_days_only=True,
            enabled=wl_on,
            enabled_reason="" if wl_on else "disabled in config (market_report.universe_watchlist)",
        ),
    )
    add(
        "futures-contracts",
        lambda: JobSpec(
            id="futures-contracts",
            # A script, not a package: resolving a futures contract needs the broker's instruments
            # endpoint, and the regime recorder that consumes this map is deliberately
            # credential-free and network-free. Same fence as the narratives -- a spawned process
            # whose failure costs a refresh and nothing else.
            argv=(pythonw, _futures_contracts_script(launcher)),
            kind=KIND_DAILY,
            at_et="08:45",
            catchup_minutes=CATCHUP_MINUTES["futures-contracts"],
            trading_days_only=True,
            enabled=True,
        ),
    )
    add(
        "guard-mutants",
        lambda: JobSpec(
            id="guard-mutants",
            # Before the bell: re-runs each guard's test under a mutant that breaks it, so a guard
            # that can no longer fail is a WARNING before anything trades, not a surprise after.
            # `--if-changed` makes a quiet morning a no-op -- a guard only stops failing when code
            # changes, and CI checks every push -- while an uncommitted edit on this machine is
            # still re-checked. Tests and in-memory patches only; no ledger, no broker.
            argv=(pythonw, _suite_script(launcher, "guard_mutants.py"), "--if-changed"),
            kind=KIND_DAILY,
            at_et="08:50",
            catchup_minutes=CATCHUP_MINUTES["guard-mutants"],
            trading_days_only=True,
            enabled=True,
        ),
    )
    av = cfgmod.advisor_settings(cfg)
    advisor_modules = ",".join(name for name, mod in av["modules"].items() if (mod or {}).get("enabled"))
    advisor_reason = "" if av["enabled"] else "disabled in config (advisor)"
    for index, at in enumerate(av["checkpoints"]):
        # Light slots share one shape; only the time differs. Named from ADVISOR_LIGHT_SLOTS
        # regardless of the configured hours so the store's filenames stay legible. A checkpoints
        # list longer than ADVISOR_LIGHT_SLOTS falls back to a generic name the advisor package
        # will reject -- that job's own derivation fails and is reported, nothing else is affected.
        named = av.get("checkpoint_slots")
        if named is not None and index < len(named):
            # The dict config form names each checkpoint explicitly (config.py advisor_settings).
            slot = named[index]
        else:
            slot = ADVISOR_LIGHT_SLOTS[index] if index < len(ADVISOR_LIGHT_SLOTS) else f"slot{index + 1}"
        add(
            f"advisor-{slot}",
            lambda slot=slot, at=at: JobSpec(
                id=f"advisor-{slot}",
                # scripts/, not a package -- same fence as review-narrative: nothing a loop can
                # import is allowed to reach an AI call.
                argv=(
                    pythonw,
                    _advisor_script(launcher),
                    "--slot",
                    slot,
                    "--model",
                    av["light_model"],
                    "--timeout",
                    str(av["timeout_seconds"]),
                )
                + (("--modules", advisor_modules) if advisor_modules else ()),
                kind=KIND_DAILY,
                at_et=at,
                catchup_minutes=CATCHUP_MINUTES["advisor-light"],
                trading_days_only=True,
                enabled=av["enabled"],
                enabled_reason=advisor_reason,
                tags=("ai",),
            ),
        )
    add(
        "advisor-deep",
        lambda: JobSpec(
            id="advisor-deep",
            argv=(
                pythonw,
                _advisor_script(launcher),
                "--slot",
                "deep",
                "--model",
                av["deep_model"],
                "--timeout",
                str(av["timeout_seconds"]),
            )
            + (("--modules", advisor_modules) if advisor_modules else ()),
            kind=KIND_DAILY,
            # After review-provisional (16:30) on purpose: the deep pack carries that fact set, and
            # the prompt tells the model it is provisional.
            at_et=av["deep_at"],
            catchup_minutes=CATCHUP_MINUTES["advisor-deep"],
            trading_days_only=True,
            enabled=av["enabled"],
            enabled_reason=advisor_reason,
            tags=("ai",),
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
    cbk = cfgmod.config_backup_settings(cfg)
    add(
        "config-backup",
        lambda: JobSpec(
            id="config-backup",
            # Local git plus an optional push to the person's own remote (orchestrator.config_backup):
            # commits whatever the home repo's allow-list tracks. A pass with nothing changed is a no-op.
            argv=_run_py(pythonw, launcher, "config-backup"),
            kind=KIND_INTERVAL,
            interval_seconds=cbk["interval_minutes"] * 60,
            enabled=cbk["enabled"],
            enabled_reason="" if cbk["enabled"] else "disabled in config (config_backup.enabled)",
        ),
    )

    bk = cfgmod.backup_settings(cfg)
    add(
        "suite-backup",
        lambda: JobSpec(
            id="suite-backup",
            # Local files only (orchestrator.backup): one verified zip of the suite's own data a night.
            argv=_run_py(pythonw, launcher, "backup"),
            kind=KIND_DAILY,
            at_et=bk["at"],
            catchup_minutes=CATCHUP_MINUTES["suite-backup"],
            trading_days_only=False,
            enabled=bk["enabled"],
            enabled_reason="" if bk["enabled"] else "disabled in config (backup.enabled)",
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
            # In-session only: the module's own --interval loop is RTH-scoped.
            window_start=paper.get("resident_start", "09:30"),
            window_end=paper.get("resident_end", "16:00"),
            trading_days_only=True,
            # Silence is measured against the loop's own HEARTBEAT, never its log. This used to point
            # at the log file on the assumption -- stated here as fact, enforced nowhere -- that
            # "every in-session iteration writes at least one log line per symbol". Flies and MEIC
            # happened to satisfy it; calendars does not, because a week holding no position has
            # nothing to say, and it was killed and restarted every two minutes for four days as a
            # result (107 times on 2026-08-17 alone). A log is a side effect of having something to
            # report, so supervising on it makes verbosity a reliability dependency and makes a quiet
            # healthy loop look exactly like a wedged one.
            #
            # The contract is now the one the console already had: the loop touches this file at the
            # top of every tick. A module that does NOT publish one degrades safely rather than
            # loudly -- `_resident_silent` returns False for a file that does not exist, so it simply
            # is not silence-supervised, and the watchdog reports the gap instead of the supervisor
            # killing a healthy process over it.
            # Keyed on the MODULE name, not the job id: the module writes this file and knows only
            # its own package name (`core.home.heartbeat_path("calendars")`).
            silence_file=str(cfgmod.resident_heartbeat_path(name)),
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
