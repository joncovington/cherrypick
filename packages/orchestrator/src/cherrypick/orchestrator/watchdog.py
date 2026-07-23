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
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import tasks, timeutil
from .util import first_json

_WATCHDOG_LOG = cfgmod.LOGS_DIR / "watchdog.log"
_STATE_FILE = cfgmod.STATE_DIR / "watchdog_state.json"
_HEARTBEAT = cfgmod.STATE_DIR / "watchdog.last.json"

# The in-place launcher (pythonw run.py <verb>) for detached EOD subprocesses. watchdog.py lives at
# src/cherrypick/orchestrator/watchdog.py, so the repo-root run.py is three parents up from its dir.
_RUN_PY = Path(__file__).resolve().parents[3] / "run.py"
# Reserved (non-finding) state key marking the day the EOD digest/insight were fired, so they fire once.
_EOD_FIRED_KEY = "_eod_fired_day"

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


def _streamer_stale_detail(global_age: float | None, underlying_age: float | None, limit: int) -> str:
    """Name whichever feed(s) are stale, so the alert distinguishes a whole-stream silence from an
    underlying-spot-only stall (the two have different causes)."""
    parts = []
    if global_age is not None and global_age > limit:
        parts.append(f"no events for {global_age:.0f}s")
    if underlying_age is not None and underlying_age > limit:
        parts.append(f"underlying spot frozen for {underlying_age:.0f}s")
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
    limit = spec.get("stale_restart_seconds", 240)
    # A stall is EITHER the whole stream going quiet OR the underlying-spot feed dying while option
    # quotes keep the global age fresh (2026-07-22). Judge on whichever available signal is most stale.
    stale_candidates = [a for a in (stale_age, underlying_age) if a is not None]
    worst_stale = max(stale_candidates) if stale_candidates else None
    detail = _streamer_stale_detail(stale_age, underlying_age, limit)
    connection_age = _streamer_connection_age(status)
    # Don't count a connection that has not had time to populate yet — a restart takes a few seconds to
    # resubscribe, and without this the next tick would see stale data and restart again, forever.
    settling = connection_age is not None and connection_age < limit
    if (running and worst_stale is not None and worst_stale > limit and not settling
            and spec.get("auto_restart")):
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
    elif running and worst_stale is not None and worst_stale > limit:
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
        findings.append(Finding(label, OK, "Streamer", "running"))
    return findings


