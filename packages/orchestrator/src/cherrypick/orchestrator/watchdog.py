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

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import eval_activity, servicecfg, tasks, timeutil, util
from .util import CREATE_NO_WINDOW, first_json

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


def _streamer_stale_detail(
    global_age: float | None,
    underlying_age: float | None,
    limit: int,
    chain_errors: dict[str, str] | None = None,
) -> str:
    """Name whichever feed(s) are stale, so the alert distinguishes a whole-stream silence from an
    underlying-spot-only stall or a single symbol's dead chain fetch (all different causes)."""
    parts = []
    if global_age is not None and global_age > limit:
        parts.append(f"no events for {global_age:.0f}s")
    if underlying_age is not None and underlying_age > limit:
        parts.append(f"underlying spot frozen for {underlying_age:.0f}s")
    if chain_errors:
        named = ", ".join(f"{sym}: {err}" for sym, err in chain_errors.items())
        parts.append(f"chain fetch failing for {named}")
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
    limit = spec.get("stale_restart_seconds", 240)
    # A stall is the whole stream going quiet, OR the underlying-spot feed dying while option quotes
    # keep the global age fresh (2026-07-22), OR a single symbol's chain fetch exhausting its in-process
    # retries while every other symbol stays fresh (2026-07-31) — judge on whichever signal fires.
    stale_candidates = [a for a in (stale_age, underlying_age) if a is not None]
    worst_stale = max(stale_candidates) if stale_candidates else None
    is_stalled = (worst_stale is not None and worst_stale > limit) or bool(chain_errors)
    detail = _streamer_stale_detail(stale_age, underlying_age, limit, chain_errors)
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
    return findings


