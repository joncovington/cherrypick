"""`cherrypick doctor` — one green/red readiness check.

Highest-leverage onboarding/reliability artifact: a single command that tells the walk-away user
whether the unattended paper setup is actually healthy *right now* — interpreter, config, module
paths, broker/keyring, streamer, scheduled tasks, paper DB writability, clock/timezone, and Dolt.
Read-only: it never installs, restarts, or trades.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import tasks, timeutil
from .util import first_json

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(module_root: Path, argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [cfgmod.python_exe(), *[str(a) for a in argv]],
        cwd=str(module_root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _dolt_databases(host: str, port: int, user: str = "root") -> set[str] | None:
    """The set of database names the Dolt (MySQL-protocol) server serves, or None if it couldn't
    be determined — no MySQL client installed, or the query failed. Optional by design: doctor is
    read-only diagnostics (never the reliability path, which stays stdlib-only), and a None result
    degrades gracefully to a reachability-only report rather than a hard cherrypick dependency."""
    try:
        import mysql.connector  # optional; only this diagnostic uses it
    except Exception:
        return None
    try:
        conn = mysql.connector.connect(host=host, port=int(port), user=user, connection_timeout=5)
        try:
            cur = conn.cursor()
            cur.execute("SHOW DATABASES")
            names = {row[0] for row in cur.fetchall()}
            cur.close()
            return names
        finally:
            conn.close()
    except Exception:
        return None


def _dolt_status(reachable: bool, required: list[str], present: set[str] | None) -> tuple[str, str]:
    """Classify the Dolt check. Reachability alone is not health: a server rooted at the wrong data
    dir answers on the port while serving none of the required databases (the failure that silently
    broke the earnings entry on 2026-07-11, masked by a port-only check). When `required` databases
    are declared and a client is available, missing databases are a hard FAIL."""
    if not reachable:
        return WARN, "not reachable (earnings entry self-starts it)"
    if not required:
        return OK, "reachable"
    if present is None:
        return OK, "reachable (db-presence check skipped: no MySQL client)"
    missing = [db for db in required if db not in present]
    if missing:
        return FAIL, f"reachable but MISSING databases: {', '.join(missing)} (serving wrong data dir?)"
    return OK, f"reachable; databases present: {', '.join(required)}"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".cherrypick_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run(cfg: dict[str, Any] | None = None, fast: bool = False) -> list[Check]:
    """Run the readiness checks. `fast=True` skips the broker/keyring check — the only one that makes
    an authenticated broker round-trip (a 35s-timeout subprocess) — so it's safe to poll on a short
    cadence (the `dashboard --serve` live-checks card) without hammering the broker or its rate limits.
    Everything else (interpreter, clock, paths, config, paper-DB writability, task registration,
    streamer liveness, Dolt reachability, notify) is local/cheap and always runs."""
    checks: list[Check] = []
    try:
        cfg = cfg or cfgmod.load_config()
    except Exception as exc:
        return [Check("config", FAIL, f"Could not load config.json: {exc}")]

    # interpreter
    checks.append(Check("python", OK, f"{sys.version.split()[0]} @ {sys.executable}"))

    # clock / timezone
    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays(cfg, cfgmod.module_root)
    now = timeutil.now_et(tz)
    checks.append(
        Check(
            "clock/tz",
            OK,
            f"{now.strftime('%Y-%m-%d %H:%M %Z')} | trading_day={timeutil.is_trading_day(now, holidays)} "
            f"| in_session={timeutil.is_session_window(now, holidays)} | holidays_loaded={len(holidays)}",
        )
    )

    modules = cfgmod.enabled_modules(cfg)
    if not modules:
        checks.append(Check("modules", WARN, "no modules enabled in config.json"))

    broker_checked = False
    for name, mcfg in modules.items():
        root = cfgmod.module_root(mcfg, name)
        in_place = bool(mcfg.get("path"))
        missing_detail = (
            f"in-place path missing: {root}"
            if in_place
            else f"not installed: {root} (run: cherrypick install)"
        )
        checks.append(
            Check(
                f"{name}.path",
                OK if root.exists() else FAIL,
                str(root) if root.exists() else missing_detail,
            )
        )
        if not root.exists():
            continue

        # module config present
        mc = root / ("config/config.json" if (root / "config/config.json").exists() else "config.json")
        checks.append(
            Check(
                f"{name}.config",
                OK if mc.exists() else WARN,
                str(mc) if mc.exists() else "module config.json not found",
            )
        )

        paper = mcfg.get("paper", {})
        # paper DB dir writable (resolved the same way every read surface resolves it, so this checks
        # the file the module actually writes — not a stale checkout-relative default)
        db_dir = cfgmod.paper_db_path(mcfg, name).parent
        checks.append(
            Check(
                f"{name}.paper_db",
                OK if _writable(db_dir) else FAIL,
                f"{db_dir} {'writable' if _writable(db_dir) else 'NOT writable'}",
            )
        )

        # scheduled task(s)
        for tkey in ("task_name", "entry_task_name", "exit_task_name"):
            tn = paper.get(tkey)
            if tn:
                reg = tasks.exists(tn)
                checks.append(
                    Check(
                        f"{name}.task[{tn}]",
                        OK if reg else WARN,
                        "registered" if reg else "not registered (run: cherrypick install)",
                    )
                )

        # broker/keyring — check once, via the first module that can. Skipped in fast mode: it's the
        # only authenticated broker round-trip, unsafe to poll on the live-checks cadence.
        if not broker_checked and not fast:
            try:
                r = _run(root, ["src/tt.py", "get_connection_status"], timeout=35)
                out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
                ok = bool(out.get("ok") or out.get("connected") or out.get("authenticated"))
                checks.append(
                    Check(
                        "broker/keyring",
                        OK if ok else FAIL,
                        "connected"
                        if ok
                        else f"get_connection_status not ok: {(r.stdout or r.stderr)[:160]}",
                    )
                )
                broker_checked = True
            except Exception as exc:
                checks.append(Check("broker/keyring", FAIL, f"connection check error: {exc}"))
                broker_checked = True

        # streamer liveness (info)
        streamer = mcfg.get("streamer", {})
        if streamer.get("enabled"):
            try:
                r = _run(root, streamer["status_argv"], timeout=15)
                running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else False
                checks.append(
                    Check(
                        f"{name}.streamer",
                        OK if running else WARN,
                        "running" if running else "not running (start with: cherrypick install)",
                    )
                )
            except Exception as exc:
                checks.append(Check(f"{name}.streamer", WARN, f"status error: {exc}"))

        # dolt — port reachability plus (when declared) that the required databases are actually served
        if paper.get("requires_dolt"):
            from .watchdog import _dolt_reachable  # local import avoids cycle at module load

            host = paper.get("dolt_host", "127.0.0.1")
            port = paper.get("dolt_port", 3306)
            reachable = _dolt_reachable(host, port)
            required = paper.get("dolt_databases") or []
            present = (
                _dolt_databases(host, port, paper.get("dolt_user", "root"))
                if reachable and required
                else None
            )
            status, detail = _dolt_status(reachable, required, present)
            checks.append(Check(f"{name}.dolt", status, detail))

            svc = paper.get("dolt_service")
            if svc and svc.get("task_name"):
                reg = tasks.exists(svc["task_name"])
                checks.append(
                    Check(
                        f"{name}.dolt_service",
                        OK if reg else WARN,
                        "keep-alive task registered"
                        if reg
                        else "keep-alive task missing (run: cherrypick install)",
                    )
                )

    # watchdog task
    wt = cfg.get("watchdog", {}).get("task_name")
    if wt:
        checks.append(
            Check(
                "watchdog.task",
                OK if tasks.exists(wt) else WARN,
                "registered" if tasks.exists(wt) else "not registered (run: cherrypick install)",
            )
        )

    # notify reachability — can the walk-away user actually be told?
    channels = cfg.get("notify", {}).get("channels", ["log"])
    from cherrypick.notify import secrets as _secrets  # local import; keyring

    detail_bits = []
    for ch in channels:
        if ch in ("log", "desktop"):
            detail_bits.append(f"{ch}=on")
        elif ch in _secrets.SUPPORTED:
            detail_bits.append(f"{ch}={_secrets.status([ch])[ch]}")
    # A push channel is configured if desktop is on (Windows) or a webhook is set.
    has_push = ("desktop" in channels and os.name == "nt") or any(
        ch in _secrets.SUPPORTED and _secrets.is_set(ch) for ch in channels
    )
    checks.append(
        Check(
            "notify.channels",
            OK if has_push else WARN,
            f"{', '.join(detail_bits)}" + ("" if has_push else "  (no push channel active; log floor only)"),
        )
    )
    return checks


def format_report(checks: list[Check]) -> tuple[str, int]:
    lines = ["cherrypick doctor", "=" * 60]
    worst = 0
    rank = {OK: 0, WARN: 1, FAIL: 2}
    for c in checks:
        lines.append(f"{_MARK[c.status]} {c.name:<24} {c.detail}")
        worst = max(worst, rank[c.status])
    summary = {0: "ALL GREEN", 1: "WARNINGS (non-blocking)", 2: "FAILURES — action needed"}[worst]
    lines += ["=" * 60, f"Result: {summary}"]
    return "\n".join(lines), worst
