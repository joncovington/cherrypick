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
from . import tasks
from . import timeutil
from .util import first_json

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(module_root: Path, argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run([cfgmod.python_exe(), *[str(a) for a in argv]],
                          cwd=str(module_root), capture_output=True, text=True, timeout=timeout)


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".cherrypick_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run(cfg: dict[str, Any] | None = None) -> list[Check]:
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
    checks.append(Check("clock/tz", OK,
                        f"{now.strftime('%Y-%m-%d %H:%M %Z')} | trading_day={timeutil.is_trading_day(now, holidays)} "
                        f"| in_session={timeutil.is_session_window(now, holidays)} | holidays_loaded={len(holidays)}"))

    modules = cfgmod.enabled_modules(cfg)
    if not modules:
        checks.append(Check("modules", WARN, "no modules enabled in config.json"))

    broker_checked = False
    for name, mcfg in modules.items():
        root = cfgmod.module_root(mcfg)
        checks.append(Check(f"{name}.path", OK if root.exists() else FAIL,
                            str(root) if root.exists() else f"module path missing: {root}"))
        if not root.exists():
            continue

        # module config present
        mc = root / ("config/config.json" if (root / "config/config.json").exists() else "config.json")
        checks.append(Check(f"{name}.config", OK if mc.exists() else WARN,
                            str(mc) if mc.exists() else "module config.json not found"))

        paper = mcfg.get("paper", {})
        # paper DB dir writable
        db_rel = paper.get("paper_db") or "data/paper_trades.db"
        db_dir = (root / db_rel).parent
        checks.append(Check(f"{name}.paper_db", OK if _writable(db_dir) else FAIL,
                            f"{db_dir} {'writable' if _writable(db_dir) else 'NOT writable'}"))

        # scheduled task(s)
        for tkey in ("task_name", "entry_task_name", "exit_task_name"):
            tn = paper.get(tkey)
            if tn:
                reg = tasks.exists(tn)
                checks.append(Check(f"{name}.task[{tn}]", OK if reg else WARN,
                                    "registered" if reg else "not registered (run: cherrypick install)"))

        # broker/keyring — check once, via the first module that can
        if not broker_checked:
            try:
                r = _run(root, ["src/tt.py", "get_connection_status"], timeout=35)
                out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
                ok = bool(out.get("ok") or out.get("connected") or out.get("authenticated"))
                checks.append(Check("broker/keyring", OK if ok else FAIL,
                                    "connected" if ok else f"get_connection_status not ok: {(r.stdout or r.stderr)[:160]}"))
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
                checks.append(Check(f"{name}.streamer", OK if running else WARN,
                                    "running" if running else "not running (start with: cherrypick install)"))
            except Exception as exc:
                checks.append(Check(f"{name}.streamer", WARN, f"status error: {exc}"))

        # dolt
        if paper.get("requires_dolt"):
            from .watchdog import _dolt_reachable  # local import avoids cycle at module load
            reachable = _dolt_reachable(paper.get("dolt_host", "127.0.0.1"), paper.get("dolt_port", 3306))
            checks.append(Check(f"{name}.dolt", OK if reachable else WARN,
                                "reachable" if reachable else "not reachable (earnings entry self-starts it)"))

    # watchdog task
    wt = cfg.get("watchdog", {}).get("task_name")
    if wt:
        checks.append(Check("watchdog.task", OK if tasks.exists(wt) else WARN,
                            "registered" if tasks.exists(wt) else "not registered (run: cherrypick install)"))

    # notify reachability — can the walk-away user actually be told?
    channels = cfg.get("notify", {}).get("channels", ["log"])
    from notify import secrets as _secrets  # local import; keyring
    detail_bits = []
    for ch in channels:
        if ch in ("log", "desktop"):
            detail_bits.append(f"{ch}=on")
        elif ch in _secrets.SUPPORTED:
            detail_bits.append(f"{ch}={_secrets.status([ch])[ch]}")
    # A push channel is configured if desktop is on (Windows) or a webhook is set.
    has_push = ("desktop" in channels and os.name == "nt") or \
               any(ch in _secrets.SUPPORTED and _secrets.is_set(ch) for ch in channels)
    checks.append(Check("notify.channels", OK if has_push else WARN,
                        f"{', '.join(detail_bits)}" + ("" if has_push else "  (no push channel active; log floor only)")))
    return checks


def format_report(checks: list[Check]) -> tuple[str, int]:
    lines = ["Cherrypick doctor", "=" * 60]
    worst = 0
    rank = {OK: 0, WARN: 1, FAIL: 2}
    for c in checks:
        lines.append(f"{_MARK[c.status]} {c.name:<24} {c.detail}")
        worst = max(worst, rank[c.status])
    summary = {0: "ALL GREEN", 1: "WARNINGS (non-blocking)", 2: "FAILURES — action needed"}[worst]
    lines += ["=" * 60, f"Result: {summary}"]
    return "\n".join(lines), worst