def _check_producer(cfg: dict[str, Any], in_session: bool) -> list[Finding]:
    """Watchdog the standalone market-data streamer (the suite's sole producer) when it is configured as
    a top-level `streamer` block.

    Dormant unless that block exists and is enabled — until the cutover, MEIC still owns the streamer
    under `modules.meic.streamer` and this returns nothing. Exactly one producer is ever enabled at a
    time (enabling this + `modules.meic.streamer.enabled=false` is the flip). Session-gated + silence
    restart, the same contract MEIC's streamer has via `_check_streamer_health`.
    """
    spec = cfg.get("streamer") or {}
    if not spec.get("enabled") or not in_session:
        return []
    root = cfgmod.module_root(spec, "streamer")
    if not root.exists():
        return [Finding("streamer", WARN, "streamer checkout missing", f"not found at {root}")]
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

    # (a) self-healing task registered
    task_name = paper.get("task_name")
    if task_name and not tasks.exists(task_name):
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

    # (b) freshness during the session
    if in_session:
        ages = [
            a
            for a in (
                _file_age_minutes(cfgmod.paper_db_path(mcfg, name)) if paper.get("paper_db") else None,
                _file_age_minutes(root / paper["log"]) if paper.get("log") else None,
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

    # (a) entry/exit tasks registered
    for tkey, label in (("entry_task_name", "entry"), ("exit_task_name", "exit")):
        tn = paper.get(tkey)
        if tn and not tasks.exists(tn):
            findings.append(
                Finding(
                    f"{name}.task.{label}",
                    CRITICAL,
                    f"Earnings {label} task missing",
                    f"Scheduled task '{tn}' is not registered. Run: cherrypick install",
                )
            )
        elif tn:
            findings.append(Finding(f"{name}.task.{label}", OK, f"Earnings {label} task", "registered"))

    # (b) Dolt reachability (only meaningful on trading days)
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

    # (c) entry SLA — after entry_time+grace on a trading day, the run must have happened
    if is_trading and paper.get("entry_time"):
        try:
            eh, em = [int(x) for x in paper["entry_time"].split(":")]
            grace_passed = now_et.time() >= datetime(now_et.year, now_et.month, now_et.day, eh, em).time()
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


def _read_heartbeat(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def _eod_launch(verb: str) -> bool:
    """Launch `pythonw run.py <verb>` DETACHED from the orchestrator root, so the digest's webhook push
    and the insight's `claude` call run OUTSIDE the watchdog process — the reliability path stays
    stdlib + OS-shell only. Reuses the same detached-Popen helper the streamer restart uses."""
    return _start_streamer(_RUN_PY.parent, [str(_RUN_PY), verb])


def _check_settlement(name: str, mcfg: dict[str, Any], now_et: datetime, is_trading: bool) -> list[Finding]:
    """Warn when a module is past the close on a trading day with open positions it has not settled.

    The freshness check misses this: the loop itself is running fine (writing its DB/log every tick),
    it just cannot settle. The flies 2026-07-22 incident — 5 open 0DTE positions left unsettled for
    9 hours because the market-data feed went stale and settlement refuses a stale price — logged
    "cannot settle" every 2 minutes with zero alert. The module already reports this through its
    `--status` (session_settled / positions_today / data_reason); the watchdog just was not reading it.

    Opt-in via `paper.settlement_check` so it only shells to `--status` for a module that exposes the
    signal (never MEIC's status path). Gated to after the close, so it does not poll all session.
    """
    paper = mcfg.get("paper", {})
    if not (is_trading and paper.get("settlement_check") and paper.get("status_argv")):
        return []
    if now_et.time() <= timeutil.MARKET_CLOSE:
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


def _check_eod(cfg: dict[str, Any], now: datetime, is_trading: bool) -> None:
    """Fire the EOD digest + insight ONCE per trading day, event-driven instead of at a fixed clock time.

    After the close, on each watchdog tick, fire as soon as every installed module has written its
    `paper-eod-<day>.md` — or at the `eod_digest.deadline` backstop (ET) if a module is late or never
    writes (a flat flies session writes none), so it can never skip. Both are launched detached (AI +
    webhook I/O out of this process). Best-effort and off the reliability path.
    """
    if not is_trading or now.time() <= timeutil.MARKET_CLOSE:
        return
    ed = cfgmod.eod_digest_settings(cfg)
    ei = cfgmod.insight_settings(cfg)
    if not ed["enabled"] and not ei["enabled"]:
        return

    day = now.date().isoformat()
    state = _load_state()
    if state.get(_EOD_FIRED_KEY) == day:
        return  # already fired today

    missing = [
        name
        for name in cfgmod.enabled_modules(cfg)
        if not (cfgmod.module_logs_dir(name) / f"paper-eod-{day}.md").exists()
    ]
    past_deadline = now.time() >= _parse_hhmm(ed["deadline"], time(16, 45))
    if missing and not past_deadline:
        return  # wait for the stragglers until the backstop

    if ed["enabled"]:
        _eod_launch("notify-eod")
    if ei["enabled"]:
        _eod_launch("eod-insight")
    # Mark fired regardless of launch outcome so a transient Popen failure can't loop every tick; a
    # failed launch is rare and the digest can always be run by hand.
    state[_EOD_FIRED_KEY] = day
    _save_state(state)


def _log_findings(findings: list[Finding], overall: str) -> None:
    cfgmod.ensure_dirs()
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
            findings.append(
                Finding(f"service.{sid}", WARN, f"{sid} checkout missing", f"not found at {root}")
            )
            continue
        running = None
        try:
            r = _run_module(root, svc["status_argv"], timeout=15)
            running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else None
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
        else:
            findings.append(Finding(f"service.{sid}", OK, sid, "running"))
    return findings


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
def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or cfgmod.load_config()
    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays(cfg, cfgmod.module_root)
    now = timeutil.now_et(tz)
    in_session = timeutil.is_session_window(now, holidays)
    is_trading = timeutil.is_trading_day(now, holidays)

    findings: list[Finding] = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        kind = mcfg.get("paper", {}).get("kind")
        try:
            if kind == "self_healing":
                findings += _check_meic(name, mcfg, in_session)
                findings += _check_settlement(name, mcfg, now, is_trading)
            elif kind == "cherrypick_scheduled":
                findings += _check_earnings(name, mcfg, now, is_trading)
        except Exception as exc:
            findings.append(
                Finding(
                    f"{name}.error",
                    CRITICAL,
                    f"Watchdog check failed for {name}",
                    f"{type(exc).__name__}: {exc}",
                )
            )

    # Drift alert: report-driven paper-drawdown check (opt-in). Flows through the same notify path.
    findings += _check_drawdown(cfg)

    # Keep generic background services (e.g. the gex spot-trail recorder) alive.
    findings += _check_services(cfg)

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

    # Fire the EOD digest + insight once all installed modules have settled (or at the deadline backstop)
    # — launched detached, so no AI/webhook I/O runs here. Best-effort.
    try:
        _check_eod(cfg, now, is_trading)
    except Exception:
        pass

    # Regenerate the read-side dashboard (static HTML, file-only) — best-effort; a render hiccup must
    # never break the reliability path.
    if cfg.get("dashboard", {}).get("auto_regen", True):
        try:
            from . import dashboard

            dashboard.render(cfg)
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
