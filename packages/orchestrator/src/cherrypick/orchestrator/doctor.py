"""`cherrypick doctor` — one green/red readiness check.

Highest-leverage onboarding/reliability artifact: a single command that tells the walk-away user
whether the unattended paper setup is actually healthy *right now* — interpreter, config, module
paths, broker/keyring, streamer, scheduled tasks, paper DB writability, clock/timezone, and Dolt.
Read-only: it never installs, restarts, or trades.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import config as cfgmod
from . import eval_activity, tasks, timeutil
from .util import CREATE_NO_WINDOW, first_json

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


_ARTIFACT_SUFFIXES = (".db", ".log")
_ARTIFACT_NAMES = ("dashboard.html",)
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".tmp", ".venv"}


def find_stray_artifacts(roots: list[Path], *, limit: int = 50) -> list[Path]:
    """Runtime files that leaked into a checkout — everything runtime now lives under the cherrypick
    home, so a `*.db`/`*.log` anywhere, a generated `dashboard.html`, a `state/*.json`, or a
    `reports/*.html` inside a checkout root is a leak. Cache/VCS dirs (`.git`, `__pycache__`, …) are
    skipped. Pure filesystem read — the `no-leak` guard and its test share it."""
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
        creationflags=CREATE_NO_WINDOW,
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


def _supervisor_driving() -> bool:
    """True when a live supervisor owns this box's scheduling (fresh heartbeat + live PID) — the
    dual-read switch every task-registration check branches on until the transition window closes."""
    from . import supersnap  # local import: avoids a cycle at module load

    return supersnap.supervisor_alive()


def _job_check(key: str, job_id: str, enabled: bool = True) -> Check:
    """One supervisor-job registration check — the `tasks.exists` equivalent, read from the job
    registry file (zero subprocess spawns). Keeps the off-by-choice-is-healthy distinction."""
    from . import supersnap

    st = supersnap.job_state(job_id)
    if enabled and st and st.get("enabled"):
        return Check(key, OK, f"supervised (job {job_id})")
    if enabled and st is not None:
        return Check(key, WARN, f"job {job_id} disabled: {st.get('enabled_reason') or 'unknown'}")
    if enabled:
        return Check(key, WARN, f"job {job_id} missing from supervisor registry (check supervisor.log)")
    return Check(key, OK, f"disabled (job {job_id} off)")


def _supervisor_checks(cfg: dict[str, Any], fast: bool) -> list[Check]:
    """The supervisor's own health for doctor: daemon liveness, the anchor task, and — full mode
    only — the legacy-task drift sweep (a pre-cutover `cherrypick-*` task still registered means the
    cutover was incomplete and something may double-fire). Empty on a pre-cutover box (no heartbeat
    file has ever been written), so the dual-read period stays noise-free."""
    from . import supersnap, supervisor

    if not supervisor.heartbeat_path().exists():
        return []
    checks: list[Check] = []
    age = supersnap.heartbeat_age_seconds()
    if supersnap.supervisor_alive():
        checks.append(Check("supervisor", OK, f"running (heartbeat {age:.0f}s old)"))
    else:
        detail = f"heartbeat {age:.0f}s old" if age is not None else "no heartbeat"
        checks.append(
            Check(
                "supervisor",
                FAIL,
                f"NOT running ({detail}; limit {supervisor.HEARTBEAT_FRESH_SECONDS}s) — "
                "run: cherrypick ensure-supervisor",
            )
        )
    if not fast:
        anchor = tasks.exists(supersnap.ANCHOR_TASK)
        checks.append(
            Check(
                "supervisor.anchor",
                OK if anchor else FAIL,
                "anchor task registered"
                if anchor
                else f"anchor task '{supersnap.ANCHOR_TASK}' missing — nothing restarts a dead "
                "supervisor (run: cherrypick install)",
            )
        )
        leftovers = [n for n in tasks.legacy_task_names(cfg) if tasks.exists(n)]
        checks.append(
            Check(
                "supervisor.legacy_tasks",
                WARN if leftovers else OK,
                f"legacy scheduled task(s) still registered — incomplete cutover, may double-fire: "
                f"{', '.join(leftovers)} (run: cherrypick install)"
                if leftovers
                else "no legacy scheduled tasks remain",
            )
        )
    return checks


def _suite_task_checks(cfg: dict[str, Any]) -> list[Check]:
    """The orchestrator's own recurring tasks — the ones `install` registers that are not a module's.

    These were unchecked, so a green `doctor` meant "the paper pipeline is registered", not "the
    suite is". The likely failure is not a task never created; it is one left registered against a
    **stale checkout path** after a move, or silently absent after an `install` that partly failed.

    Each task's name is resolved through the same `*_settings` helper `install`/`uninstall` use, so
    a config-driven rename cannot desync the check from what was actually registered.

    **Off-by-choice reads differently from missing.** `reconcile` and the Follow Feed notifier are
    opt-in; "not registered because not enabled" is a healthy state, and reporting it as a warning
    would train the operator to ignore this whole section — which is the failure mode that let
    `holidays_loaded=0` sit in the docs as an accepted gap.
    """
    if _supervisor_driving():
        sh = (cfg.get("watchdog", {}) or {}).get("streamer_health", {}) or {}
        return [
            _job_check("task.trade_notify", "trade-notify"),
            _job_check("task.log_archive", "log-archive", cfgmod.archive_settings(cfg)["enabled"]),
            _job_check("task.reconcile", "reconcile", cfgmod.reconcile_schedule_settings(cfg)["enabled"]),
            _job_check("task.follow_notify", "follow-notify", cfgmod.follow_feed_settings(cfg)["enabled"]),
            _job_check("task.lossdog_notify", "lossdog-notify", cfgmod.lossdog_settings(cfg)["enabled"]),
            # streamer-health is preopen's whole-session replacement under the supervisor
            _job_check("task.streamer_health", "streamer-health", sh.get("enabled", True)),
        ]
    resolved = [
        ("trade_notify", (cfg.get("trade_notify", {}) or {}).get("task_name"), True),
        ("log_archive", cfgmod.archive_settings(cfg)["task_name"], cfgmod.archive_settings(cfg)["enabled"]),
        (
            "reconcile",
            cfgmod.reconcile_schedule_settings(cfg)["task_name"],
            cfgmod.reconcile_schedule_settings(cfg)["enabled"],
        ),
        (
            "follow_notify",
            cfgmod.follow_feed_settings(cfg)["task_name"],
            cfgmod.follow_feed_settings(cfg)["enabled"],
        ),
        ("preopen", cfgmod.preopen_settings(cfg)["task_name"], cfgmod.preopen_settings(cfg)["enabled"]),
    ]
    checks: list[Check] = []
    for label, name, enabled in resolved:
        if not name:
            continue
        registered = tasks.exists(name)
        if enabled and registered:
            status, detail = OK, f"registered ({name})"
        elif enabled:
            status, detail = WARN, f"enabled but not registered (run: cherrypick install) [{name}]"
        elif registered:
            # A task still firing for a feature that has been switched off. Benign for these
            # (each command re-reads config and no-ops), but it is drift worth seeing.
            detail = f"disabled in config but still registered (run: cherrypick install) [{name}]"
            status = WARN
        else:
            status, detail = OK, "disabled (not registered)"
        checks.append(Check(f"task.{label}", status, detail))
    return checks


def run(cfg: dict[str, Any] | None = None, fast: bool = False) -> list[Check]:
    """Run the readiness checks. `fast=True` skips the broker/keyring check — the only one that makes
    an authenticated broker round-trip (a 35s-timeout subprocess) — so it's safe to poll on a short
    cadence (the `dashboard --serve` live-checks card) without hammering the broker or its rate limits.
    Everything else (interpreter, clock, paths, config, paper-DB writability, task registration,
    streamer liveness, Dolt reachability, notify) is local/cheap and always runs."""
    checks: list[Check] = []
    try:
        cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    except Exception as exc:
        return [Check("config", FAIL, f"Could not load config.json: {exc}")]

    # interpreter
    # portable_path, like every other path this command prints. The interpreter lives under the user
    # home on Windows, so the raw value carries the username onto the dashboard's System card — the
    # one surface in the suite that renders doctor's details verbatim to a browser.
    checks.append(Check("python", OK, f"{sys.version.split()[0]} @ {cfgmod.portable_path(sys.executable)}"))

    # cherrypick-core is a required, installed dependency (packages/core) -- not a submodule with a
    # graceful degrade path. Without it, every module and most of this orchestrator's own read
    # surfaces fail; catching it here turns a confusing ModuleNotFoundError deep in a detached
    # subprocess (the streamer launches with output -> DEVNULL) into a visible doctor red instead.
    if importlib.util.find_spec("cherrypick.core") is None:
        checks.append(
            Check("cherrypick.core", FAIL, "not installed -- run: pip install -e <repo>/packages/core")
        )
    else:
        checks.append(Check("cherrypick.core", OK, "installed"))

    # onboarding: broker credentials per module, keyring-only (presence + source, never values).
    # WARN (yellow), never FAIL: a paper-only suite runs fine without broker credentials
    # everywhere except earnings' scanner — the confirmed onboarding-redesign decision.
    try:
        from . import accounts as _accounts

        ob = _accounts.onboarding_status(cfg)
        if ob.get("ok"):
            missing = [m["module"] for m in ob["modules"] if m["credentials"] == "missing"]
            parts = [
                f"{m['module']}: creds {m['credentials']}"
                + (f", account {m['account']} ({m['account_source']})" if m["account"] else "")
                for m in ob["modules"]
                if m["credentials"] != "n/a"
            ]
            detail = "; ".join(parts) or "no modules with a keyring service"
            checks.append(
                Check(
                    "onboarding",
                    WARN if missing else OK,
                    detail + (" — run `cherrypick connect`" if missing else ""),
                )
            )
        else:
            checks.append(Check("onboarding", WARN, ob.get("error", "keyring unavailable")))
    except Exception as exc:  # the panel is informational; it must never break doctor
        checks.append(Check("onboarding", WARN, f"status unavailable: {exc}"))

    # clock / timezone
    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays()
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
                cfgmod.portable_path(root) if root.exists() else missing_detail,
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
                # `~/.cherrypick/config/<mod>.json`, not the resolved absolute path: the sibling
                # `.path` and `.paper_db` checks already render portably, and this one was the
                # outlier putting the username on screen.
                cfgmod.portable_path(mc) if mc else "module config not found (home or in-repo)",
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

        # eval activity — during RTH, is the loop actually evaluating candidates and deciding sensibly
        # (not just writing a file)? Session-gated: off-hours a stopped loop is expected, not a fault.
        if timeutil.is_session_window(now, holidays):
            act = eval_activity.for_module(
                mcfg, name, now.date().isoformat(), cfg.get("eval_activity", {}).get("window_minutes", 30)
            )
            if act is not None:
                ea_cfg = cfg.get("eval_activity", {})
                status, detail = eval_activity.assess(
                    act,
                    window_min=ea_cfg.get("window_minutes", 30),
                    eval_stale_min=ea_cfg.get("stale_minutes", 10),
                    error_frac_warn=ea_cfg.get("error_fraction", 0.5),
                )
                mark = OK if status == eval_activity.OK else WARN
                checks.append(Check(f"{name}.eval_activity", mark, detail))

        # scheduled task(s) — or, on a supervisor-driven box, the matching supervisor job(s)
        task_keys = (("task_name", "paper"), ("entry_task_name", "entry"), ("exit_task_name", "exit"))
        for tkey, suffix in task_keys:
            tn = paper.get(tkey)
            if not tn:
                continue
            if _supervisor_driving():
                checks.append(_job_check(f"{name}.task[{name}-{suffix}]", f"{name}-{suffix}"))
                continue
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
                r = _run(root, [*cfgmod.broker_tool(mcfg, name), "get_connection_status"], timeout=35)
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
                if _supervisor_driving():
                    checks.append(_job_check(f"{name}.dolt_service", f"{name}-dolt"))
                else:
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

    # watchdog task (or its supervisor job)
    wt = cfg.get("watchdog", {}).get("task_name")
    if wt:
        if _supervisor_driving():
            checks.append(_job_check("watchdog.task", "watchdog"))
        else:
            registered = tasks.exists(wt)
            checks.append(
                Check(
                    "watchdog.task",
                    OK if registered else WARN,
                    "registered" if registered else "not registered (run: cherrypick install)",
                )
            )

    checks.extend(_supervisor_checks(cfg, fast))
    checks.extend(_suite_task_checks(cfg))

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

    # standalone market-data producer (top-level `streamer`): the suite's single stream-cache writer.
    # Module-owned streamers are checked in the modules loop above; this covers the standalone producer
    # (the case where modules.<m>.streamer is disabled), so its health shows on `cherrypick doctor` and
    # the served dashboard's system card. Reads the streamer's own --status (its cache/state), never the
    # broker — the same cheap local subprocess the module-streamer check uses.
    producer = cfg.get("streamer") or {}
    if producer.get("enabled"):
        root = cfgmod.module_root(producer, "streamer")
        if not root.exists():
            checks.append(Check("streamer", FAIL, f"checkout not found at {cfgmod.portable_path(root)}"))
        else:
            try:
                r = _run(root, producer["status_argv"], timeout=15)
                status = first_json(r.stdout) if r.returncode == 0 else {}
                running = bool(status.get("running"))
                age = next(
                    (
                        status[k]
                        for k in ("oldest_event_age_s", "stale_age_s")
                        if isinstance(status.get(k), (int, float))
                    ),
                    None,
                )
                if not running:
                    checks.append(Check("streamer", WARN, "not running (start with: cherrypick install)"))
                else:
                    fresh = f"last event {age:.0f}s ago" if age is not None else "running"
                    limit = producer.get("stale_restart_seconds", 240)
                    # A connected-but-silent streamer is the 34-hour-stall failure — flag it, but only
                    # during market hours (off-hours a quiet feed is expected, not a fault).
                    if age is not None and age > limit and timeutil.is_market_hours():
                        checks.append(
                            Check(
                                "streamer",
                                WARN,
                                f"running but silent — {fresh} (watchdog restarts at {limit}s)",
                            )
                        )
                    else:
                        quiet = "  (quiet off-hours)" if age is not None and age > limit else ""
                        checks.append(Check("streamer", OK, f"running, {fresh}{quiet}"))
            except Exception as exc:
                checks.append(Check("streamer", WARN, f"status error: {exc}"))

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
