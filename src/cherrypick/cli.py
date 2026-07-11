#!/usr/bin/env python3
"""cherrypick - unattended paper-trading orchestrator (CLI entry point).

Drives sibling trading modules (MEICAgent, EarningsAgent) in place for hands-off PAPER data
collection, with a watchdog + notifications so a walk-away user is told (or at least has it logged)
whenever something stalls. Never touches live trading; never sits on a module's loop decision path.

Subcommands:
  init                 Scaffold + validate config.json (first-run onboarding); --force to overwrite.
  install              Register all scheduled tasks (MEIC paper, earnings entry/exit, watchdog,
                       fast trade-notify) and start the streamer if it is down.
  uninstall            Remove cherrypick-managed scheduled tasks.
  status               Show task registration, last watchdog heartbeat, and last earnings run.
  doctor               One green/red readiness check (read-only).
  watchdog             Run one watchdog pass (this is what the scheduled task invokes).
  report               Unified cross-module paper P&L (read-only): totals + per-profile breakdown.
  dashboard            Regenerate the read-only status dashboard (static HTML: health + P&L + logs).
  calibrate            Per-profile paper calibration readings + advisory promotion recommendations.
  run-earnings-entry   Run EarningsAgent's paper entry now (invoked by its daily task).
  run-earnings-exit    Run EarningsAgent's paper exit now (invoked by its daily task).
  ensure-dolt          Start any module's declared Dolt server if down (invoked by its keep-alive task).
  notify-test          Fire a test notification through all configured channels.
  notify-trades        Push new paper entries/exits to the trade channels (also runs on each watchdog tick).
  secrets-set          Store a slack/discord webhook URL in the OS keyring (--channel; --url or prompt).
  secrets-status       Show which push-channel webhooks are configured (secret-free).
  secrets-delete       Remove a stored webhook (--channel).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cherrypick.notify import Notifier
from cherrypick.notify import secrets as notify_secrets
from cherrypick.orchestrator import (
    calibrate,
    dashboard,
    doctor,
    init,
    report,
    serve,
    tasks,
    timeutil,
    trade_notifier,
    watchdog,
)
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator.util import first_json

# The OS scheduler invokes the in-place launcher `pythonw <repo>/run.py <cmd>`. This module is
# <repo>/src/cherrypick/cli.py, so the repo-root launcher is two parents up. (Renamed from
# cherrypick.py to run.py in the src-layout packaging — a root cherrypick.py would shadow the
# cherrypick namespace package — so scheduled tasks must be re-registered via `python run.py install`.)
_LAUNCHER = Path(__file__).resolve().parents[2] / "run.py"


def _emit(obj) -> None:
    # Scheduled tasks run under pythonw.exe where sys.stdout is None; the real work (logs, heartbeats,
    # notifications) is already done by the time we get here, so emitting is best-effort only.
    if sys.stdout is None:
        return
    try:
        json.dump(obj, sys.stdout, indent=2, default=str)
        print()
    except (ValueError, OSError):
        pass


def _module_log(name: str) -> Path:
    return cfgmod.log_file(f"{name}.log")


def _append_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **record}) + "\n")


# --------------------------------------------------------------------------- install/uninstall
def _ensure_module_checkout(name: str, mcfg: dict) -> dict:
    """Make sure a module's code is on disk before we register its tasks.

    Policy (confirmed): clone only when the managed checkout is absent; never touch an existing one.
    An explicit `path` is a dev-managed working copy — we verify it exists but never clone over it.
    Runs only at install time via the OS shell's `git`, so it stays off the watchdog reliability path.
    """
    root = cfgmod.module_root(mcfg, name)
    if mcfg.get("path"):
        detail = f"in-place path {root}" + ("" if root.exists() else " MISSING")
        return {"ok": root.exists(), "detail": detail}
    if root.exists():
        return {"ok": True, "detail": f"already present at {root}"}
    repo = mcfg.get("repo")
    if not repo:
        return {"ok": False, "detail": "no 'repo' and no 'path' configured; cannot locate module"}
    root.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "clone"]
    if mcfg.get("ref"):
        argv += ["--branch", str(mcfg["ref"])]
    argv += [str(repo), str(root)]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "detail": f"git clone failed: {(r.stderr or r.stdout).strip()[:200]}"}
    sm = subprocess.run(
        ["git", "submodule", "update", "--init"], cwd=str(root), capture_output=True, text=True
    )
    note = "" if sm.returncode == 0 else f"; submodule init warn: {(sm.stderr or '').strip()[:120]}"
    return {"ok": True, "detail": f"cloned to {root}{note}"}


def cmd_install(cfg) -> None:
    results = {}
    pyw = cfgmod.pythonw_exe()
    modules = cfgmod.enabled_modules(cfg)

    for name, mcfg in modules.items():
        # Materialize the module checkout first; skip task registration if it isn't on disk.
        chk = _ensure_module_checkout(name, mcfg)
        results[f"{name}.checkout"] = chk
        if not chk.get("ok"):
            continue
        root = cfgmod.module_root(mcfg, name)
        paper = mcfg.get("paper", {})
        kind = paper.get("kind")

        if kind == "self_healing":
            # MEIC manages its own self-healing task; just invoke its installer in place.
            r = subprocess.run(
                [cfgmod.python_exe(), *paper["install_argv"]], cwd=str(root), capture_output=True, text=True
            )
            results[f"{name}.paper_task"] = {
                "ok": r.returncode == 0,
                "detail": (r.stdout or r.stderr).strip(),
            }
            # the module registers its own task; clear its battery guards too (laptop durability)
            if r.returncode == 0 and paper.get("task_name"):
                tasks.allow_on_battery(paper["task_name"])
            # start streamer if down
            streamer = mcfg.get("streamer", {})
            if streamer.get("enabled"):
                results[f"{name}.streamer"] = _ensure_streamer(root, streamer)

        elif kind == "cherrypick_scheduled":
            # cherrypick owns the earnings schedule (the module has none).
            entry_tr = tasks.build_tr(pyw, str(_LAUNCHER), "run-earnings-entry")
            exit_tr = tasks.build_tr(pyw, str(_LAUNCHER), "run-earnings-exit")
            results[f"{name}.entry_task"] = tasks.create_daily_task(
                paper["entry_task_name"], entry_tr, paper["entry_time"]
            )
            results[f"{name}.exit_task"] = tasks.create_daily_task(
                paper["exit_task_name"], exit_tr, paper["exit_time"]
            )

        # optional cherrypick-managed Dolt keep-alive (portable, idempotent; run_now starts it now)
        svc = paper.get("dolt_service")
        if svc and svc.get("task_name"):
            svc_tr = tasks.build_tr(pyw, str(_LAUNCHER), "ensure-dolt")
            results[f"{name}.dolt_service"] = tasks.create_minute_task(
                svc["task_name"], svc_tr, svc.get("interval_minutes", 5)
            )

    # watchdog task
    wd = cfg.get("watchdog", {})
    if wd.get("task_name"):
        wd_tr = tasks.build_tr(pyw, str(_LAUNCHER), "watchdog")
        results["watchdog_task"] = tasks.create_minute_task(
            wd["task_name"], wd_tr, wd.get("interval_minutes", 10)
        )

    # dedicated low-latency trade-notify task (polls paper DBs far more often than the watchdog)
    tn = cfg.get("trade_notify", {})
    if tn.get("task_name"):
        tn_tr = tasks.build_tr(pyw, str(_LAUNCHER), "notify-trades")
        results["trade_notify_task"] = tasks.create_minute_task(
            tn["task_name"], tn_tr, tn.get("interval_minutes", 2)
        )

    _emit({"ok": all(v.get("ok", True) for v in results.values()), "installed": results})


def _ensure_streamer(root: Path, streamer: dict) -> dict:
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *streamer["status_argv"]],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else False
    except Exception:
        running = False
    if running:
        return {"ok": True, "detail": "already running"}
    started = watchdog._start_streamer(root, streamer["start_argv"])
    return {"ok": started, "detail": "started" if started else "start failed"}


def cmd_uninstall(cfg) -> None:
    results = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        root = cfgmod.module_root(mcfg, name)
        paper = mcfg.get("paper", {})
        if paper.get("kind") == "self_healing" and paper.get("uninstall_argv"):
            r = subprocess.run(
                [cfgmod.python_exe(), *paper["uninstall_argv"]], cwd=str(root), capture_output=True, text=True
            )
            results[f"{name}.paper_task"] = {
                "ok": r.returncode == 0,
                "detail": (r.stdout or r.stderr).strip(),
            }
        for tkey in ("entry_task_name", "exit_task_name"):
            if paper.get(tkey):
                results[f"{name}.{tkey}"] = tasks.delete(paper[tkey])
        svc = paper.get("dolt_service")
        if svc and svc.get("task_name"):
            results[f"{name}.dolt_service"] = tasks.delete(svc["task_name"])
    if cfg.get("watchdog", {}).get("task_name"):
        results["watchdog_task"] = tasks.delete(cfg["watchdog"]["task_name"])
    if cfg.get("trade_notify", {}).get("task_name"):
        results["trade_notify_task"] = tasks.delete(cfg["trade_notify"]["task_name"])
    _emit({"ok": True, "removed": results, "note": "streamer (if running) left untouched"})


# --------------------------------------------------------------------------- status
def cmd_status(cfg) -> None:
    out = {"tasks": {}, "heartbeats": {}}
    for mcfg in cfgmod.enabled_modules(cfg).values():
        paper = mcfg.get("paper", {})
        for tkey in ("task_name", "entry_task_name", "exit_task_name"):
            if paper.get(tkey):
                out["tasks"][paper[tkey]] = tasks.query_verbose(paper[tkey])
        svc_task = paper.get("dolt_service", {}).get("task_name")
        if svc_task:
            out["tasks"][svc_task] = tasks.query_verbose(svc_task)
    for section in ("watchdog", "trade_notify"):
        tn = cfg.get(section, {}).get("task_name")
        if tn:
            out["tasks"][tn] = tasks.query_verbose(tn)
    for hb in ("watchdog.last.json", "earnings_entry.last.json", "earnings_exit.last.json"):
        p = cfgmod.STATE_DIR / hb
        if p.exists():
            try:
                out["heartbeats"][hb] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                out["heartbeats"][hb] = {"error": "unreadable"}
    _emit(out)


# --------------------------------------------------------------------------- dolt keep-alive
def _dolt_service_dir(svc: dict) -> Path:
    """Resolve a `dolt_service.data_dir` portably: expand `~`, and resolve a relative path against the
    cherrypick runtime ROOT. Config must not carry absolute/machine paths (a portability guardrail)."""
    p = Path(svc.get("data_dir", "")).expanduser()
    if not p.is_absolute():
        p = (cfgmod.ROOT / p).resolve()
    return p


def _start_dolt(data_dir: Path) -> bool:
    """Launch `dolt sql-server` detached from data_dir (benign, no window; dolt refuses to double-bind
    the port). `dolt` comes from PATH so no install path is hardcoded."""
    if not data_dir.exists():
        return False
    try:
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
        subprocess.Popen(
            ["dolt", "sql-server"],
            cwd=str(data_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def _ensure_dolt(cfg) -> None:
    """Idempotently ensure each module's declared Dolt server is up. Invoked by the per-module
    `dolt_service` keep-alive task. Decision is stdlib-only (socket reachability); remediation is a
    benign, non-trading subprocess start — it never touches the broker or a paper DB. Keeping the port
    occupied also stops a module runner from self-starting an empty Dolt in the wrong directory."""
    results = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        svc = paper.get("dolt_service")
        if not svc:
            continue
        host = paper.get("dolt_host", "127.0.0.1")
        port = paper.get("dolt_port", 3306)
        if watchdog._dolt_reachable(host, port):
            results[name] = {"ok": True, "detail": "already up"}
            continue
        data_dir = _dolt_service_dir(svc)
        started = _start_dolt(data_dir)
        results[name] = {
            "ok": started,
            "detail": f"started in {data_dir}" if started else f"start failed (missing dir? {data_dir})",
        }
    _emit({"ok": all(v["ok"] for v in results.values()) if results else True, "dolt": results})


# --------------------------------------------------------------------------- earnings runners
def _run_earnings(cfg, phase: str) -> None:
    """phase = 'entry' | 'exit'. Invoked by the daily scheduled task."""
    tz = cfg.get("timezone", "America/New_York")
    holidays = timeutil.load_holidays(cfg, cfgmod.module_root)
    now = timeutil.now_et(tz)
    today = now.strftime("%Y-%m-%d")
    mcfg = cfg.get("modules", {}).get("earnings")
    hb_path = cfgmod.state_file(f"earnings_{phase}.last.json")
    log_path = _module_log("earnings_paper")

    if not mcfg or not mcfg.get("enabled"):
        _emit({"ok": True, "skipped": "earnings module disabled"})
        return
    if not timeutil.is_trading_day(now, holidays):
        rec = {"date": today, "ok": True, "skipped": "not a trading day", "phase": phase}
        hb_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        _append_log(log_path, rec)
        _emit(rec)
        return

    paper = mcfg["paper"]
    root = cfgmod.module_root(mcfg, "earnings")
    argv = [a.replace("{today}", today) for a in paper[f"{phase}_argv"]]

    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *argv], cwd=str(root), capture_output=True, text=True, timeout=1800
        )
        try:
            result = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            result = {"raw": (r.stdout or "")[:2000]}
        ok = r.returncode == 0 and result.get("ok", True) is not False
        error = None if ok else (result.get("error") or (r.stderr or "")[:500])
    except Exception as exc:
        ok, result, error = False, {}, f"{type(exc).__name__}: {exc}"

    rec = {
        "date": today,
        "phase": phase,
        "ok": ok,
        "error": error,
        "opened": (result or {}).get("opened"),
        "closed": (result or {}).get("closed"),
    }
    hb_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    _append_log(log_path, {**rec, "result": result})

    if not ok:
        Notifier(cfg.get("notify")).notify(
            "CRITICAL",
            f"earnings.{phase}",
            f"Earnings paper {phase} failed",
            f"{error or 'see logs/earnings_paper.log'}",
        )

    # Push any fills this run produced right away instead of waiting for the next trade-notify tick.
    # Best-effort: a notify hiccup must never fail the scheduled earnings run itself.
    if ok:
        try:
            trade_notifier.run(cfg)
        except Exception:
            pass
    _emit(rec)


# --------------------------------------------------------------------------- misc
def cmd_init(force: bool) -> None:
    result = init.run(force=force)
    _emit(result)
    sys.exit(0 if result.get("ok") else 1)


def cmd_doctor(cfg) -> None:
    checks = doctor.run(cfg)
    report, worst = doctor.format_report(checks)
    print(report)
    sys.exit(0 if worst < 2 else 1)


def cmd_watchdog(cfg) -> None:
    _emit(watchdog.run(cfg))


def cmd_notify_trades(cfg) -> None:
    _emit(trade_notifier.run(cfg))


def cmd_report(cfg) -> None:
    _emit(report.run(cfg))


def cmd_dashboard(cfg, args) -> None:
    """One-shot static render (default), or a localhost live server with --serve.

    Serve mode reuses the same build_model/_render_html as the file render; it adds a live GEX section
    (polled from the cherrypick-gex module) when `gex.enabled` is set. Blocks until Ctrl-C.
    """
    if getattr(args, "serve", False):
        _emit(serve.serve(cfg, host=args.host, port=args.port, open_browser=not args.no_browser))
        return
    _emit(dashboard.run(cfg))


def cmd_calibrate(cfg) -> None:
    _emit(calibrate.run(cfg))


def cmd_notify_test(cfg) -> None:
    res = Notifier(cfg.get("notify")).notify(
        "INFO",
        "notify_test",
        "Notification test",
        "If you can see this (and it is in logs/notify.log), cherrypick can reach you.",
    )
    _emit({"ok": True, "channels": res})


def cmd_secrets_set(channel: str | None, url: str | None) -> None:
    if channel not in notify_secrets.SUPPORTED:
        _emit({"ok": False, "error": f"--channel must be one of {list(notify_secrets.SUPPORTED)}"})
        sys.exit(2)
    if not url:
        # Read without echo / shell history. A webhook URL is a bearer secret.
        url = getpass.getpass(f"Paste the {channel} webhook URL (input hidden): ").strip()
    if not url:
        _emit({"ok": False, "error": "no URL provided"})
        sys.exit(2)
    notify_secrets.set_webhook(channel, url)
    _emit({"ok": True, "channel": channel, "stored_in": "OS keyring", "status": notify_secrets.status()})


def cmd_secrets_status() -> None:
    _emit({"ok": True, "keyring_service": notify_secrets.SERVICE_NAME, "webhooks": notify_secrets.status()})


def cmd_secrets_delete(channel: str | None) -> None:
    if channel not in notify_secrets.SUPPORTED:
        _emit({"ok": False, "error": f"--channel must be one of {list(notify_secrets.SUPPORTED)}"})
        sys.exit(2)
    removed = notify_secrets.delete_webhook(channel)
    _emit({"ok": removed, "channel": channel, "status": notify_secrets.status()})


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cherrypick", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "init",
            "install",
            "uninstall",
            "status",
            "doctor",
            "watchdog",
            "report",
            "dashboard",
            "calibrate",
            "run-earnings-entry",
            "run-earnings-exit",
            "ensure-dolt",
            "notify-test",
            "notify-trades",
            "secrets-set",
            "secrets-status",
            "secrets-delete",
        ],
    )
    parser.add_argument(
        "--channel",
        choices=list(notify_secrets.SUPPORTED),
        help="Push channel for secrets-set/secrets-delete",
    )
    parser.add_argument(
        "--url", default=None, help="Webhook URL for secrets-set (omit to be prompted without echo)"
    )
    parser.add_argument("--force", action="store_true", help="For init: overwrite an existing config.json")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="For dashboard: run a localhost live server instead of writing a static file",
    )
    parser.add_argument("--host", default=None, help="For dashboard --serve: bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="For dashboard --serve: bind port (def 8787)")
    parser.add_argument(
        "--no-browser", action="store_true", help="For dashboard --serve: do not open a browser"
    )
    args = parser.parse_args()

    # `init` scaffolds config.json, so it must run before the config pre-load (a fresh user has none).
    if args.command == "init":
        cmd_init(args.force)
        return

    cfg = cfgmod.load_config()
    dispatch = {
        "install": lambda: cmd_install(cfg),
        "uninstall": lambda: cmd_uninstall(cfg),
        "status": lambda: cmd_status(cfg),
        "doctor": lambda: cmd_doctor(cfg),
        "watchdog": lambda: cmd_watchdog(cfg),
        "report": lambda: cmd_report(cfg),
        "dashboard": lambda: cmd_dashboard(cfg, args),
        "calibrate": lambda: cmd_calibrate(cfg),
        "notify-trades": lambda: cmd_notify_trades(cfg),
        "run-earnings-entry": lambda: _run_earnings(cfg, "entry"),
        "run-earnings-exit": lambda: _run_earnings(cfg, "exit"),
        "ensure-dolt": lambda: _ensure_dolt(cfg),
        "notify-test": lambda: cmd_notify_test(cfg),
        "secrets-set": lambda: cmd_secrets_set(args.channel, args.url),
        "secrets-status": lambda: cmd_secrets_status(),
        "secrets-delete": lambda: cmd_secrets_delete(args.channel),
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