def _recycle_streamer_if_stale(label: str, root: Path, spec: dict[str, Any], settling: bool) -> Finding:
    """A streamer that is up and streaming, but on config from before the last edit.

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
        state = servicecfg.staleness(spec, root, label)
    except Exception:  # never fail the tick over a stale check
        return healthy

    if state["adopt"]:
        servicecfg.write_stamp(label, state["hash"], state["source"])
        return healthy
    if not state["stale"]:
        return healthy

    where = state.get("source") or "streamer config"
    if not spec.get("auto_restart"):
        return Finding(
            label,
            WARN,
            "Streamer running stale config",
            f"Config changed since launch ({where}); auto_restart is off, so restart it by hand.",
        )
    stopped = _stop_streamer(root, spec)
    started = _start_streamer(root, spec["start_argv"]) if stopped else False
    if started:
        servicecfg.write_stamp(label, state["hash"], state["source"])
        return Finding(
            label,
            WARN,
            "Streamer recycled onto new config",
            f"Config changed since launch ({where}); stopped and restarted so it re-reads.",
        )
    return Finding(
        label,
        WARN,
        "Streamer stale config — recycle failed",
        f"Config changed since launch ({where}) but the {'restart' if stopped else 'stop'} failed; "
        "it is still producing on the old config.",
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

    # (b) freshness during the session. The log lives in the shared logs home
    # (~/.cherrypick/logs/<name>/), NOT the module checkout -- config declares it
    # checkout-relative for historical reasons, so resolve the basename against
    # module_logs_dir exactly like the dashboard does. Resolving against `root`
    # silently misses (the old bug: _file_age_minutes returns None for a missing
    # file, so half the freshness signal was dead with no error).
    if in_session:
        log_rel = paper.get("log")
        ages = [
            a
            for a in (
                _file_age_minutes(cfgmod.paper_db_path(mcfg, name)) if paper.get("paper_db") else None,
                _file_age_minutes(cfgmod.module_logs_dir(name) / Path(log_rel).name) if log_rel else None,
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

    # (c) entry SLA — after entry_time+grace on a trading day, the run must have happened.
    # The grace matters: the entry task fires AT entry_time, its subprocess may run up to
    # 30 minutes (cli timeout 1800s), and the heartbeat is only written after it returns —
    # so a comparison against entry_time alone raised CRITICAL for a run that was simply
    # still in progress (the same false-alarm class _check_settlement's grace fixed).
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


def _eod_launch(verb: str) -> bool:
    """Launch `pythonw run.py <verb>` DETACHED from the orchestrator root, so the digest's webhook push
    and the insight's `claude` call run OUTSIDE the watchdog process — the reliability path stays
    stdlib + OS-shell only. Reuses the same detached-Popen helper the streamer restart uses."""
    return _start_streamer(_RUN_PY.parent, [str(_RUN_PY), verb])


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

    registered = tasks.exists(task_name)
    armed_for = (status or {}).get("armed_for")
    today = now_et.date().isoformat()

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
                f"{label} LIVE task survived past disarm",
                f"{task_name} still registered past {disarm_hhmm}+{grace}m (armed_for={armed_for}); "
                "halt flag set — live ticks now refuse. Investigate why self-disarm failed, then "
                "uninstall the task and clear the halt flag before re-arming.",
            )
        )
        return findings  # halted state — the armed-window checks below would only add noise

    if registered:
        # (b) freshness while armed and in session: the task must have actually RUN recently.
        if in_session:
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
        # (a) status says armed-for-today but the task is gone mid-window.
        findings.append(
            Finding(
                f"{name}.live_task",
                CRITICAL,
                f"{label} LIVE task missing",
                f"{task_name} not registered but the arm stamp says armed for {armed_for}.",
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
    adv = cfgmod.advise_settings(cfg)
    adv_on = adv["enabled"] and any(m.get("enabled") for m in adv["modules"].values())
    if not ed["enabled"] and not ei["enabled"] and not adv_on:
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

    launched_ok = True
    if ed["enabled"]:
        launched_ok = _eod_launch("notify-eod") and launched_ok
    if ei["enabled"]:
        launched_ok = _eod_launch("eod-insight") and launched_ok
    if adv_on:
        # Same completion event, same detachment: next-session advice is generated from the day's
        # freshly-written reports, and the claude call never runs in the watchdog process. The
        # watchdog fires it and forgets — it never waits on or alerts about advice generation.
        launched_ok = _eod_launch("advise") and launched_ok
    # Mark fired regardless of launch outcome so a transient Popen failure can't loop every tick —
    # but a failed launch must not be SILENT: the digest exists for the walk-away guarantee, so a
    # lost day gets a notification pointing at the manual re-run instead of vanishing.
    state[_EOD_FIRED_KEY] = day
    _save_state(state)
    if not launched_ok:
        try:
            Notifier(cfg.get("notify")).notify(
                "WARNING",
                "eod.launch",
                "EOD digest/insight launch failed",
                f"Detached launch failed for {day}. Run `cherrypick notify-eod` (and `eod-insight`) by hand.",
            )
        except Exception:
            pass


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
def run_preopen(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pre-open producer check: streamer liveness only, on a tight interval, in a short window.

    The full tick runs every 10 minutes and streamer supervision starts at 09:15, so the first
    supervising tick of the day can land as late as ~09:25 — minutes before the 09:30–09:35 opening
    range, which cannot be reconstructed once missed. A streamer that died overnight was therefore
    unsupervised until then, and a restart is not instant: `_check_streamer_health`'s own `settling`
    window is 240s, so a 09:25 relaunch is still resubscribing when the range starts.

    This is a separate task rather than a shorter global interval on purpose. Dropping the watchdog
    to 2 minutes would multiply every tick's work — module checks, dashboard render, EOD triggers —
    all day, to fix a 35-minute window.

    It reuses `_check_streamer_health` rather than copying it: that function carries the 2026-07-20
    silence-restart lesson (a live-but-quiet socket reporting running=true), and a second copy would
    drift from it. Findings go through the same notify path, so a failure here alerts exactly like
    any other. Writes no heartbeat — the full tick owns that, and a second writer would make
    "when did the watchdog last run" ambiguous.
    """
    cfg = cfgmod.load_config() if cfg is None else cfg
    settings = cfgmod.preopen_settings(cfg)
    if not settings["enabled"]:
        return {"ok": True, "skipped": "preopen not enabled"}

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
