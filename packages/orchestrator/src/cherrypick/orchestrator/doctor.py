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

from cherrypick.core import home as _home

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


_ARTIFACT_SUFFIXES = (".db", ".log")
_ARTIFACT_NAMES = ("dashboard.html",)
_SKIP_DIRS = {".git", "_core", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".tmp", ".venv"}


def find_stray_artifacts(roots: list[Path], *, limit: int = 50) -> list[Path]:
    """Runtime files that leaked into a checkout — everything runtime now lives under the cherrypick
    home, so a `*.db`/`*.log` anywhere, a generated `dashboard.html`, a `state/*.json`, or a
    `reports/*.html` inside a checkout root is a leak. Vendored/cache dirs (`_core`, `.git`,
    `__pycache__`, …) are skipped. Pure filesystem read — the `no-leak` guard and its test share it."""
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            here = Path(dirpath)
            for fn in filenames:
                is_leak = (
                    fn.endswith(_ARTIFACT_SUFFIXES)
                    or fn in _ARTIFACT_NAMES
                    or (here.name == "reports" and fn.endswith(".html"))
                    or (here.name == "state" and fn.endswith(".json"))
                )
                if is_leak:
                    found.append(here / fn)
                    if len(found) >= limit:
                        return found
    return found


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
            f"in-place path missing: {cfgmod.portable_path(root)}"
            if in_place
            else f"not installed: {cfgmod.portable_path(root)} (run: cherrypick install)"
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

        # module config present — home-first (~/.cherrypick/config/<pkg>.json), else the legacy in-repo
        # config, mirroring how the module itself resolves it (see each module's paths.config_path()).
        mc = next(
            (
                c
                for c in (_home.config_path(name), root / "config" / "config.json", root / "config.json")
                if c.exists()
            ),
            None,
        )
        checks.append(
            Check(
                f"{name}.config",
                OK if mc else WARN,
                str(mc) if mc else "module config not found (home or in-repo)",
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
                f"{cfgmod.portable_path(db_dir)} {'writable' if _writable(db_dir) else 'NOT writable'}",
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

    # background services (e.g. the gex spot-trail recorder): report each enabled daemon's status
    for svc in cfgmod.enabled_services(cfg):
        sid = svc["id"]
        root = cfgmod.module_root(svc, sid)
        if not root.exists():
            where = cfgmod.portable_path(root)
            checks.append(Check(f"service.{sid}", WARN, f"checkout not found at {where}"))
            continue
        try:
            r = _run(root, svc["status_argv"], timeout=15)
            running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else None
        except Exception:
            running = None
        if running:
            checks.append(Check(f"service.{sid}", OK, "running"))
        elif running is False:
            checks.append(Check(f"service.{sid}", WARN, "not running (install/watchdog starts it)"))
        else:
            checks.append(Check(f"service.{sid}", WARN, "status unknown (could not read status_argv)"))

    # no-leak guard: runtime output (DBs, logs, dashboard, state, reports) must live under the cherrypick
    # home, never inside a checkout. Advisory (WARN) — leftovers don't break anything, but they signal a
    # path resolver regressed or a pre-home-cutover file needs sweeping (see `cherrypick migrate-home`).
    roots = [cfgmod.ROOT] + [cfgmod.module_root(m, n) for n, m in cfgmod.enabled_modules(cfg).items()]
    stray = find_stray_artifacts(roots)
    if stray:
        sample = ", ".join(p.name for p in stray[:4]) + (" …" if len(stray) > 4 else "")
        checks.append(
            Check(
                "repo.no_leak",
                WARN,
                f"{len(stray)} runtime file(s) inside a checkout (should be under ~/.cherrypick): {sample}",
            )
        )
    else:
        checks.append(Check("repo.no_leak", OK, "no runtime artifacts in the checkout"))
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
