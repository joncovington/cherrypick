"""cherrypick watchdog — the walk-away reliability guarantee, minimal form.

Runs on its own schedule (a Windows task, every N minutes). Each run it checks that every enabled
module's paper pipeline is registered, alive, and producing fresh data during the trading session,
and that the day's scheduled paper runs actually happened. Findings are:
  - always written to logs/watchdog.log (the floor), and
  - notified (with de-dup + re-notify throttling) on any WARN/CRITICAL and on recovery.

It performs only benign, non-trading remediation (restart the data streamer). It never places,
cancels, or closes an order, and it never touches live trading — its authority is data + alerts.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import home as core_home
from cherrypick.core import streamrequests as _streamrequests

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import eval_activity, jobspec, servicecfg, tasks, timeutil, util
from .util import CREATE_NO_WINDOW, first_json

_WATCHDOG_LOG = cfgmod.LOGS_DIR / "watchdog.log"
_STATE_FILE = cfgmod.STATE_DIR / "watchdog_state.json"
_HEARTBEAT = cfgmod.STATE_DIR / "watchdog.last.json"

# The in-place launcher (pythonw run.py <verb>) for detached EOD subprocesses. watchdog.py lives at
# src/cherrypick/orchestrator/watchdog.py, so the repo-root run.py is three parents up from its dir.
_RUN_PY = Path(__file__).resolve().parents[3] / "run.py"
# Reserved (non-finding) state key marking the day the EOD digest/insight were fired, so they fire once.

OK, WARN, CRITICAL = "OK", "WARN", "CRITICAL"
_RANK = {OK: 0, WARN: 1, CRITICAL: 2}


@dataclass
class Finding:
    key: str
    status: str
    title: str
    message: str


# --------------------------------------------------------------------------- helpers
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- supervisor dual-read
# During the schtasks→supervisor transition every registration check reads BOTH ways: when a live
# supervisor is driving this box (fresh heartbeat + live PID), job presence comes from its registry;
# otherwise the legacy scheduled-task checks apply unchanged. The fallback branch is deleted with
# the transition window (P6).
def _supervisor_driving() -> bool:
    from . import supersnap  # local import: avoids a cycle at module load

    return supersnap.supervisor_alive()


def _supervisor_job(job_id: str) -> dict[str, Any] | None:
    from . import supersnap

    return supersnap.job_state(job_id)


def _check_supervisor(cfg: dict[str, Any]) -> list[Finding]:
    """Belt-and-suspenders over the supervisor itself: the anchor task restarts a dead supervisor
    (ensure-supervisor), and this finding catches the inverse faults the anchor can't see — the
    anchor task being deleted while the supervisor still runs (nothing would restart it after the
    next crash), and a wedged/dead supervisor observed from a still-firing legacy watchdog task
    mid-transition. Skipped entirely on a box that has neither an anchor nor a heartbeat (pre-
    cutover), so the dual-read period stays alarm-free."""
    from . import supersnap, supervisor

    # The heartbeat file is the post-cutover marker: the supervisor writes it on its first pass and
    # it persists thereafter. No file → pre-cutover box → skip (and never spawn a schtasks query).
    if not supervisor.heartbeat_path().exists():
        return []
    anchor_exists = tasks.exists(supersnap.ANCHOR_TASK)
    findings: list[Finding] = []
    if supersnap.supervisor_alive():
        findings.append(
            Finding(
                "supervisor.alive",
                OK,
                "Supervisor",
                f"running (heartbeat {supersnap.heartbeat_age_seconds():.0f}s old)",
            )
        )
    else:
        age = supersnap.heartbeat_age_seconds()
        findings.append(
            Finding(
                "supervisor.alive",
                CRITICAL,
                "Supervisor is not running",
                (f"Heartbeat is {age:.0f}s old" if age is not None else "No supervisor heartbeat found")
                + f" (limit {supervisor.HEARTBEAT_FRESH_SECONDS}s). Scheduled jobs are not being "
                "fired. ensure-supervisor should restart it within ~2 min; check logs/supervisor.log.",
            )
        )
    if anchor_exists:
        findings.append(Finding("supervisor.anchor", OK, "Supervisor anchor task", "registered"))
    else:
        findings.append(
            Finding(
                "supervisor.anchor",
                CRITICAL,
                "Supervisor anchor task missing",
                f"Scheduled task '{supersnap.ANCHOR_TASK}' is not registered — nothing will restart "
                "the supervisor if it dies. Run: cherrypick install",
            )
        )
    return findings


def _check_job_registry_drift(cfg: dict[str, Any]) -> list[Finding]:
    """Jobs config DERIVES that the running supervisor has never heard of — see
    `supersnap.jobs_missing_from_registry` for why this is invisible everywhere else.

    Deliberately WARN rather than CRITICAL: nothing is broken *right now*, the missed work is
    whatever the undelivered jobs would have done, and the remedy is a human restarting the daemon
    rather than something to page about at 3am."""
    from . import supersnap

    findings: list[Finding] = []
    for job_id, reason in sorted(supersnap.jobs_failing_derivation().items()):
        findings.append(
            Finding(
                "supervisor.job_derive_failed",
                WARN,
                f"Job {job_id} could not be built from config",
                f"{reason}. It is not scheduled and will not fire; the supervisor keeps its row so "
                "the history is not erased. Fix the config or the derivation, then restart it.",
            )
        )

    missing = supersnap.jobs_missing_from_registry(cfg)
    if missing is None:
        return findings  # not answerable; `_check_supervisor` owns the dead-daemon alarm
    if not missing:
        return [*findings, Finding("supervisor.jobs_current", OK, "Supervisor job table", "matches config")]
    return [
        *findings,
        Finding(
            "supervisor.jobs_stale",
            WARN,
            "Supervisor is running a stale job table",
            f"{len(missing)} job(s) derived from config that the running supervisor has never seen: "
            f"{', '.join(missing)}. It loads jobspec once at startup, so these are not scheduled and "
            "will not fire. Restart it to pick them up: cherrypick supervise --stop, then "
            "cherrypick ensure-supervisor.",
        )
    ]


def _check_console(cfg: dict[str, Any]) -> list[Finding]:
    """The console (the suite's only read surface since 2026-08-12) is a supervisor-managed
    resident job like a module's paper loop, but with no paper-freshness backstop: a module's own
    (b) check would eventually flag a stalled resident loop by proxy (no new trade writes), while
    the console produces no other artifact whose staleness would out it.

    **That parenthetical was wrong about modules, and cost four days.** A module's freshness check
    does NOT backstop a stuck resident loop: a restart loop keeps writing (each new child records a
    loop iteration), so the paper DB reads 0 min old precisely BECAUSE the job is thrashing.
    `calendars-paper` was restarted 107 times on 2026-08-17 under an unbroken `OK / 0 min old`.
    `_check_resident_health` is the check that actually covers that, for every resident job
    including this one; what stays here is the console's job-presence and enabled state.

    Found live on 2026-08-14:
    its job sat un-running (backoff/orphaned bookkeeping) for ~21 hours with nothing surfacing it,
    because process-liveness/HTTP checks were deliberately never added here (console_settings:
    "keeps the reliability path free of network calls") and nothing else read `resident_state`.
    This reads the same registry the supervisor already keeps, same as `_check_meic`'s job-presence
    check, just for the one job kind (resident) a module-style freshness check can't backstop."""
    from . import supersnap

    settings = cfgmod.console_settings(cfg)
    if not settings["enabled"]:
        return []
    if not _supervisor_driving():
        return []  # pre-cutover box: the console job isn't derived/tracked here yet either
    st = supersnap.job_state("console")
    if st is None:
        return [
            Finding(
                "console.task",
                CRITICAL,
                "Console job missing",
                "Supervisor has no 'console' job. Check config + logs/supervisor.log.",
            )
        ]
    if not st.get("enabled", False):
        return [
            Finding(
                "console.task",
                CRITICAL,
                "Console job disabled",
                f"Supervisor job 'console' is disabled: {st.get('enabled_reason') or 'unknown'}",
            )
        ]
    state = st.get("resident_state")
    if state in ("backoff", "start failed"):
        return [
            Finding(
                "console.task",
                WARN,
                "Console is not running",
                f"Resident state '{state}' — the suite's only read surface is down. Check "
                "logs/supervisor.log and logs/console/.",
            )
        ]
    # "running" is healthy; "idle" never applies (console declares no window); an unset state means
    # the supervisor hasn't evaluated it on this box yet. None of those are worth alarming on.
    return [Finding("console.task", OK, "Console", state or "not yet evaluated")]


# Starts in one window past which a resident job is churning rather than running. Generous on
# purpose: a couple of genuine crashes in a session is bad luck, and this must fire on the shape the
# 2026-08-17 sessions had (49 and 107) rather than on noise.
_RESIDENT_CHURN_STARTS = 6


def _check_resident_health(cfg: dict[str, Any]) -> list[Finding]:
    """Restart churn and unexpected stops across every resident job.

    The gap this closes was found the hard way. On 2026-08-17 `calendars-paper` was killed and
    restarted 107 times and the watchdog reported it `OK — supervised, 0 min old` on every single
    tick. Three reasons, all structural:

    * `_check_console`'s own docstring states the assumption — *"a module's paper loop gets an
      indirect backstop from its own freshness check (no new trade writes eventually goes stale)"*.
      **That is false for a restart loop.** The restart's own `record_iteration` write keeps the
      paper DB fresh, so freshness reads 0 min old precisely BECAUSE the job is thrashing.
    * `resident_state` reads `"running"` on every pass during a storm, so the one signal that was
      being read said nothing.
    * `consecutive_failures` cannot serve either: a clean exit resets it, and a clean exit is the
      storm's own signature (161 spawns beside 0 failures).

    So this reads `starts_in_window`, which the supervisor keeps for exactly this, plus the two
    states that are now silent by design rather than by accident: a job the module stopped early,
    and a job publishing no heartbeat (which is deliberately not silence-supervised — restarting on
    "I can't tell" is the bug that started all of this, so the diagnosis belongs here instead).
    """
    from . import supersnap

    if not _supervisor_driving():
        return []
    findings: list[Finding] = []
    for jid, st in sorted(supersnap.all_job_states().items()):
        if st.get("kind") != jobspec.KIND_RESIDENT or not st.get("enabled", False):
            continue
        starts = int(st.get("starts_in_window") or 0)
        if starts > _RESIDENT_CHURN_STARTS:
            findings.append(
                Finding(
                    f"{jid}.churn",
                    WARN,
                    f"{jid} is restarting repeatedly",
                    f"{starts} starts since its window opened. A supervised job that keeps being "
                    "restarted is not running -- check logs/supervisor.log for the reason "
                    "(silence, or a crash) and the module's own log beside it.",
                )
            )
        # A resident that publishes no heartbeat is not silence-supervised at all. That degrade is
        # deliberate -- the alternative was killing a process nobody can judge, which is the bug
        # this area is recovering from, and refusing to derive the job would take a trading loop
        # down over telemetry. But it is not free, and this is the only place that says so.
        if st.get("silence_file") and not st.get("heartbeat_seen") and st.get("running_pid"):
            findings.append(
                Finding(
                    f"{jid}.heartbeat",
                    WARN,
                    f"{jid} publishes no heartbeat",
                    f"Nothing has been written to {cfgmod.portable_path(Path(st['silence_file']))}, so a "
                    "wedged loop here would never be caught. The module should touch it at the top of "
                    "every tick (cherrypick.core.home.heartbeat_path).",
                )
            )
        # Stopped early by its own exit. The supervisor believes a session-scoped loop that says it
        # is finished (that is what ended the 16:00 respawn storm), so if it said so WRONGLY nothing
        # brings it back until the window reopens -- and this is the only thing that would say so.
        if st.get("module_stopped") and st.get("resident_state") == "module reports session complete":
            findings.append(
                Finding(
                    f"{jid}.stopped",
                    WARN,
                    f"{jid} stopped itself mid-window",
                    "The module exited cleanly and the supervisor is honoring that until its window "
                    "reopens. Expected at the session's end; anything earlier means the loop's own "
                    "gate closed early, or another instance held its lock.",
                )
            )
    return findings


def _file_age_minutes(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return (datetime.now().timestamp() - path.stat().st_mtime) / 60.0
    except OSError:
        return None


def _run_module(module_root: Path, argv: list[str], timeout: int = 25) -> subprocess.CompletedProcess:
    return subprocess.run(
        [cfgmod.python_exe(), *[str(a) for a in argv]],
        cwd=str(module_root),
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _dolt_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except OSError:
        return False


def _streamer_stale_age(status: dict[str, Any]) -> float | None:
    """Seconds since the streamer last received ANY market event, or None if it can't be read.

    Prefers the numeric age the streamer computes itself; falls back to `last_event_at` so a module
    reporting only a timestamp still works.
    """
    for key in ("oldest_event_age_s", "stale_age_s"):
        value = status.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    last = status.get("last_event_at")
    if isinstance(last, str):
        try:
            seen = datetime.fromisoformat(last)
            now = datetime.now(seen.tzinfo) if seen.tzinfo else datetime.now()
            return (now - seen).total_seconds()
        except ValueError:
            return None
    return None


def _streamer_underlying_stale_age(status: dict[str, Any]) -> float | None:
    """Seconds since the freshest SUBSCRIBED UNDERLYING last updated its spot, or None if unreported.

    Distinct from `_streamer_stale_age` (freshest of ANY event): option quotes tick constantly and
    mask a dead underlying-spot feed, so a streamer can look healthy on the global age while every
    underlying's spot has been frozen for hours (the 2026-07-22 stall — spot froze at 10:05 ET, option
    quotes ran to 20:00, nothing restarted). A producer that doesn't report this field degrades cleanly
    to the global-age check.
    """
    value = status.get("underlyings_stale_age_s")
    return float(value) if isinstance(value, (int, float)) else None


def _streamer_chain_fetch_errors(status: dict[str, Any]) -> dict[str, str]:
    """Symbols whose 0DTE chain fetch is currently failing (`stream_symbol_health.chain_fetch_error`
    non-null), or `{}` if unreported/healthy.

    Distinct from `_streamer_stale_age`/`_streamer_underlying_stale_age` (both freshest-of-any-symbol):
    a chain fetch retries in-process for ~2 minutes (`cherrypick.core.streamer`) before giving up and
    disabling that symbol's window — but if it does give up, every OTHER symbol can keep ticking fine
    and mask the dead one from both aggregate ages indefinitely (the 2026-07-31 XSP incident: ~40
    minutes silent, running=true and both aggregate ages fresh, because QQQ/SPX were fine). A producer
    that doesn't report this field degrades cleanly to the aggregate-age checks alone.
    """
    errors = status.get("chain_fetch_errors")
    return errors if isinstance(errors, dict) else {}


def _streamer_dead_underlyings(status: dict[str, Any]) -> dict[str, float]:
    """Union underlyings whose spot has been individually stale past the producer's dead limit
    during regular hours (`stream_trades` age per symbol), or `{}` if unreported/healthy.

    Distinct from `_streamer_underlying_stale_age`, which deliberately takes the FRESHEST
    underlying so one quiet name can't false-trip — the choice that let TQQQ's dead trade
    subscription hide behind a live SPX for four sessions (2026-08-17..21) while pmcc read
    `no_long_chain` and overview's breadth legs froze. The producer self-heals a stale
    subscription at 10 minutes (cherrypick.core.streamer); this field only names a symbol the
    self-heal has already failed to revive (15 min), so a restart is the right medicine by the
    time the watchdog sees it. A producer that doesn't report this field degrades cleanly."""
    dead = status.get("dead_underlyings")
    return dead if isinstance(dead, dict) else {}


def _streamer_stale_detail(
    global_age: float | None,
    underlying_age: float | None,
    limit: int,
    chain_errors: dict[str, str] | None = None,
    dead_underlyings: dict[str, float] | None = None,
) -> str:
    """Name whichever feed(s) are stale, so the alert distinguishes a whole-stream silence from an
    underlying-spot-only stall, a single symbol's dead chain fetch, or a single symbol's dead spot
    subscription (all different causes)."""
    parts = []
    if global_age is not None and global_age > limit:
        parts.append(f"no events for {global_age:.0f}s")
    if underlying_age is not None and underlying_age > limit:
        parts.append(f"underlying spot frozen for {underlying_age:.0f}s")
    if chain_errors:
        named = ", ".join(f"{sym}: {err}" for sym, err in chain_errors.items())
        parts.append(f"chain fetch failing for {named}")
    if dead_underlyings:
        named = ", ".join(f"{sym} {age:.0f}s" for sym, age in dead_underlyings.items())
        parts.append(f"dead spot subscription for {named}")
    return " and ".join(parts) or f"stale (limit {limit}s)"


def _streamer_connection_age(status: dict[str, Any]) -> float | None:
    """Seconds since the current connection was established, so a just-restarted streamer isn't
    judged stale before it has resubscribed."""
    started = status.get("connected_since")
    if not isinstance(started, str):
        return None
    try:
        since = datetime.fromisoformat(started)
    except ValueError:
        return None
    now = datetime.now(since.tzinfo) if since.tzinfo else datetime.now()
    return (now - since).total_seconds()


def _stop_streamer(module_root: Path, streamer: dict[str, Any]) -> bool:
    """Ask a stalled streamer to exit before relaunching.

    Without this the restart is a no-op: the daemon is single-instance guarded, so the new process
    would see the old PID alive and refuse to start, leaving the stall in place.
    """
    stop_argv = streamer.get("stop_argv")
    if not stop_argv:
        status_argv = streamer.get("status_argv") or []
        stop_argv = [*status_argv[:1], "--stop"] if status_argv else None
    if not stop_argv:
        return False
    try:
        _run_module(module_root, stop_argv, timeout=15)
        return True
    except Exception:
        return False


def _start_streamer(module_root: Path, start_argv: list[str]) -> bool:
    """Launch the streamer detached (benign, no-window). Safe: streamer refuses to double-start."""
    try:
        exe = cfgmod.pythonw_exe()
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
        subprocess.Popen(
            [exe, *[str(a) for a in start_argv]],
            cwd=str(module_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- checks
def _check_streamer_health(label: str, root: Path, spec: dict[str, Any]) -> list[Finding]:
    """Liveness + silence-based restart for a market-data streamer.

    The caller session-gates (a streamer is only expected to have fresh events during market hours) and
    supplies the finding `label`; the contract is identical whether the producer is MEIC's own streamer
    (`modules.meic.streamer`) or the standalone producer (top-level `streamer`). Restart on SILENCE, not
    just death — a live socket that has gone quiet still reports running=true. The 2026-07-20 stall: the
    streamer reconnected, then received nothing for 8 minutes while reporting running=true and its own
    stale_warning=false (that flag only trips at 600s). Nothing restarted it; MEIC degraded to REST and
    the flies module refused every iteration on stale quotes. This is the load-bearing bit of the
    walk-away guarantee, so it lives in one place both producers share (a copy would drift).
    """
    findings: list[Finding] = []
    running = None
    status: dict[str, Any] = {}
    try:
        r = _run_module(root, spec["status_argv"], timeout=15)
        if r.returncode == 0:
            status = first_json(r.stdout) or {}
            running = bool(status.get("running"))
    except Exception:
        running = None

    stale_age = _streamer_stale_age(status)
    underlying_age = _streamer_underlying_stale_age(status)
    chain_errors = _streamer_chain_fetch_errors(status)
    dead_underlyings = _streamer_dead_underlyings(status)
    limit = spec.get("stale_restart_seconds", 240)
    # A stall is the whole stream going quiet, OR the underlying-spot feed dying while option quotes
    # keep the global age fresh (2026-07-22), OR a single symbol's chain fetch exhausting its in-process
    # retries while every other symbol stays fresh (2026-07-31), OR one symbol's spot subscription
    # dying mid-flight while others keep every aggregate fresh (2026-08-17, TQQQ) — judge on
    # whichever signal fires.
    stale_candidates = [a for a in (stale_age, underlying_age) if a is not None]
    worst_stale = max(stale_candidates) if stale_candidates else None
    is_stalled = (
        (worst_stale is not None and worst_stale > limit)
        or bool(chain_errors)
        or bool(dead_underlyings)
    )
    detail = _streamer_stale_detail(stale_age, underlying_age, limit, chain_errors, dead_underlyings)
    connection_age = _streamer_connection_age(status)
    # Don't count a connection that has not had time to populate yet — a restart takes a few seconds to
    # resubscribe, and without this the next tick would see stale data and restart again, forever.
    settling = connection_age is not None and connection_age < limit
    if running and is_stalled and not settling and spec.get("auto_restart"):
        _stop_streamer(root, spec)
        started = _start_streamer(root, spec["start_argv"])
        findings.append(
            Finding(
                label,
                WARN,
                "Streamer stalled — restarted" if started else "Streamer stalled — restart failed",
                f"Connected but {detail} (limit {limit}s). "
                + ("Restart issued." if started else "Could not relaunch; quotes stay stale."),
            )
        )
    elif running and is_stalled:
        findings.append(
            Finding(
                label,
                WARN,
                "Streamer stalled",
                f"Connected but {detail} (auto_restart off).",
            )
        )
    elif running is False and spec.get("auto_restart"):
        started = _start_streamer(root, spec["start_argv"])
        findings.append(
            Finding(
                label,
                WARN,
                "Streamer was down — restarted" if started else "Streamer down — restart failed",
                "Auto-restart issued."
                if started
                else "Could not launch streamer; paper GEX/quotes degrade to REST.",
            )
        )
    elif running is False:
        findings.append(
            Finding(
                label,
                WARN,
                "Streamer down",
                "Streamer not running during market hours (auto_restart off).",
            )
        )
    elif running is None:
        findings.append(
            Finding(
                label,
                WARN,
                "Streamer status unknown",
                "Could not read streamer --status; check manually.",
            )
        )
    else:
        findings.append(_recycle_streamer_if_stale(label, root, spec, settling))
    churn = _streamer_churn_finding(label, status)
    if churn is not None:
        findings.append(churn)
    return findings


# How much forward earnings calendar the scanner needs to keep finding candidates. Below this it
# is running out, not out — the distinction that makes the warning actionable rather than a
# post-mortem.
_EARNINGS_CALENDAR_MIN_DAYS = 7


def _check_earnings_calendar(cfg: dict[str, Any]) -> list[Finding]:
    """Warn while the earnings announcement calendar is RUNNING OUT, not after it has.

    The failure this exists for (2026-08-25): the module had not paper traded for eleven sessions
    and nothing anywhere said so. Dolt was up, its tables were full, the loop ticked and the config
    was enabled — but the local clone was 55 commits behind, so the calendar ended on 2026-08-14 and
    the scanner had nothing to scan. It is invisible by nature: "no candidates today" and "a quiet
    earnings week" are the same observation, and this module produces no ledger row either way.

    Reads the state file `scripts/refresh_dolt_data.py` writes, never Dolt itself — the reliability
    path is stdlib and files only, and adding a MySQL driver to it would put a network client on the
    one path that must never have one.
    """
    if not (cfg.get("modules", {}).get("earnings") or {}).get("enabled"):
        return []
    raw = _read_json_file(cfgmod.state_file("dolt_data.json"))
    if raw is None:
        return [
            Finding(
                "earnings.calendar",
                WARN,
                "Earnings calendar coverage unknown",
                "No state/dolt_data.json — the earnings-dolt-pull job has never completed, so "
                "nothing is refreshing the announcement calendar the scanner reads.",
            )
        ]
    max_date = raw.get("earnings_calendar_max_date")
    if not isinstance(max_date, str):
        return [
            Finding(
                "earnings.calendar",
                WARN,
                "Earnings calendar unreadable",
                "The last pull could not read earnings_calendar; a calendar nothing can query is "
                "as useless to the scanner as an empty one.",
            )
        ]
    try:
        remaining = (datetime.fromisoformat(max_date).date() - timeutil.now_et().date()).days
    except ValueError:
        return []
    if remaining < _EARNINGS_CALENDAR_MIN_DAYS:
        return [
            Finding(
                "earnings.calendar",
                WARN,
                "Earnings calendar running out",
                f"The announcement calendar reaches only {max_date} ({remaining} day(s) ahead). "
                "Past it the scanner finds nothing and the module stops trading SILENTLY — no "
                "error, no ledger row. Run scripts/refresh_dolt_data.py or check the "
                "earnings-dolt-pull job.",
            )
        ]
    return [
        Finding(
            "earnings.calendar",
            OK,
            "Earnings calendar",
            f"covers to {max_date} ({remaining} days ahead).",
        )
    ]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _streamer_window_strike_count(cfg: dict[str, Any]) -> int:
    """The producer's base ATM window width, from ITS OWN config — the file the daemon reads, not a
    copy. Falls back to the engine's default when the machine has no streamer config."""
    try:
        raw = json.loads(core_home.config_path("streamer").read_text(encoding="utf-8"))
        value = ((raw.get("streamer") or {}).get("window_strike_count"))
        if isinstance(value, (int, float)):
            return int(value)
    except (OSError, ValueError, AttributeError):
        pass
    return int(((cfg.get("streamer") or {}).get("window_strike_count")) or 60)


def _check_subscription_budget(cfg: dict[str, Any]) -> list[Finding]:
    """Warn when the DECLARED registry would cost a producer more than the suite's ceiling.

    The 2026-08-24 outage was discovered at an open, in production: a module had declared something
    expensive and nothing said so until the producer was already crash-looping and every trading
    module was starving. `cherrypick.core.streamer`'s pacing removed that failure mode; this exists
    so the next expensive declaration announces itself when it is DECLARED.

    Estimated from the same registry union the producer subscribes from, so the two cannot disagree
    about what was asked for — and reported with the single most expensive symbol named, because a
    total alone does not say which declaration to look at (that morning's book was dominated by one
    symbol's widened window). Report-only: what to shed is a measurement decision, not the
    watchdog's to make.
    """
    budget = int(
        (cfg.get("streamer") or {}).get("subscription_budget")
        or _streamrequests.DEFAULT_SUBSCRIPTION_BUDGET
    )
    try:
        status = _streamrequests.budget_status(
            default_strike_count=_streamer_window_strike_count(cfg), budget=budget
        )
    except Exception:  # noqa: BLE001 — a registry read must never fail the tick
        return []
    if not status["over"]:
        return [
            Finding(
                "streamer.budget",
                OK,
                "Subscription budget",
                f"~{status['total']:,} of {status['budget']:,} across {status['windows']} window(s).",
            )
        ]
    worst = status["worst"] or {}
    return [
        Finding(
            "streamer.budget",
            WARN,
            "Declared subscriptions over budget",
            f"The registry would cost ~{status['total']:,} subscriptions against a budget of "
            f"{status['budget']:,} ({status['windows']} windows). Largest: {worst.get('symbol')} at "
            f"~{worst.get('subscriptions', 0):,} over {worst.get('windows')} window(s) at "
            f"{worst.get('strike_count')} strikes/side"
            + (" (widened by a module hint)" if worst.get("hinted") else "")
            + ". A producer restart would subscribe this; shed before it does.",
        )
    ]


# A producer reconnecting more than this often is churning rather than recovering. A healthy day is
# 0-2 reconnects; 2026-08-24's rate-limit spiral ran ~60/hour for a whole morning while every branch
# above reported the streamer as running and, between kills, streaming.
_RECONNECT_CHURN_PER_HOUR = 6.0
_RECONNECT_CHURN_MIN_DELTA = 3


def _streamer_churn_state_path() -> Path:
    return cfgmod.state_file("streamer-reconnects.json")


def _streamer_churn_finding(label: str, status: dict[str, Any]) -> Finding | None:
    """WARN when a producer is RECONNECTING repeatedly — the state no other check can see.

    Every branch above judges one instant: is it running, is its data fresh. A producer in a
    reconnect loop passes all of them between kills, which is exactly how 2026-08-24 went
    unreported — 79 reconnects in a morning, and the only symptoms that surfaced were two trading
    modules complaining about stale quotes, a diagnosis one level removed from the cause. This is
    the same lesson `starts_in_window` already encodes for resident jobs: **a process that keeps
    coming back looks healthy at every instant and is not.**

    Report-only, deliberately. Churn means the producer is ALREADY restarting itself, so a restart
    is not a remedy — it is another instance of the problem (the suite's own rule that ambiguity is
    reported, never remediated, and that restart is the most expensive remedy there is).
    """
    count = status.get("reconnect_count")
    if not isinstance(count, (int, float)):
        return None
    now = datetime.now(timezone.utc).timestamp()
    try:
        state = json.loads(_streamer_churn_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    prev = state.get(label) or {}
    finding = None
    prev_count, prev_at = prev.get("count"), prev.get("at")
    if isinstance(prev_count, (int, float)) and isinstance(prev_at, (int, float)):
        delta = count - prev_count
        hours = max((now - prev_at) / 3600.0, 1e-6)
        rate = delta / hours
        # A counter that went DOWN means the daemon restarted and reset it — not churn, and not a
        # comparable baseline, so it only re-baselines.
        if delta >= _RECONNECT_CHURN_MIN_DELTA and rate >= _RECONNECT_CHURN_PER_HOUR:
            finding = Finding(
                f"{label}.churn",
                WARN,
                "Streamer reconnect churn",
                f"{int(delta)} reconnect(s) in {(now - prev_at) / 60:.0f} min "
                f"(~{rate:.0f}/hour, total {int(count)}). It is restarting itself, so quotes are "
                "arriving in bursts even while it reports running; check the streamer log for the "
                "reason before restarting anything.",
            )
    try:
        state[label] = {"count": count, "at": now}
        _streamer_churn_state_path().write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # telemetry about telemetry: never fail the tick over it
    return finding


def _recycle_streamer_if_stale(label: str, root: Path, spec: dict[str, Any], settling: bool) -> Finding:
    """A streamer that is up and streaming, but on config — or a subscription set — from before the
    last edit.

    Two ways to be stale, same file-versus-process gap (see servicecfg): the config file moved under a
    process that read it once at launch, or a module's stream request now names an underlying this
    process never subscribed (underlyings bind once, when the streamer is built; legs do not — those
    are re-read every poll and need no restart).

    Reached only from the healthy branch, so the stall path always wins: a streamer that is silent is
    restarted for silence, and its config gets stamped by that restart anyway. `settling` is honoured
    for the same reason it exists on the stall path — a streamer restarted seconds ago has not had
    time to resubscribe, and recycling it again would be the start of a loop.

    Producers are stamped under the finding label, so the standalone producer and a module's own
    streamer keep separate stamps and cannot recycle each other during a cutover.
    """
    healthy = Finding(label, OK, "Streamer", "running")
    if settling:
        return healthy
    try:
        state = servicecfg.staleness(spec, root, label, check_subscriptions=True)
    except Exception:  # never fail the tick over a stale check
        return healthy

    subs = state.get("subscriptions")
    if state["adopt"]:
        servicecfg.write_stamp(label, state["hash"], state["source"], subs)
        return healthy
    if not state["stale"]:
        return healthy

    if state.get("kind") == "subscriptions":
        why = state["reason"]
        headline = "subscriptions"
    else:
        why = f"config changed since launch ({state.get('source') or 'streamer config'})"
        headline = "config"
    if not spec.get("auto_restart"):
        return Finding(
            label,
            WARN,
            f"Streamer running stale {headline}",
            f"Streamer {why}; auto_restart is off, so restart it by hand.",
        )
    stopped = _stop_streamer(root, spec)
    started = _start_streamer(root, spec["start_argv"]) if stopped else False
    if started:
        servicecfg.write_stamp(label, state["hash"], state["source"], subs)
        return Finding(
            label,
            WARN,
            f"Streamer recycled onto new {headline}",
            f"Streamer {why}; stopped and restarted so it picks them up.",
        )
    return Finding(
        label,
        WARN,
        f"Streamer stale {headline} — recycle failed",
        f"Streamer {why} but the {'restart' if stopped else 'stop'} failed; "
        "it is still producing on the old one.",
    )


def _check_producer(cfg: dict[str, Any], in_session: bool) -> list[Finding]:
    """Watchdog the standalone market-data streamer (the suite's sole producer) when it is configured as
    a top-level `streamer` block.

    Dormant unless that block exists and is enabled — until the cutover, MEIC still owns the streamer
    under `modules.meic.streamer` and this returns nothing. Exactly one producer is ever enabled at a
    time (enabling this + `modules.meic.streamer.enabled=false` is the flip). Session-gated + silence
    restart, the same contract MEIC's streamer has via `_check_streamer_health`.

    Off-session it still emits one informational OK finding (liveness only — `--status` reads files and
    the PID, never the broker) so the log and dashboard always say what the producer is doing; without
    it, "overall OK" on a weekend is indistinguishable from the streamer never being checked at all.
    Never WARN and never restart off-session: there is nothing to stream, staleness is meaningless, and
    the first in-session tick (from NEAR_OPEN, 9:15 ET) auto-starts it before the bell.
    """
    spec = cfg.get("streamer") or {}
    if not spec.get("enabled"):
        return []
    root = cfgmod.module_root(spec, "streamer")
    if not root.exists():
        msg = f"not found at {cfgmod.portable_path(root)}"
        if not in_session:
            return [Finding("streamer", OK, "streamer", f"off-hours; checkout {msg}")]
        return [Finding("streamer", WARN, "streamer checkout missing", msg)]
    if not in_session:
        running = None
        try:
            r = _run_module(root, spec["status_argv"], timeout=15)
            if r.returncode == 0:
                running = bool((first_json(r.stdout) or {}).get("running"))
        except Exception:
            running = None
        if running is None:
            msg = "off-hours; status unreadable — will be checked at the next in-session tick"
        elif running:
            msg = "running (off-hours; staleness not checked)"
        else:
            msg = "not running (off-hours; auto-start at the first in-session tick, 9:15 ET)"
        return [Finding("streamer", OK, "Streamer", msg)]
    return _check_streamer_health("streamer", root, spec)


def _check_meic(name: str, mcfg: dict[str, Any], in_session: bool) -> list[Finding]:
    """Health checks for a `self_healing` module (one that registers its own recurring task).

    Alert text is built from the module NAME rather than hardcoded to MEIC. It was hardcoded, which
    was harmless while MEIC was the only self_healing module and actively misleading once a second
    one existed — a missing flies task raised a CRITICAL titled for MEIC, pointing the operator at
    the wrong module entirely. Same fault as the SLA heartbeat naming, different function.
    """
    findings: list[Finding] = []
    root = cfgmod.module_root(mcfg)
    paper = mcfg.get("paper", {})
    label = name.upper() if len(name) <= 4 else name.capitalize()

    # (a) the paper loop has a driver. Supervisor-driven boxes check the job registry (the file the
    # supervisor maintains); pre-cutover boxes fall back to the scheduled task — dual-read until the
    # transition window closes.
    task_name = paper.get("task_name")
    if _supervisor_driving():
        st = _supervisor_job(f"{name}-paper")
        if st is None:
            findings.append(
                Finding(
                    f"{name}.task",
                    CRITICAL,
                    f"{label} paper job missing",
                    f"Supervisor has no '{name}-paper' job. Check config + logs/supervisor.log.",
                )
            )
        elif not st.get("enabled", False):
            findings.append(
                Finding(
                    f"{name}.task",
                    CRITICAL,
                    f"{label} paper job disabled",
                    f"Supervisor job '{name}-paper' is disabled: {st.get('enabled_reason') or 'unknown'}",
                )
            )
        else:
            findings.append(Finding(f"{name}.task", OK, f"{label} paper job", "supervised"))
    elif task_name and not tasks.exists(task_name):
        findings.append(
            Finding(
                f"{name}.task",
                CRITICAL,
                f"{label} paper task missing",
                f"Scheduled task '{task_name}' is not registered. Run: cherrypick install",
            )
        )
    else:
        findings.append(Finding(f"{name}.task", OK, f"{label} paper task", "registered"))

    # (b) freshness during the session. The log lives in the shared logs home
    # (~/.cherrypick/logs/<name>/), NOT the module checkout -- config declares it
    # checkout-relative for historical reasons, so resolve the basename against
    # module_logs_dir exactly like the dashboard does. Resolving against `root`
    # silently misses (the old bug: _file_age_minutes returns None for a missing
    # file, so half the freshness signal was dead with no error).
    if in_session:
        log_rel = paper.get("log")
        # Three candidate signals, freshest wins. The DB mtime and the log are both conditional
        # writes — in WAL mode the main DB file's mtime only moves on a checkpoint, and a log line
        # is a side effect of having something to say — so a healthy-but-idle loop (calendars, most
        # weeks: no positions, nothing to mark, nothing to log) looked dead through both and this
        # check flapped WARN for most of 2026-08-21's session. The heartbeat is the one file a
        # resident loop writes UNCONDITIONALLY every tick (cherrypick.core.home.heartbeat_path — the
        # same lesson the supervisor learned from calendars' 107 restarts on 2026-08-17); a module
        # that doesn't write one degrades cleanly to the two conditional signals.
        ages = [
            a
            for a in (
                _file_age_minutes(cfgmod.paper_db_path(mcfg, name)) if paper.get("paper_db") else None,
                _file_age_minutes(cfgmod.module_logs_dir(name) / Path(log_rel).name) if log_rel else None,
                _file_age_minutes(core_home.heartbeat_path(name)),
            )
            if a is not None
        ]
        fresh_limit = paper.get("freshness_minutes", 20)
        if not ages:
            findings.append(
                Finding(
                    f"{name}.fresh",
                    WARN,
                    f"{label} paper has no output yet",
                    "No paper DB or log file found during market hours.",
                )
            )
        elif min(ages) > fresh_limit:
            findings.append(
                Finding(
                    f"{name}.fresh",
                    WARN,
                    f"{label} paper data is stale",
                    f"No paper write in {min(ages):.0f} min (limit {fresh_limit}). Is the task running?",
                )
            )
        else:
            findings.append(Finding(f"{name}.fresh", OK, f"{label} paper fresh", f"{min(ages):.0f} min old"))
    else:
        findings.append(Finding(f"{name}.fresh", OK, f"{label} paper", "off-hours (freshness not checked)"))

    # (c) streamer liveness (session only); benign auto-restart
    streamer = mcfg.get("streamer", {})
    if streamer.get("enabled") and in_session:
        findings += _check_streamer_health(f"{name}.streamer", root, streamer)
    return findings


def _check_earnings(name: str, mcfg: dict[str, Any], now_et: datetime, is_trading: bool) -> list[Finding]:
    findings: list[Finding] = []
    paper = mcfg.get("paper", {})

    # (a) entry/exit have a driver (supervisor job registry, or the scheduled tasks pre-cutover).
    #
    # Only for the two-daily-jobs shape. A self-healing earnings module runs ONE continuous job
    # (`<name>-paper`, checked by the generic module coverage) whose loop does entry and exit from
    # its own clock — so looking up `earnings-entry` there would raise a CRITICAL for a job that is
    # correctly absent, every tick. entry_task_name/exit_task_name are kept in config for deleting
    # the pre-cutover scheduled tasks by name, so their presence is not the discriminator; the kind
    # is.
    if paper.get("kind") == "cherrypick_scheduled":
        findings.extend(_check_scheduled_entry_exit_jobs(name, paper))

    # (b) Dolt reachability (only meaningful on trading days)
    findings.extend(_check_earnings_dolt(name, paper, is_trading))
    # (c) entry SLA — unchanged by the cutover: the loop writes the same heartbeat files.
    findings.extend(_check_earnings_entry_sla(name, mcfg, paper, now_et, is_trading))
    return findings


def _check_scheduled_entry_exit_jobs(name: str, paper: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for tkey, label in (("entry_task_name", "entry"), ("exit_task_name", "exit")):
        tn = paper.get(tkey)
        if not tn:
            continue
        if _supervisor_driving():
            st = _supervisor_job(f"{name}-{label}")
            if st is None or not st.get("enabled", False):
                why = "missing" if st is None else f"disabled: {st.get('enabled_reason') or 'unknown'}"
                findings.append(
                    Finding(
                        f"{name}.task.{label}",
                        CRITICAL,
                        f"Earnings {label} job {why.split(':')[0]}",
                        f"Supervisor job '{name}-{label}' is {why}. Check config + logs/supervisor.log.",
                    )
                )
            else:
                findings.append(Finding(f"{name}.task.{label}", OK, f"Earnings {label} job", "supervised"))
        elif not tasks.exists(tn):
            findings.append(
                Finding(
                    f"{name}.task.{label}",
                    CRITICAL,
                    f"Earnings {label} task missing",
                    f"Scheduled task '{tn}' is not registered. Run: cherrypick install",
                )
            )
        else:
            findings.append(Finding(f"{name}.task.{label}", OK, f"Earnings {label} task", "registered"))
    return findings


def _check_earnings_dolt(name: str, paper: dict[str, Any], is_trading: bool) -> list[Finding]:
    findings: list[Finding] = []
    if paper.get("requires_dolt") and is_trading:
        if _dolt_reachable(paper.get("dolt_host", "127.0.0.1"), paper.get("dolt_port", 3306)):
            findings.append(Finding(f"{name}.dolt", OK, "Dolt server", "reachable"))
        else:
            findings.append(
                Finding(
                    f"{name}.dolt",
                    WARN,
                    "Dolt server unreachable",
                    "EarningsAgent paper entry self-starts Dolt, but a persistent outage will block entries.",
                )
            )

    return findings


def _check_earnings_entry_sla(
    name: str, mcfg: dict[str, Any], paper: dict[str, Any], now_et: datetime, is_trading: bool
) -> list[Finding]:
    """After entry_time+grace on a trading day, the entry run must have happened.

    Unchanged by the lifecycle cutover: the loop writes the same heartbeat files the scheduled verb
    used to, so the file and its shape are the contract rather than who writes it.

    The grace matters: the scan starts AT entry_time and may run for many minutes, and the
    heartbeat is only written when it returns — so a comparison against entry_time alone raised
    CRITICAL for a run that was simply still in progress (the same false-alarm class
    _check_settlement's grace fixed).
    """
    findings: list[Finding] = []
    if is_trading and paper.get("entry_time"):
        try:
            eh, em = [int(x) for x in paper["entry_time"].split(":")]
            grace = int(paper.get("entry_sla_grace_minutes", 35))
            deadline = now_et.replace(hour=eh, minute=em, second=0, microsecond=0) + timedelta(minutes=grace)
            grace_passed = now_et >= deadline
        except Exception:
            grace_passed = False
        if grace_passed:
            # Heartbeat path and alert wording both derive from the module name. They were hardcoded
            # to Earnings, which was invisible while Earnings was the only scheduled module and
            # actively misleading once a second one existed: another module's missed run would raise
            # a CRITICAL reading "Earnings paper entry did not run".
            entry_state, _ = cfgmod.sla_state_files(name, mcfg)
            hb = _read_heartbeat(entry_state)
            today = now_et.strftime("%Y-%m-%d")
            label = f"{name.capitalize()} paper entry"
            log_hint = paper.get("log") or f"{name}_paper.log"
            if hb.get("date") != today:
                findings.append(
                    Finding(
                        f"{name}.entry_sla",
                        CRITICAL,
                        f"{label} did not run",
                        f"No successful entry heartbeat for {today} after {paper['entry_time']} ET.",
                    )
                )
            elif not hb.get("ok", False):
                findings.append(
                    Finding(
                        f"{name}.entry_sla",
                        WARN,
                        f"{label} reported an error",
                        f"Last entry: {hb.get('error') or f'see logs/{log_hint}'}",
                    )
                )
            else:
                findings.append(Finding(f"{name}.entry_sla", OK, label, "ran today"))
    return findings


_read_heartbeat = util.read_json  # one implementation of the best-effort JSON read


# --------------------------------------------------------------------------- drawdown (drift) alert
def _drawdown_finding(key: str, label: str, net: float, floor: float, crit_mult: float) -> Finding | None:
    """WARN when net P&L breaches `floor`; CRITICAL when it breaches floor*crit_mult. None if healthy."""
    if net <= floor * crit_mult:
        status = CRITICAL
    elif net <= floor:
        status = WARN
    else:
        return None
    return Finding(
        key,
        status,
        f"{label} paper drawdown",
        f"Net paper P&L {net:+.2f} at/below alert floor {floor:+.2f} "
        f"(critical below {floor * crit_mult:+.2f}).",
    )


def _check_drawdown(cfg: dict[str, Any]) -> list[Finding]:
    """Report-driven drawdown alert. Opt-in via cfg['watchdog']['drawdown']; file-only, never trades."""
    dd = cfg.get("watchdog", {}).get("drawdown") or {}
    if not dd:
        return []
    from . import report  # local import: report is read-only and only needed when the alert is on

    try:
        rep = report.run(cfg)
    except Exception:
        return []  # a report hiccup must never break the reliability path

    findings: list[Finding] = []
    crit_mult = dd.get("critical_multiplier", 2)

    suite = rep.get("suite", {})
    if dd.get("suite_floor") is not None and suite.get("trades"):
        f = _drawdown_finding(
            "drawdown.suite", "Suite", suite.get("net_pnl", 0.0), dd["suite_floor"], crit_mult
        )
        if f:
            findings.append(f)

    for name, floor in (dd.get("module_floors") or {}).items():
        m = rep.get("modules", {}).get(name, {})
        if floor is not None and m.get("ok") and m.get("trades"):
            f = _drawdown_finding(f"drawdown.{name}", name, m.get("net_pnl", 0.0), floor, crit_mult)
            if f:
                findings.append(f)
    return findings


# --------------------------------------------------------------------------- state + notify
def _load_state() -> dict[str, Any]:
    return _read_heartbeat(_STATE_FILE) or {}


def _save_state(state: dict[str, Any]) -> None:
    cfgmod.ensure_dirs()
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _parse_hhmm(value: str, default: time) -> time:
    try:
        h, m = (int(x) for x in str(value).split(":", 1))
        return time(h, m)
    except (ValueError, TypeError):
        return default


def _check_eval_activity(
    name: str, mcfg: dict[str, Any], now_et: datetime, in_session: bool, settings: dict[str, Any]
) -> list[Finding]:
    """Is the loop actually EVALUATING candidates (not just writing a file), and is it deciding sensibly?

    Freshness only proves the loop ran; this proves it did meaningful work. Rejecting every candidate is
    HEALTHY (a legit gate — e.g. MEIC on regime_gex_negative), so this WARNs only on: stopped evaluating
    mid-session, iterating-but-evaluating-nothing, evals dominated by errors, or 0 entries for a reason
    that isn't a known gate. Session-gated and schema-keyed — returns nothing for a module with no reader
    (earnings, event-driven). Reads the paper DB read-only; off the notify path's stdlib-only floor."""
    if not in_session:
        return []
    act = eval_activity.for_module(mcfg, name, now_et.date().isoformat(), settings["window_minutes"])
    if act is None:
        return []
    label = name.upper() if len(name) <= 4 else name.capitalize()
    status, detail = eval_activity.assess(
        act,
        window_min=settings["window_minutes"],
        eval_stale_min=settings["stale_minutes"],
        error_frac_warn=settings["error_fraction"],
    )
    finding = WARN if status == eval_activity.WARN else OK
    return [Finding(f"{name}.eval_activity", finding, f"{label} eval activity", detail)]



def _check_advice_enactment(cfg: dict[str, Any], now_et: datetime, is_trading: bool) -> list[Finding]:
    """Did today's modules actually APPLY the advice artifact issued for them last night?

    This is the check that replaced a scheduled AI checkpoint (2026-08-26). `advice_enacted` rides
    on every fact pack partly so a dropped artifact is visible at 10am rather than in the evening
    verdict that scores it — but a model was never needed to answer it, and across its whole history
    the midday slot never once caught one. All three enactment failures found so far (08-25 meic,
    08-25 earnings, 08-26 calendars) were found by the deep slot, after the close. The question is
    deterministic, so it belongs here, where it costs nothing and fails the same way twice.

    It matters because of the 2026-08-25 incident: five artifacts went out with zero rejections,
    three were applied and two were not, and the two were the modules whose experiments had their
    most informative session available. A `not_enacted` session buys an experiment nothing, and
    earnings carries a kill-at-session-6 rule — so "the parameter did nothing" and "the parameter
    never reached a session with trades" would have concluded identically.

    Driven by SUBPROCESS, never by import: this package drives the advisor by subprocess by rule
    (see jobspec's ADVISOR_LIGHT_SLOTS note and packages/advisor/CLAUDE.md's fence), and the verdict
    stays in `enactment.py` rather than being re-derived here. The comparison is genuinely subtle —
    a reject-all artifact beside a baseline decision IS enacted — and a second opinion here would be
    free to drift from the one the console and the evening pass both read.

    Silent unless something is wrong. `no_artifact` is the ordinary state of a module with no active
    experiment and is not reported; only `not_enacted` is, because that is an artifact that existed,
    validated, and was ignored.
    """
    if not is_trading or not cfgmod.advisor_settings(cfg).get("enabled"):
        return []
    # After the loops have had a session start in which to record a decision, and before the deep
    # slot scores it anyway — a warning that arrives with the verdict is not an early warning.
    if not (time(10, 30) <= now_et.time() <= time(16, 30)):
        return []

    session = now_et.date().isoformat()
    try:
        # cwd is irrelevant to `-m` resolution (the advisor is an installed package); the home
        # directory is used because it always exists, unlike a module root the advisor has none of.
        r = _run_module(
            core_home.home(),
            ["-m", "cherrypick.advisor", "enactment", "--session", session],
            timeout=25,
        )
        payload = first_json(r.stdout or "")
    except Exception as exc:  # noqa: BLE001 -- a check that cannot run must not break the sweep
        return [
            Finding(
                "advisor.enactment",
                WARN,
                "Advice enactment unknown",
                f"Could not read enactment for {session}: {type(exc).__name__}: {exc}",
            )
        ]
    if not isinstance(payload, dict) or not payload.get("ok"):
        return []

    dropped = [
        (name, (row or {}).get("detail") or "")
        for name, row in (payload.get("modules") or {}).items()
        if isinstance(row, dict) and row.get("status") == "not_enacted"
    ]
    if not dropped:
        return []
    listed = "; ".join(f"{name}: {detail}" for name, detail in sorted(dropped))
    return [
        Finding(
            "advisor.enactment",
            WARN,
            f"Advice not applied by {len(dropped)} module(s)",
            f"An artifact was issued for {session} and the loop's own record disagrees with it. "
            f"The session buys those experiments nothing and their counters do not advance. "
            f"{listed}",
        )
    ]

def _check_settlement(name: str, mcfg: dict[str, Any], now_et: datetime, is_trading: bool) -> list[Finding]:
    """Warn when a module is past the close on a trading day with open positions it has not settled.

    The freshness check misses this: the loop itself is running fine (writing its DB/log every tick),
    it just cannot settle. The flies 2026-07-22 incident — 5 open 0DTE positions left unsettled for
    9 hours because the market-data feed went stale and settlement refuses a stale price — logged
    "cannot settle" every 2 minutes with zero alert. The module already reports this through its
    `--status` (session_settled / positions_today / data_reason); the watchdog just was not reading it.

    Opt-in via `paper.settlement_check` so it only shells to `--status` for a module that exposes the
    signal (never MEIC's status path). Gated to the module's settle time + a retry buffer (not merely
    the 16:00 close): a module settles a bit after the close (flies at 16:20) and retries for a few
    minutes, so open positions in that window are NOT yet overdue — firing at the close alone
    false-alarmed every day between 16:00 and 16:20.
    """
    paper = mcfg.get("paper", {})
    if not (is_trading and paper.get("settlement_check") and paper.get("status_argv")):
        return []
    grace = int(paper.get("settlement_grace_minutes", 30))  # past settle time (~16:20) + retries
    close_min = timeutil.MARKET_CLOSE.hour * 60 + timeutil.MARKET_CLOSE.minute
    if now_et.hour * 60 + now_et.minute < close_min + grace:
        return []
    label = name.upper() if len(name) <= 4 else name.capitalize()
    try:
        r = _run_module(cfgmod.module_root(mcfg), paper["status_argv"], timeout=15)
        status = first_json(r.stdout) if r.returncode == 0 else None
    except Exception:
        status = None
    # Can't read the signal — say nothing (don't fire, don't clear a prior alert).
    if not status or "session_settled" not in status or "positions_today" not in status:
        return []
    open_count = status.get("positions_today") or 0
    if status.get("session_settled") is False and open_count > 0:
        reason = status.get("data_reason") or "settlement price unavailable"
        return [
            Finding(
                f"{name}.settle_overdue",
                WARN,
                f"{label} settlement overdue",
                f"{open_count} open position(s) past the close still unsettled ({reason}).",
            )
        ]
    return [Finding(f"{name}.settle_overdue", OK, f"{label} settlement", "settled or no open positions")]


_TASK_STILL_RUNNING = 267009  # SCHED_S_TASK_RUNNING (0x41301) -- an in-progress tick, not a failure


def _scheduler_age_minutes(info: dict[str, Any], now_et: datetime) -> float | None:
    """Minutes since the scheduler's own reported last run, or None if unparseable.

    `last_run_time` is in the SCHEDULER's local zone (whatever the machine runs, e.g. Mountain),
    which is generally not the same zone as `now_et` (Eastern) — both are timezone-AWARE in
    production, so the subtraction is correct regardless of the offset difference. The
    aware/naive fallback below only matters for tests that pass a naive `now_et` fixture.
    """
    last_run = info.get("last_run_time")
    if not isinstance(last_run, str):
        return None
    try:
        ts = datetime.fromisoformat(last_run)
    except ValueError:
        return None
    now = now_et
    if ts.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=ts.tzinfo)
    elif ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    return (now - ts).total_seconds() / 60


def _check_live(name: str, mcfg: dict[str, Any], now_et: datetime, in_session: bool) -> list[Finding]:
    """Watchdog the module's LIVE loop — active only when the module config carries a `live`
    block with a `task_name`.

    Three checks plus the disarm backstop:
      (a) armed-window task check: the live loop self-disarms after `disarm_time`, so a missing
          task is only CRITICAL while the module's own status says it should be armed.
      (b) freshness in-session (CRITICAL) — a registered-but-silent live loop is the dangerous
          state, since real working orders may be resting unwatched. On Windows this reads the
          scheduler's OWN last-run record (`tasks.last_run_info`) rather than a log file's mtime —
          a live tick that legitimately has nothing to log (e.g. two already-completed, risk-free
          positions sitting quiet for 30+ minutes) used to read as "silent" and false-alarm; the
          task having *run* is what matters, not whether it *logged* anything. POSIX cron has no
          run-history to query, so it falls back to the log-mtime check `last_run_info` replaces.
      (c) settle-overdue after close + grace, via the module's live `--status` JSON.
      (d) disarm backstop (the dead-man's switch's second layer): far outside market hours, a
          live task STILL registered means self-disarm failed — the watchdog sets the suite
          halt flag (its own liveops surface; every live tick then refuses at readiness) and
          raises CRITICAL. Purely risk-reducing: it never touches the module's task itself.
    """
    live = mcfg.get("live") or {}
    task_name = live.get("task_name")
    if not task_name:
        return []
    label = name.upper() if len(name) <= 4 else name.capitalize()
    findings: list[Finding] = []

    status = None
    if live.get("status_argv"):
        try:
            r = _run_module(cfgmod.module_root(mcfg), live["status_argv"], timeout=15)
            status = first_json(r.stdout) if r.returncode == 0 else None
        except Exception:
            status = None

    # The armed SIGNAL, per driver. Post-cutover (a supervisor heartbeat exists on this box) the
    # module's arm record is authoritative — the same file that enables the supervisor's
    # `<name>-live` job — with the legacy task registration still honored as a transition-window
    # belt-and-braces (a stale schtasks entry must still trip the backstop). Pre-cutover it is the
    # schtasks registration, exactly as before. Checked record-first so the disarmed steady state
    # costs no schtasks spawn on a supervisor box.
    from . import supersnap, supervisor

    hb_exists = supervisor.heartbeat_path().exists()
    sup_alive = _supervisor_driving()
    arm_rec = util.read_json(supervisor.arm_record_path(name), default=None) or None
    if hb_exists:
        registered = bool(arm_rec) or tasks.exists(task_name)
        armed_desc = "arm record present" if arm_rec else f"task '{task_name}' registered"
    else:
        registered = tasks.exists(task_name)
        armed_desc = f"task '{task_name}' registered"
    armed_for = (status or {}).get("armed_for")
    today = now_et.date().isoformat()

    # A dead supervisor while live is armed is a SILENT live loop — no ticks are being fired, no
    # stops are being watched. Strictly stronger than any freshness read: raise it before anything
    # else (except that the disarm backstop below still runs on its own signal).
    supervisor_down_while_armed = (
        hb_exists and not sup_alive and (str((arm_rec or {}).get("date")) == today or armed_for == today)
    )
    if supervisor_down_while_armed:
        findings.append(
            Finding(
                f"{name}.live_supervisor",
                CRITICAL,
                f"{label} LIVE armed but the supervisor is down",
                "The arm record says live is armed for today, but the supervisor heartbeat is "
                "stale — no live ticks are being fired and resting orders are unwatched. "
                "ensure-supervisor should restart it within ~2 min; if not, disarm "
                "(--uninstall-task) and flatten manually.",
            )
        )

    # (d) Disarm backstop first — it's the check that must never be skipped.
    disarm_hhmm = str(live.get("disarm_time", "17:00"))
    try:
        h, m = disarm_hhmm.split(":")
        disarm_minute = int(h) * 60 + int(m)
    except ValueError:
        disarm_minute = 17 * 60
    grace = int(live.get("disarm_grace_minutes", 30))
    now_minute = now_et.hour * 60 + now_et.minute
    past_disarm_window = now_minute >= disarm_minute + grace or (armed_for and armed_for != today)
    if registered and past_disarm_window:
        from . import liveops

        flag = liveops.halt_flag_path()
        try:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        except OSError:
            pass
        findings.append(
            Finding(
                f"{name}.live_disarm",
                CRITICAL,
                f"{label} LIVE armed signal survived past disarm",
                f"{armed_desc} past {disarm_hhmm}+{grace}m (armed_for={armed_for}); "
                "halt flag set — live ticks now refuse. Investigate why self-disarm failed, then "
                "disarm (--uninstall-task removes the arm record and any legacy task) and clear "
                "the halt flag before re-arming.",
            )
        )
        return findings  # halted state — the armed-window checks below would only add noise

    if registered:
        # (b) freshness while armed and in session: the ticks must actually be RUNNING recently.
        if in_session and sup_alive:
            # Supervisor-driven: the job registry is the run record — `still_running` is the
            # SCHED_S_TASK_RUNNING equivalent, last_start/last_exit_code replace the scheduler's
            # LastRunTime/LastTaskResult. Same false-alarm posture: a quiet-but-running tick is OK.
            fresh_minutes = int(live.get("freshness_minutes", 5))
            job = supersnap.job_state(f"{name}-live")
            info = supersnap.job_run_info(f"{name}-live")
            if job is None or not job.get("enabled", False):
                why = "missing from the supervisor registry" if job is None else "disabled"
                findings.append(
                    Finding(
                        f"{name}.live_fresh",
                        CRITICAL,
                        f"{label} LIVE job not running",
                        f"Armed, but supervisor job '{name}-live' is {why} — arming did not take. "
                        "Check the arm record and logs/supervisor.log.",
                    )
                )
            elif info is None:
                # enabled but never started: a just-armed loop's first tick lands within one
                # interval — not an alarm state yet; the next watchdog tick re-judges.
                findings.append(
                    Finding(f"{name}.live_fresh", OK, f"{label} live loop", "armed, first tick pending")
                )
            elif info["still_running"]:
                findings.append(Finding(f"{name}.live_fresh", OK, f"{label} live loop", "tick running"))
            else:
                try:
                    started = datetime.fromisoformat(str(info["last_run_time"]))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    age_min = (now_et.astimezone(timezone.utc) - started).total_seconds() / 60
                except ValueError:
                    age_min = None
                failed = info.get("last_exit_code") not in (0, None)
                if age_min is None or age_min > fresh_minutes or failed:
                    if age_min is None:
                        shown = "unparseable last-start time"
                    elif failed:
                        shown = f"last tick {age_min:.0f} min ago, exit={info.get('last_exit_code')}"
                    else:
                        shown = f"last tick {age_min:.0f} min ago"
                    findings.append(
                        Finding(
                            f"{name}.live_fresh",
                            CRITICAL,
                            f"{label} LIVE loop silent",
                            f"supervisor registry reports {shown} while live is armed and the market "
                            "is open — real working orders may be resting unwatched.",
                        )
                    )
                else:
                    findings.append(Finding(f"{name}.live_fresh", OK, f"{label} live loop", "fresh"))
        elif in_session and not supervisor_down_while_armed:
            fresh_minutes = int(live.get("freshness_minutes", 5))
            scheduler_info = tasks.last_run_info(task_name)
            if scheduler_info is not None:
                age_min = _scheduler_age_minutes(scheduler_info, now_et)
                last_result = scheduler_info.get("last_task_result")
                # 267009 (0x41301) is SCHED_S_TASK_RUNNING -- Task Scheduler's sentinel for "this
                # instance hasn't finished yet," reported while a tick is still mid-execution (e.g.
                # a slow broker call). Not a failure; flagging it as one false-CRITICALed a live tick
                # the watchdog simply happened to poll while it was still running.
                failed = last_result not in (0, None, _TASK_STILL_RUNNING)
                if age_min is None or age_min > fresh_minutes or failed:
                    if age_min is None:
                        shown = "unparseable last-run time"
                    elif failed:
                        shown = f"last run {age_min:.0f} min ago, result={last_result}"
                    else:
                        shown = f"last ran {age_min:.0f} min ago"
                    findings.append(
                        Finding(
                            f"{name}.live_fresh",
                            CRITICAL,
                            f"{label} LIVE loop silent",
                            f"scheduler reports {shown} while the live task is armed and the market "
                            "is open — real working orders may be resting unwatched.",
                        )
                    )
                else:
                    findings.append(Finding(f"{name}.live_fresh", OK, f"{label} live loop", "fresh"))
            else:
                # POSIX (or a scheduler query failure): fall back to the log-mtime check.
                log_path = cfgmod.module_logs_dir(name) / str(live.get("log", "flies_live.log"))
                try:
                    age_min = (now_et.timestamp() - os.path.getmtime(log_path)) / 60
                except OSError:
                    age_min = None
                if age_min is None or age_min > fresh_minutes:
                    shown = "missing" if age_min is None else f"{age_min:.0f} min old"
                    findings.append(
                        Finding(
                            f"{name}.live_fresh",
                            CRITICAL,
                            f"{label} LIVE loop silent",
                            f"live log {shown} while the live task is armed and the market is open — "
                            "real working orders may be resting unwatched.",
                        )
                    )
                else:
                    findings.append(Finding(f"{name}.live_fresh", OK, f"{label} live loop", "fresh"))
    elif armed_for == today and now_minute < disarm_minute:
        # (a) status says armed-for-today but the armed signal is gone mid-window.
        missing = "no arm record and no legacy task" if hb_exists else f"{task_name} not registered"
        findings.append(
            Finding(
                f"{name}.live_task",
                CRITICAL,
                f"{label} LIVE armed signal missing",
                f"{missing}, but the module's status says armed for {armed_for}.",
            )
        )

    # (c2) orphaned orders: the last tick's broker-truth sweep found working orders the ledger
    # has never heard of — real orders resting unwatched. Always CRITICAL, session or not.
    if status and (status.get("orphaned_orders") or 0) > 0:
        findings.append(
            Finding(
                f"{name}.live_orphans",
                CRITICAL,
                f"{label} ORPHANED live orders",
                f"{status['orphaned_orders']} working order(s) at the broker unknown to the live "
                "ledger — review in the broker UI before any further arming.",
            )
        )
    elif status and "orphaned_orders" in status:
        findings.append(Finding(f"{name}.live_orphans", OK, f"{label} live orders", "all accounted for"))

    # (c) live settlement overdue: same shape as the paper check, over the live status.
    close_min = timeutil.MARKET_CLOSE.hour * 60 + timeutil.MARKET_CLOSE.minute
    settle_grace = int(live.get("settlement_grace_minutes", 30))
    if status and now_minute >= close_min + settle_grace:
        if status.get("session_settled") is False and (status.get("open_positions") or 0) > 0:
            findings.append(
                Finding(
                    f"{name}.live_settle_overdue",
                    WARN,
                    f"{label} LIVE settlement overdue",
                    f"{status.get('open_positions')} open live position(s) past the close still "
                    "unsettled — run the provisional settle or --settle --price <official>.",
                )
            )
        else:
            findings.append(
                Finding(f"{name}.live_settle_overdue", OK, f"{label} live settlement", "settled or flat")
            )
    return findings


def _log_findings(findings: list[Finding], overall: str) -> None:
    cfgmod.ensure_dirs()
    # Own-log rotation: logrotate refuses active .log files by design, so without this the
    # watchdog's log grew forever (and was re-read on every dashboard render).
    util.rotate_if_large(_WATCHDOG_LOG)
    with _WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": _utcnow(), "overall": overall, "findings": [asdict(f) for f in findings]})
            + "\n"
        )


def _check_services(cfg: dict[str, Any]) -> list[Finding]:
    """Keep the generic background services (e.g. the gex spot-trail recorder) alive: check each one's
    `status_argv` and, if down and `auto_restart`, relaunch it detached. Benign, non-trading remediation
    — the same shape as the streamer's keep-alive. Not session-gated: a service says on its own whether
    it should be up (the recorder is cheap to run all day)."""
    findings: list[Finding] = []
    for svc in cfgmod.enabled_services(cfg):
        sid = svc["id"]
        root = cfgmod.module_root(svc, sid)
        if not root.exists():
            msg = f"not found at {cfgmod.portable_path(root)}"
            findings.append(Finding(f"service.{sid}", WARN, f"{sid} checkout missing", msg))
            continue
        running = None
        status: dict[str, Any] = {}
        try:
            r = _run_module(root, svc["status_argv"], timeout=15)
            status = first_json(r.stdout) if r.returncode == 0 else {}
            running = bool(status.get("running")) if r.returncode == 0 else None
        except Exception:
            running = None
        if running is False and svc.get("auto_restart"):
            started = _start_streamer(root, svc["start_argv"])
            findings.append(
                Finding(
                    f"service.{sid}",
                    WARN,
                    f"{sid} was down — restarted" if started else f"{sid} down — restart failed",
                    "Auto-restart issued." if started else "Could not launch service.",
                )
            )
        elif running is False:
            findings.append(
                Finding(f"service.{sid}", WARN, f"{sid} down", "Service not running (auto_restart off).")
            )
        elif running is None:
            findings.append(
                Finding(f"service.{sid}", WARN, f"{sid} status unknown", "Could not read status_argv.")
            )
        elif status.get("stalled") is True:
            # Alive but wedged: the service's own published heartbeat went silent (see the gex
            # recorder's status contract). A pid check cannot see this — the 2026-07-23 shape with a
            # different cause — and a plain start would lose to the wedged pid's single-instance
            # lock, so the remedy is the stale-config recycle: stop, then start. Same auto_restart
            # gate as every other touch.
            age = status.get("heartbeat_age_seconds")
            silent = f"heartbeat silent {int(age)}s" if isinstance(age, (int, float)) else "heartbeat silent"
            if svc.get("auto_restart"):
                stopped = _stop_streamer(root, svc)
                started = _start_streamer(root, svc["start_argv"]) if stopped else False
                findings.append(
                    Finding(
                        f"service.{sid}",
                        WARN,
                        f"{sid} stalled — recycled" if started else f"{sid} stalled — recycle failed",
                        f"Process alive but {silent}; "
                        + (
                            "stopped and relaunched."
                            if started
                            else f"the {'restart' if stopped else 'stop'} failed and it is still wedged."
                        ),
                    )
                )
            else:
                findings.append(
                    Finding(
                        f"service.{sid}",
                        WARN,
                        f"{sid} stalled",
                        f"Process alive but {silent}; auto_restart is off, so recycle it by hand.",
                    )
                )
        else:
            findings.append(_recycle_if_stale(svc, root, sid))
    return findings


def _recycle_if_stale(svc: dict[str, Any], root: Path, sid: str) -> Finding:
    """A service that is UP but running config from before the last edit gets stopped and started so
    it re-reads. See servicecfg: liveness cannot see this, because nothing is wrong with the process.

    Gated on `auto_restart` — the same permission that lets the watchdog relaunch a service that is
    down. A service the orchestrator may not restart is not one it may recycle either, so a service
    with `auto_restart` off is only reported, never touched.
    """
    try:
        state = servicecfg.staleness(svc, root, sid)
    except Exception:  # a stale-check hiccup must never fail the tick
        return Finding(f"service.{sid}", OK, sid, "running")

    if state["adopt"]:
        # First sighting: record what it is running now so the NEXT change is catchable. Nothing is
        # restarted — with no prior stamp there is no evidence of staleness, only the absence of it.
        servicecfg.write_stamp(sid, state["hash"], state["source"])
        return Finding(f"service.{sid}", OK, sid, "running")
    if not state["stale"]:
        return Finding(f"service.{sid}", OK, sid, "running")

    where = state.get("source") or "service entry"
    if not svc.get("auto_restart"):
        return Finding(
            f"service.{sid}",
            WARN,
            f"{sid} running stale config",
            f"Config changed since launch ({where}); auto_restart is off, so restart it by hand.",
        )

    stopped = _stop_streamer(root, svc)
    started = _start_streamer(root, svc["start_argv"]) if stopped else False
    if started:
        # Stamp only the config the NEW process actually launched with. Stamping a failed recycle
        # would mark the stale process as current and never try again.
        servicecfg.write_stamp(sid, state["hash"], state["source"])
        return Finding(
            f"service.{sid}",
            WARN,
            f"{sid} recycled onto new config",
            f"Config changed since launch ({where}); stopped and restarted so it re-reads.",
        )
    return Finding(
        f"service.{sid}",
        WARN,
        f"{sid} stale config — recycle failed",
        f"Config changed since launch ({where}) but the {'restart' if stopped else 'stop'} failed; "
        "it is still running the old config.",
    )


def _process_notifications(
    findings: list[Finding], notifier: Notifier, renotify_minutes: int, now: datetime | None = None
) -> None:
    state = _load_state()
    now = now or datetime.now(timezone.utc)
    for f in findings:
        prev = state.get(f.key)
        if f.status in (WARN, CRITICAL):
            last_notified = prev and prev.get("last_notified")
            elapsed_ok = True
            if prev and prev.get("status") == f.status and last_notified:
                try:
                    elapsed_ok = (
                        now - datetime.fromisoformat(last_notified)
                    ).total_seconds() >= renotify_minutes * 60
                except ValueError:
                    elapsed_ok = True
            changed = (prev is None) or (prev.get("status") != f.status)
            if changed or elapsed_ok:
                notifier.notify(f.status, f.key, f.title, f.message)
                state[f.key] = {
                    "status": f.status,
                    "first_seen": (prev or {}).get("first_seen", now.isoformat()),
                    "last_notified": now.isoformat(),
                }
            else:
                state[f.key] = {**prev, "status": f.status}
        else:  # OK
            if prev and prev.get("status") in (WARN, CRITICAL):
                notifier.notify("INFO", f.key, f"Recovered: {f.title}", f.message)
            state.pop(f.key, None)
    _save_state(state)


# --------------------------------------------------------------------------- entrypoint
def run_streamer_health(cfg: dict[str, Any] | None = None, *, require_enabled: bool = True) -> dict[str, Any]:
    """The dedicated producer check: streamer liveness only, on its own tight cadence.

    Under the supervisor this runs as the `streamer-health` job — every 60s across the whole
    session (09:00–16:00 ET, trading days), which is strictly broader than the pre-open window the
    old `cherrypick-preopen` task covered. The reasons that task existed all survive here: don't
    multiply the full 10-minute tick's work (module checks, dashboard render, EOD triggers) to
    protect streamer liveness; a streamer that died overnight must be caught before the 09:30–09:35
    opening range, which cannot be reconstructed once missed and needs the 240s settling window
    before quotes are trustworthy again.

    It reuses `_check_streamer_health` rather than copying it: that function carries the 2026-07-20
    silence-restart lesson (a live-but-quiet socket reporting running=true), and a second copy would
    drift from it. Findings go through the same notify path, so a failure here alerts exactly like
    any other. Writes no heartbeat — the full tick owns that, and a second writer would make
    "when did the watchdog last run" ambiguous.
    """
    cfg = cfgmod.load_config() if cfg is None else cfg
    if require_enabled:
        sh = (cfg.get("watchdog", {}) or {}).get("streamer_health", {}) or {}
        if not sh.get("enabled", True):
            return {"ok": True, "skipped": "streamer-health not enabled"}

    tz = cfg.get("timezone", "America/New_York")
    now = timeutil.now_et(tz)
    if not timeutil.is_trading_day(now, timeutil.load_holidays()):
        # The task has no day-of-week filter (see tasks.create_windowed_minute_task), so weekends
        # and holidays reach here and stop here.
        return {"ok": True, "skipped": "not a trading day"}

    findings: list[Finding] = []
    spec = cfg.get("streamer") or {}
    if spec.get("enabled"):
        root = cfgmod.module_root(spec, "streamer")
        if root.exists():
            findings += _check_streamer_health("streamer", root, spec)
    else:
        for name, mcfg in cfgmod.enabled_modules(cfg).items():
            streamer = mcfg.get("streamer") or {}
            if streamer.get("enabled"):
                root = cfgmod.module_root(mcfg, name)
                if root.exists():
                    findings += _check_streamer_health(f"{name}.streamer", root, streamer)

    if not findings:
        return {"ok": True, "skipped": "no streamer configured"}

    overall = OK
    for f in findings:
        if _RANK[f.status] > _RANK[overall]:
            overall = f.status
    _log_findings(findings, overall)
    notifier = Notifier(cfg.get("notify"))
    renotify = cfg.get("watchdog", {}).get("renotify_minutes", 60)
    _process_notifications(findings, notifier, renotify)
    return {
        "ok": True,
        "overall": overall,
        "findings": [{"key": f.key, "status": f.status, "title": f.title} for f in findings],
    }


def run_preopen(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deprecated alias for the pre-supervisor `cherrypick-preopen` task. Honors that task's own
    legacy enable flag, then delegates to `run_streamer_health` — the check itself is identical, so
    a box mid-transition (task still registered, supervisor not yet cut over) behaves exactly as
    before. Retired with the task once the transition window closes."""
    cfg = cfgmod.load_config() if cfg is None else cfg
    if not cfgmod.preopen_settings(cfg)["enabled"]:
        return {"ok": True, "skipped": "preopen not enabled"}
    return run_streamer_health(cfg, require_enabled=False)


def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays()
    now = timeutil.now_et(tz)
    in_session = timeutil.is_session_window(now, holidays)
    is_trading = timeutil.is_trading_day(now, holidays)

    ea_cfg = cfg.get("eval_activity", {})
    ea_settings = {
        "window_minutes": ea_cfg.get("window_minutes", 30),
        "stale_minutes": ea_cfg.get("stale_minutes", 10),
        "error_fraction": ea_cfg.get("error_fraction", 0.5),
    }

    findings: list[Finding] = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        kind = mcfg.get("paper", {}).get("kind")
        try:
            if kind == "self_healing":
                findings += _check_meic(name, mcfg, in_session)
                findings += _check_settlement(name, mcfg, now, is_trading)
                findings += _check_eval_activity(name, mcfg, now, in_session, ea_settings)
            elif kind == "cherrypick_scheduled":
                findings += _check_earnings(name, mcfg, now, is_trading)
            # The live-loop check is kind-independent: any module may declare a `live` block.
            findings += _check_live(name, mcfg, now, in_session)
        except Exception as exc:
            findings.append(
                Finding(
                    f"{name}.error",
                    CRITICAL,
                    f"Watchdog check failed for {name}",
                    f"{type(exc).__name__}: {exc}",
                )
            )

    # The supervisor + its anchor task (skips itself entirely on a pre-cutover box).
    try:
        findings += _check_supervisor(cfg)
    except Exception as exc:
        findings.append(
            Finding("supervisor.error", WARN, "Supervisor check failed", f"{type(exc).__name__}: {exc}")
        )

    # Jobs config derives that the running daemon has never heard of — invisible to every other
    # surface, because they enumerate the registry a stale supervisor wrote.
    try:
        findings += _check_job_registry_drift(cfg)
    except Exception as exc:
        findings.append(
            Finding(
                "supervisor.jobs_error",
                WARN,
                "Supervisor job-table check failed",
                f"{type(exc).__name__}: {exc}",
            )
        )

    # The console: the suite's only read surface, and the one resident job with no other artifact
    # (paper writes, a log) whose staleness would catch it if its restart loop got stuck.
    try:
        findings += _check_console(cfg)
    except Exception as exc:
        findings.append(
            Finding("console.error", WARN, "Console check failed", f"{type(exc).__name__}: {exc}")
        )

    # Every resident job's restart churn and self-stops. A module's freshness check cannot backstop
    # these -- a restart loop's own writes keep the paper DB looking fresh -- which is exactly how
    # 107 restarts in a session went unreported.
    try:
        findings += _check_resident_health(cfg)
    except Exception as exc:
        findings.append(
            Finding("resident.error", WARN, "Resident job check failed", f"{type(exc).__name__}: {exc}")
        )

    # Drift alert: report-driven paper-drawdown check (opt-in). Flows through the same notify path.
    findings += _check_drawdown(cfg)

    # Keep generic background services (e.g. the gex spot-trail recorder) alive.
    findings += _check_services(cfg)
    # Declared-cost check: the next expensive declaration should announce itself here rather than
    # at an open (see _check_subscription_budget).
    findings += _check_subscription_budget(cfg)
    # The data dependency that stops earnings silently (see _check_earnings_calendar).
    findings += _check_earnings_calendar(cfg)
    # Was last night's advice actually applied? Deterministic, and it replaced an AI checkpoint
    # that never once caught this (see _check_advice_enactment).
    findings += _check_advice_enactment(cfg, now, is_trading)

    # Watchdog the standalone market-data producer (dormant until the cutover enables the top-level
    # `streamer` block; today MEIC still owns the streamer under modules.meic.streamer).
    findings += _check_producer(cfg, in_session)

    overall = OK
    for f in findings:
        if _RANK[f.status] > _RANK[overall]:
            overall = f.status

    _log_findings(findings, overall)
    notifier = Notifier(cfg.get("notify"))
    renotify = cfg.get("watchdog", {}).get("renotify_minutes", 60)
    _process_notifications(findings, notifier, renotify)

    # Paper-trade entry/exit notifications — best-effort, independent of the health-alert path so a
    # trade-notify hiccup can never break the reliability check.
    try:
        from . import trade_notifier

        trade_notifier.run(cfg)
    except Exception:
        pass

    cfgmod.ensure_dirs()
    _HEARTBEAT.write_text(
        json.dumps(
            {
                "ts": _utcnow(),
                "et": now.isoformat(),
                "overall": overall,
                "in_session": in_session,
                "is_trading_day": is_trading,
                "findings": [asdict(f) for f in findings],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {"overall": overall, "findings": [asdict(f) for f in findings]}


if __name__ == "__main__":
    result = run()
    json.dump(result, sys.stdout, indent=2)
    print()
