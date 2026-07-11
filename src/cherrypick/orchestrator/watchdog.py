"""Cherrypick watchdog — the walk-away reliability guarantee, minimal form.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import tasks, timeutil
from .util import first_json

_WATCHDOG_LOG = cfgmod.LOGS_DIR / "watchdog.log"
_STATE_FILE = cfgmod.STATE_DIR / "watchdog_state.json"
_HEARTBEAT = cfgmod.STATE_DIR / "watchdog.last.json"

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
def _check_meic(name: str, mcfg: dict[str, Any], in_session: bool) -> list[Finding]:
    findings: list[Finding] = []
    root = cfgmod.module_root(mcfg)
    paper = mcfg.get("paper", {})

    # (a) self-healing task registered
    task_name = paper.get("task_name")
    if task_name and not tasks.exists(task_name):
        findings.append(
            Finding(
                f"{name}.task",
                CRITICAL,
                "MEIC paper task missing",
                f"Scheduled task '{task_name}' is not registered. Run: cherrypick install",
            )
        )
    else:
        findings.append(Finding(f"{name}.task", OK, "MEIC paper task", "registered"))

    # (b) freshness during the session
    if in_session:
        ages = [
            a
            for a in (
                _file_age_minutes(root / paper["paper_db"]) if paper.get("paper_db") else None,
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
                    "MEIC paper has no output yet",
                    "No paper DB or log file found during market hours.",
                )
            )
        elif min(ages) > fresh_limit:
            findings.append(
                Finding(
                    f"{name}.fresh",
                    WARN,
                    "MEIC paper data is stale",
                    f"No paper write in {min(ages):.0f} min (limit {fresh_limit}). Is the task running?",
                )
            )
        else:
            findings.append(Finding(f"{name}.fresh", OK, "MEIC paper fresh", f"{min(ages):.0f} min old"))
    else:
        findings.append(Finding(f"{name}.fresh", OK, "MEIC paper", "off-hours (freshness not checked)"))

    # (c) streamer liveness (session only); benign auto-restart
    streamer = mcfg.get("streamer", {})
    if streamer.get("enabled") and in_session:
        running = None
        try:
            r = _run_module(root, streamer["status_argv"], timeout=15)
            running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else None
        except Exception:
            running = None
        if running is False and streamer.get("auto_restart"):
            started = _start_streamer(root, streamer["start_argv"])
            findings.append(
                Finding(
                    f"{name}.streamer",
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
                    f"{name}.streamer",
                    WARN,
                    "Streamer down",
                    "Streamer not running during market hours (auto_restart off).",
                )
            )
        elif running is None:
            findings.append(
                Finding(
                    f"{name}.streamer",
                    WARN,
                    "Streamer status unknown",
                    "Could not read streamer --status; check manually.",
                )
            )
        else:
            findings.append(Finding(f"{name}.streamer", OK, "Streamer", "running"))
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
            hb = _read_heartbeat(cfgmod.STATE_DIR / "earnings_entry.last.json")
            today = now_et.strftime("%Y-%m-%d")
            if hb.get("date") != today:
                findings.append(
                    Finding(
                        f"{name}.entry_sla",
                        CRITICAL,
                        "Earnings paper entry did not run",
                        f"No successful entry heartbeat for {today} after {paper['entry_time']} ET.",
                    )
                )
            elif not hb.get("ok", False):
                findings.append(
                    Finding(
                        f"{name}.entry_sla",
                        WARN,
                        "Earnings paper entry reported an error",
                        f"Last entry: {hb.get('error') or 'see logs/earnings_paper.log'}",
                    )
                )
            else:
                findings.append(Finding(f"{name}.entry_sla", OK, "Earnings paper entry", "ran today"))
    return findings


def _read_heartbeat(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# --------------------------------------------------------------------------- state + notify
def _load_state() -> dict[str, Any]:
    return _read_heartbeat(_STATE_FILE) or {}


def _save_state(state: dict[str, Any]) -> None:
    cfgmod.ensure_dirs()
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _log_findings(findings: list[Finding], overall: str) -> None:
    cfgmod.ensure_dirs()
    with _WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": _utcnow(), "overall": overall, "findings": [asdict(f) for f in findings]})
            + "\n"
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
