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
  doctor               One green/red readiness check (read-only). --fast skips the authenticated
                       broker round-trip (local/offline checks only).
  watchdog             Run one watchdog pass (this is what the scheduled task invokes).
  report               Unified cross-module paper P&L (read-only): totals + per-profile breakdown.
                       --eod (today ET) or --date YYYY-MM-DD restricts to one session; default all-time.
  eod-digest           Write the suite end-of-day digest (logs/eod-digest-<day>.md): one session's
                       cross-module P&L + links to each module's paper-eod file. --date; default today.
  notify-eod           Write the digest and push a one-line summary through the notify channels (the
                       scheduled cherrypick-eod-digest task runs this). --date; default today.
  reconcile            Paper↔live isolation guard: query the real broker account (read-only) and flag
                       any open positions/BP a paper-only suite shouldn't have. On-demand; never trades.
  connect              Guided per-module onboarding (--module): set OAuth creds (via the module's own
                       hidden-input tool) and select the live-trading account. Never trades.
  account              List (--module), set (--set <last4|index>), or clear (--clear) a module's
                       designated live-trading account. Masked; never trades.
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
    accounts,
    calibrate,
    connect,
    dashboard,
    doctor,
    eod_digest,
    init,
    migrate,
    reconcile,
    report,
    serve,
    tasks,
    timeutil,
    trade_notifier,
    watchdog,
)
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator.util import CREATE_NO_WINDOW, first_json

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
    r = subprocess.run(argv, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        return {"ok": False, "detail": f"git clone failed: {(r.stderr or r.stdout).strip()[:200]}"}
    sm = subprocess.run(
        ["git", "submodule", "update", "--init"],
        cwd=str(root),
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    note = "" if sm.returncode == 0 else f"; submodule init warn: {(sm.stderr or '').strip()[:120]}"
    return {"ok": True, "detail": f"cloned to {root}{note}"}


def cmd_install(cfg) -> None:
    results = {}
    pyw = cfgmod.pythonw_exe()
    modules = cfgmod.enabled_modules(cfg)
    # Daily task times (entry/exit/digest) are expressed in the market timezone but the OS scheduler
    # fires on local time — convert so e.g. 15:45 ET registers correctly on a non-ET host.
    tz = cfg.get("timezone", "America/New_York")

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
                paper["entry_task_name"], entry_tr, timeutil.to_local_hhmm(paper["entry_time"], tz)
            )
            results[f"{name}.exit_task"] = tasks.create_daily_task(
                paper["exit_task_name"], exit_tr, timeutil.to_local_hhmm(paper["exit_time"], tz)
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

    # suite end-of-day digest: writes the roll-up + pushes a summary once per afternoon. ON by
    # default (opt out with "eod_digest": {"enabled": false}); the default name/time mean a config
    # predating the feature still gets it scheduled here.
    ed = cfgmod.eod_digest_settings(cfg)
    if ed["enabled"]:
        ed_tr = tasks.build_tr(pyw, str(_LAUNCHER), "notify-eod")
        results["eod_digest_task"] = tasks.create_daily_task(
            ed["task_name"], ed_tr, timeutil.to_local_hhmm(ed["at"], tz)
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
    # Always attempt the digest task by its resolved name (idempotent) so opting out *after* an
    # install still removes a previously-registered task.
    results["eod_digest_task"] = tasks.delete(cfgmod.eod_digest_settings(cfg)["task_name"])
    _emit({"ok": True, "removed": results, "note": "streamer (if running) left untouched"})


# --------------------------------------------------------------------------- status
def cmd_status(cfg) -> None:
    out = {"tasks": tasks.registry_snapshot(cfg), "heartbeats": {}}
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


def cmd_doctor(cfg, fast: bool = False) -> None:
    checks = doctor.run(cfg, fast=fast)
    report, worst = doctor.format_report(checks)
    print(report)
    sys.exit(0 if worst < 2 else 1)


def cmd_watchdog(cfg) -> None:
    _emit(watchdog.run(cfg))


def _account_table(listing: dict) -> None:
    """Print a masked account listing for `cherrypick account`."""
    if not listing.get("ok"):
        print(f"account: {listing.get('error')}")
        return
    live = listing.get("live_enabled")
    live_str = "on" if live is True else "off" if live is False else "unknown"
    print(f"{listing['module']} — live trading: {live_str}")
    desig = listing.get("designated")
    print(f"designated live-trading account: {desig or '(none - SDK picks the first account)'}")
    for i, a in enumerate(listing.get("accounts", []), 1):
        mark = "  <- designated" if a.get("designated") else ""
        bits = [a["account"]]
        if a.get("nickname"):
            bits.append(str(a["nickname"]))
        if a.get("type"):
            bits.append(str(a["type"]))
        print(f"  {i}) {'  '.join(bits)}{mark}")


def cmd_account(cfg, args) -> None:
    """List / set / clear a module's designated live-trading account (masked)."""
    module = args.module
    if not module:
        _emit({"ok": False, "error": "account requires --module <name>"})
        sys.exit(2)
    if args.clear:
        _emit(accounts.clear_account(cfg, module))
        return
    if args.set:
        # Setting the destination for LIVE orders — confirm unless --yes.
        if not args.yes:
            print(
                f"This designates the account {module} will use for LIVE orders. cherrypick never places"
                f" trades; it only records the destination."
            )
            if (
                input(
                    f"Type 'yes' to set {module}'s live-trading account to selection {args.set!r}: "
                ).strip()
                != "yes"
            ):
                _emit({"ok": False, "error": "aborted"})
                return
        _emit(accounts.set_account(cfg, module, args.set))
        return
    _account_table(accounts.list_accounts(cfg, module))


def cmd_connect(cfg, args) -> None:
    module = args.module
    if not module:
        _emit({"ok": False, "error": "connect requires --module <name>"})
        sys.exit(2)
    _emit(connect.run(cfg, module))


def cmd_reconcile(cfg) -> None:
    result = reconcile.run(cfg)
    report_text, _worst = reconcile.format_report(result)
    print(report_text)
    # exit by verdict: FLAT -> 0, DRIFT (real account not flat) -> 1, UNKNOWN (couldn't check) -> 2
    sys.exit({reconcile.FLAT: 0, reconcile.DRIFT: 1, reconcile.UNKNOWN: 2}.get(result.get("verdict"), 2))


def cmd_notify_trades(cfg) -> None:
    _emit(trade_notifier.run(cfg))


def _resolve_session(args) -> str | None:
    """The session an EOD-scoped command targets: an explicit --date wins, else --eod means today
    (ET), else None (the all-time cumulative view)."""
    if getattr(args, "date", None):
        return args.date
    if getattr(args, "eod", False):
        return timeutil.now_et().strftime("%Y-%m-%d")
    return None


def cmd_report(cfg, args) -> None:
    _emit(report.run(cfg, session=_resolve_session(args)))


def cmd_eod_digest(cfg, args) -> None:
    # --date selects the day; otherwise today (ET). (--eod is redundant here but accepted.)
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    _emit(eod_digest.run(cfg, day=day))


def cmd_notify_eod(cfg, args) -> None:
    """Write the suite EOD digest, then push a one-line summary through the notify channels. This is
    what the scheduled `cherrypick-eod-digest` task runs. The digest write and the push are both
    best-effort: a notify hiccup never fails the file write."""
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    res = eod_digest.run(cfg, day=day)
    suite = res.get("suite", {})
    net = suite.get("net_pnl")
    money = "-" if net is None else (f"-${abs(net):,.2f}" if net < 0 else f"${net:,.2f}")
    # The pushed message can leave the machine (Slack/Discord), so it names only the report *file*,
    # never its absolute path — an absolute path leaks the OS username and directory layout to a
    # third-party service. The full path stays in this command's local stdout envelope below.
    digest_name = Path(res.get("digest", "")).name or f"eod-digest-{day}.md"
    message = (
        f"Paper suite {day}: {suite.get('trades', 0)} trades closed, net {money}, "
        f"{suite.get('wins', 0)}W/{suite.get('losses', 0)}L. See {digest_name} in the cherrypick logs."
    )
    channels = Notifier(cfg.get("notify")).notify("INFO", f"eod_{day}", f"EOD digest {day}", message)
    _emit({"ok": True, "session": day, "digest": res.get("digest"), "suite": suite, "channels": channels})


def cmd_dashboard(cfg, args) -> None:
    """One-shot static render (default), or a localhost live server with --serve.

    Serve mode reuses the same build_model/_render_html as the file render; it adds a live GEX section
    (polled from the cherrypick-gex module) when `gex.enabled` is set. Blocks until Ctrl-C.
    """
    if getattr(args, "serve", False):
        _emit(serve.serve(cfg, host=args.host, port=args.port, open_browser=not args.no_browser))
        return
    _emit(dashboard.run(cfg))


def cmd_migrate_home(cfg, apply: bool) -> None:
    """Move config files into ~/.cherrypick and sweep regenerable leftovers out of the checkouts.
    Dry-run by default (prints the plan and touches nothing); pass --apply to perform it."""
    res = migrate.run(cfg, dry_run=not apply)
    mode = "dry-run - nothing changed" if res["dry_run"] else "applied"
    verb = "would move" if res["dry_run"] else "moved"
    swept = "would sweep" if res["dry_run"] else "swept"
    print(f"cherrypick migrate-home ({mode})")
    for mv in res["moved"]:
        print(f"  {verb} config: {mv['src']} -> {mv['dest']}")
    for d in res["deleted"]:
        print(f"  {swept}: {d}")
    for db in res["db_review"]:
        print(f"  REVIEW (left in place — may hold data): {db}")
    if not (res["moved"] or res["deleted"] or res["db_review"]):
        print("  nothing to migrate — already clean")
    elif res["dry_run"]:
        print("Re-run with --apply to perform the migration.")


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
            "eod-digest",
            "notify-eod",
            "reconcile",
            "connect",
            "account",
            "dashboard",
            "migrate-home",
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
        "--date", default=None, help="For report/eod-digest: a session day 'YYYY-MM-DD' (default today)"
    )
    parser.add_argument(
        "--eod", action="store_true", help="For report: restrict to today's (ET) session instead of all-time"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="For doctor: skip the authenticated broker check (local/offline checks only)",
    )
    parser.add_argument("--module", default=None, help="For connect/account: which module to target")
    parser.add_argument(
        "--set",
        dest="set",
        default=None,
        help="For account: designate this account (a last-4 or 1-based index)",
    )
    parser.add_argument("--clear", action="store_true", help="For account: unset the designated account")
    parser.add_argument("--yes", action="store_true", help="For account --set: skip the confirmation prompt")
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
    parser.add_argument(
        "--apply", action="store_true", help="For migrate-home: perform the move (default is a dry run)"
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
        "doctor": lambda: cmd_doctor(cfg, fast=args.fast),
        "watchdog": lambda: cmd_watchdog(cfg),
        "report": lambda: cmd_report(cfg, args),
        "eod-digest": lambda: cmd_eod_digest(cfg, args),
        "notify-eod": lambda: cmd_notify_eod(cfg, args),
        "reconcile": lambda: cmd_reconcile(cfg),
        "connect": lambda: cmd_connect(cfg, args),
        "account": lambda: cmd_account(cfg, args),
        "dashboard": lambda: cmd_dashboard(cfg, args),
        "migrate-home": lambda: cmd_migrate_home(cfg, args.apply),
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
