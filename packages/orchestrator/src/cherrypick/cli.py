#!/usr/bin/env python3
"""cherrypick - unattended paper-trading orchestrator (CLI entry point).

Drives sibling trading modules (MEICAgent, EarningsAgent) in place for hands-off PAPER data
collection, with a watchdog + notifications so a walk-away user is told (or at least has it logged)
whenever something stalls. Never touches live trading; never sits on a module's loop decision path.

Subcommands:
  init                 Scaffold + validate config.json (first-run onboarding); --force to overwrite.
  install              Register the ONE OS anchor task (cherrypick-supervisor), start the supervisor
                       daemon (which derives and fires every job from config), delete legacy per-job
                       tasks, and start the streamer/services if down. Refuses while flies is
                       live-armed for today (--force overrides).
  uninstall            Anchor task off first, then stop the supervisor, remove legacy tasks, and
                       stop managed services.
  status               Show the supervisor job registry + heartbeats (falls back to the legacy
                       schtasks snapshot on a pre-cutover box).
  doctor               One green/red readiness check (read-only). --fast skips the authenticated
                       broker round-trip (local/offline checks only).
  watchdog             Run one watchdog pass (the supervisor's 10-minute job invokes this).
  supervise            Run the supervisor daemon loop in the foreground (--stop asks a running
                       daemon to exit). The anchor task keeps it alive via ensure-supervisor.
  ensure-supervisor    The anchor task's probe: restart the supervisor if its heartbeat is stale;
                       escalate one CRITICAL after 3 consecutive failed probes.
  streamer-health      One streamer-liveness pass (the supervisor's 60s in-session job) — the
                       whole-session replacement for the retired pre-open task.
  preopen-check        Deprecated alias for streamer-health (honors the legacy preopen flag).
  report               Unified cross-module paper P&L (read-only): totals + per-profile breakdown.
                       --eod (today ET) or --date YYYY-MM-DD restricts to one session; default all-time.
                       --live reads the live-tagged ledgers (modules' live_db) instead — a separate
                       view that never feeds calibrate/promotion (those read paper only).
  eod-digest           Write the suite end-of-day digest (logs/eod-digest-<day>.md): one session's
                       cross-module P&L + links to each module's paper-eod file. --date; default today.
  notify-eod           Write the digest and push a one-line summary through the notify channels (the
                       scheduled cherrypick-eod-digest task runs this). --date; default today.
  archive              End-of-month rotation: zip each finished month's dated reports + rotated log
                       backups into logs/archive/<YYYY-MM>/<scope>.zip and remove the originals (the
                       scheduled cherrypick-log-archive task runs this). --month YYYY-MM; --dry-run.
  eod-insight          AI synthesis over the day's deterministic reports → logs/eod-insight-<day>.md
                       (opt-in; needs Claude Code on PATH + eod_insight.enabled). --date; default today.
  advise               Bounded next-session parameter proposals per module → state/advice/
                       <module>-<session>.json, validated against each module's advice_bounds
                       (opt-in twice: advise.enabled + advise.modules.<m>.enabled; needs Claude
                       Code on PATH). Loops re-validate and fall back to baseline. --date.
  reconcile            Paper↔live isolation guard: query the real broker account (read-only) and flag
                       any open positions/BP a paper-only suite shouldn't have. On-demand; never trades.
  connect              Guided per-module onboarding (--module): set OAuth creds (via the module's own
                       hidden-input tool) and select the live-trading account. Never trades.
  account              List (--module), set (--set <last4|index>), or clear (--clear) a module's
                       designated live-trading account. Masked; never trades.
  calibrate            Per-profile paper calibration readings + advisory promotion recommendations.
  run-earnings-entry   Run EarningsAgent's paper entry now (invoked by its daily task).
  run-earnings-exit    Run EarningsAgent's paper exit now (invoked by its daily task).
  ensure-dolt          Start any module's declared Dolt server if down (invoked by its keep-alive task).
  notify-test          Fire a test notification through all configured channels.
  notify-trades        Push new paper entries/exits to the trade channels (also runs on each watchdog tick).
  notify-follow        Push new tastylive Follow Feed orders to their own channel (own task, network call).
  notify-desk          Card manual-desk orders and watch them to fill (own task, broker + network call).
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
from datetime import date, datetime, timezone
from pathlib import Path

from cherrypick.notify import Notifier
from cherrypick.notify import secrets as notify_secrets
from cherrypick.orchestrator import (
    accounts,
    advise,
    calibrate,
    configedit,
    connect,
    desk_notifier,
    doctor,
    eod_digest,
    eod_insight,
    follow_notifier,
    init,
    logrotate,
    migrate,
    reconcile,
    report,
    servicecfg,
    settings_serve,
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
    return {"ok": True, "detail": f"cloned to {root}"}


def cmd_install(cfg, force: bool = False) -> None:
    """Install = ONE anchor task + the supervisor daemon. The supervisor derives every job
    (watchdog, notifiers, module loops, dailies) from config each pass, so nothing else needs
    registering — and every legacy per-job task is unconditionally deleted (the eod-digest deletion
    pattern) so the two mechanisms can never double-fire."""
    from cherrypick.orchestrator import supersnap, supervisor

    results = {}
    pyw = cfgmod.pythonw_exe()
    modules = cfgmod.enabled_modules(cfg)

    # Refuse to cut over while flies is live-armed for today: deleting the legacy live task
    # mid-armed-day must never silently disarm real orders. --force overrides deliberately.
    today = timeutil.now_et(cfg.get("timezone", "America/New_York")).date().isoformat()
    for name, mcfg in modules.items():
        if not mcfg.get("live"):
            continue
        rec = supervisor.read_arm_records(cfg).get(name)
        if rec and str(rec.get("date")) == today and not force:
            _emit(
                {
                    "ok": False,
                    "error": f"{name} is LIVE-ARMED for today ({supervisor.arm_record_path(name)}). "
                    "Install would delete its legacy task mid-day. Disarm first "
                    "(python -m cherrypick.flies.live_loop --uninstall-task) or re-run with --force.",
                }
            )
            sys.exit(1)

    for name, mcfg in modules.items():
        # Materialize the module checkout; the supervisor spawns its ticks from this path.
        chk = _ensure_module_checkout(name, mcfg)
        results[f"{name}.checkout"] = chk
        if not chk.get("ok"):
            continue
        root = cfgmod.module_root(mcfg, name)
        # per-module streamer (the disabled rollback path) still starts like any daemon
        streamer = mcfg.get("streamer", {})
        if streamer.get("enabled"):
            results[f"{name}.streamer"] = _ensure_daemon(root, streamer, f"{name}.streamer", producer=True)

    # The one remaining OS task: a 2-minute keep-alive probe that (re)starts the supervisor.
    # The OS guarantees the probe; the probe guarantees the daemon; the daemon fires everything.
    anchor_tr = tasks.build_tr(pyw, str(_LAUNCHER), "ensure-supervisor")
    results["anchor_task"] = tasks.create_minute_task(supersnap.ANCHOR_TASK, anchor_tr, 2, run_now=False)

    # Start the supervisor now rather than waiting for the anchor's first fire.
    if supersnap.supervisor_alive():
        results["supervisor"] = {"ok": True, "detail": "already running"}
    else:
        started = _spawn_supervisor_detached()
        results["supervisor"] = {"ok": started, "detail": "started" if started else "start failed"}

    # Unconditionally delete every legacy per-job task (idempotent — deleting an absent task is a
    # successful no-op), so a partially-cutover box can't have schtasks and the supervisor both
    # firing the same command.
    for legacy in tasks.legacy_task_names(cfg):
        results[f"legacy.{legacy}"] = tasks.delete(legacy)

    # Start the standalone market-data producer (top-level `streamer`) if enabled — the same
    # start-detached-if-down contract as a service. The watchdog keeps it alive in-session thereafter;
    # its single-instance guard prevents a duplicate start (e.g. if it's already running).
    streamer_spec = cfg.get("streamer") or {}
    if streamer_spec.get("enabled"):
        sroot = cfgmod.module_root(streamer_spec, "streamer")
        results["streamer"] = (
            _ensure_daemon(sroot, streamer_spec, "streamer", producer=True)
            if sroot.exists()
            else {"ok": False, "detail": f"checkout not found at {sroot}"}
        )

    # generic background services (e.g. the gex spot-trail recorder): start each detached if it's down.
    # The watchdog keeps them alive thereafter; single-instance guards prevent duplicate starts.
    for svc in cfgmod.enabled_services(cfg):
        sroot = cfgmod.module_root(svc, svc["id"])
        results[f"service.{svc['id']}"] = (
            _ensure_daemon(sroot, svc)
            if sroot.exists()
            else {"ok": False, "detail": f"checkout not found at {sroot}"}
        )

    _emit({"ok": all(v.get("ok", True) for v in results.values()), "installed": results})


def _ensure_daemon(root: Path, spec: dict, stamp_id: str | None = None, *, producer: bool = False) -> dict:
    """Ensure a detached background daemon is up: check `status_argv` (prints {"running": bool}) and
    launch `start_argv` detached if it is down. Shared by the streamer and the generic `services`.

    A launch here is also where the config stamp comes from — `install` is what usually FOLLOWS a
    config edit, so stamping the freshly started process is what lets a later edit be detected as
    stale (see servicecfg). An already-running daemon is stamped too, adopting whatever it has.
    `producer` marks a market-data streamer, whose stamp also carries the subscription union.
    """
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *spec["status_argv"]],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        running = bool(first_json(r.stdout).get("running")) if r.returncode == 0 else False
    except Exception:
        running = False
    if running:
        _stamp_service_config(root, spec, stamp_id, producer=producer)
        return {"ok": True, "detail": "already running"}
    started = watchdog._start_streamer(root, spec["start_argv"])
    if started:
        _stamp_service_config(root, spec, stamp_id, producer=producer)
    return {"ok": started, "detail": "started" if started else "start failed"}


def _stamp_service_config(
    root: Path, spec: dict, stamp_id: str | None = None, *, producer: bool = False
) -> None:
    """Record what config a daemon was launched with. `services[]` entries name themselves with `id`;
    the streamer blocks carry none, so their caller passes the same label the watchdog stamps under
    ("streamer", "<module>.streamer") — the two must agree or every tick would see a missing stamp.

    A `producer` also stamps the stream-request union it just bound, for the same reason: install is
    what usually follows the config edit, and a producer started here subscribed exactly today's
    union. Without it the first watchdog tick would adopt that union instead of comparing against it,
    silently absorbing any request change made between the install and that tick.
    """
    sid = stamp_id or spec.get("id")
    if not sid:
        return
    try:
        digest, source = servicecfg.effective_config(spec, root)
        subs = servicecfg.subscription_snapshot() if producer else None
        servicecfg.write_stamp(sid, digest, source, subs)
    except Exception:  # stamping is a convenience, never a reason to fail an install
        pass


def _format_uninstall_report(results: dict[str, dict]) -> tuple[str, int]:
    """Render `cmd_uninstall`'s per-task results the way `doctor.format_report` renders checks --
    `[ OK ]`/`[FAIL]` lines a walk-away user can scan, not a JSON blob they have to parse to learn
    whether every task actually went away. Also names what uninstall deliberately leaves running,
    since that's exactly the ambiguity a user reaching for "stopped and confirmed stopped" needs
    resolved without having to already know the /uninstall doc by heart."""
    lines = ["cherrypick uninstall", "=" * 60]
    worst = 0
    for name, r in results.items():
        ok = r.get("ok", True)
        worst = max(worst, 0 if ok else 1)
        lines.append(f"{'[ OK ]' if ok else '[FAIL]'} {name:<24} {r.get('detail', '')}")
    lines += [
        "-" * 60,
        "Left running by design (uninstall does not touch these):",
        "  - streamer (packages/streamer) -- the suite's shared market-data producer",
        "  - the console (packages/console) -- the suite's read surface, kept alive by the supervisor",
        "  - Dolt sql-server on :3306, if something outside cherrypick started it",
        "  Stop these yourself for a full stop -- see docs/operations.md.",
        "=" * 60,
        f"Result: {'ALL REMOVED' if worst == 0 else 'FAILURES -- action needed'}",
    ]
    return "\n".join(lines), worst


def cmd_uninstall(cfg) -> None:
    """Unschedule + stop, in the one order that can't resurrect anything: anchor task first (so
    nothing restarts the supervisor), then the supervisor itself (stop file, then terminate), then
    every legacy task name (idempotent; covers a pre-cutover box too), then the managed services."""
    import time as _time

    from cherrypick.orchestrator import supersnap, supervisor
    from cherrypick.orchestrator.util import pid_alive, read_json

    results = {}
    # 1. The anchor goes first — deleting it after stopping the supervisor would leave a window
    # where the next probe fire restarts what we're stopping (the old dolt-keep-alive hazard).
    results["anchor_task"] = tasks.delete(supersnap.ANCHOR_TASK)

    # 2. Stop the supervisor: polite stop file, ≤10s wait, then terminate.
    pid = (read_json(supervisor.heartbeat_path()) or {}).get("pid")
    if pid and pid_alive(pid):
        supervisor.request_stop()
        deadline = _time.time() + 10
        while _time.time() < deadline and pid_alive(pid):
            _time.sleep(0.5)
        if pid_alive(pid):
            supervisor._terminate_pid(pid)
            results["supervisor"] = {"ok": not pid_alive(pid), "detail": f"terminated pid {pid}"}
        else:
            results["supervisor"] = {"ok": True, "detail": f"stopped cleanly (pid {pid})"}
    else:
        results["supervisor"] = {"ok": True, "detail": "not running"}

    # 3. A surviving live arm record on a stopped box is a silent live loop — remove it and say so.
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if not mcfg.get("live"):
            continue
        rec_path = supervisor.arm_record_path(name)
        if rec_path.exists():
            try:
                rec_path.unlink()
                results[f"{name}.live_arm"] = {
                    "ok": True,
                    "detail": "LIVE ARM RECORD REMOVED — check the broker UI for resting orders",
                }
            except OSError as exc:
                results[f"{name}.live_arm"] = {"ok": False, "detail": str(exc)}

    # 4. Every legacy per-job task by its resolved name (idempotent — also cleans a pre-cutover box).
    for legacy in tasks.legacy_task_names(cfg):
        results[f"legacy.{legacy}"] = tasks.delete(legacy)
    # Stop generic background services (e.g. the gex recorder) — unlike the streamer, these are the
    # orchestrator's own daemons, so a full uninstall stops them.
    for svc in cfgmod.enabled_services(cfg):
        # Drop the launch stamp either way: a stopped service's stamp describes a process that no
        # longer exists, and leaving it behind would make the next install's adopt look like a
        # config change and recycle a freshly started daemon for nothing.
        servicecfg.clear_stamp(svc["id"])
        if svc.get("stop_argv"):
            sroot = cfgmod.module_root(svc, svc["id"])
            try:
                r = subprocess.run(
                    [cfgmod.python_exe(), *svc["stop_argv"]],
                    cwd=str(sroot),
                    capture_output=True,
                    text=True,
                    timeout=15,
                    creationflags=CREATE_NO_WINDOW,
                )
                results[f"service.{svc['id']}"] = {
                    "ok": r.returncode == 0,
                    "detail": (r.stdout or r.stderr).strip()[:200],
                }
            except Exception as exc:
                results[f"service.{svc['id']}"] = {"ok": False, "detail": str(exc)}
    report, worst = _format_uninstall_report(results)
    print(report)
    sys.exit(0 if worst == 0 else 1)


# --------------------------------------------------------------------------- status
def cmd_status(cfg) -> None:
    from cherrypick.orchestrator import supersnap

    # Supervisor-driven boxes report the job registry (+ the anchor task's OS truth) — the
    # `registry_snapshot` schtasks sweep survives only as the pre-cutover fallback.
    if supersnap.supervisor_alive():
        out = {"supervisor": supersnap.supervisor_snapshot(cfg), "heartbeats": {}}
    else:
        out = {"tasks": tasks.registry_snapshot(cfg), "heartbeats": {}}
    for hb in (
        "watchdog.last.json",
        "earnings_entry.last.json",
        "earnings_exit.last.json",
        "earnings_symbol_watch.last.json",
    ):
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
    holidays = timeutil.load_holidays()
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
            [cfgmod.python_exe(), *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=1800,
            creationflags=CREATE_NO_WINDOW,
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
        "stranded": (result or {}).get("stranded"),
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
    elif rec["stranded"]:
        # The run itself succeeded but left positions it could not close for a second
        # (or later) consecutive sweep. Silent stranding is how a position vanishes from
        # every closed-trade metric -- say so, once per daily run, while it persists.
        names = ", ".join(
            f"{s.get('symbol', '?')} ({s.get('reason', 'unknown')} x{s.get('close_attempts', '?')})"
            for s in rec["stranded"][:5]
        )
        Notifier(cfg.get("notify")).notify(
            "WARNING",
            "earnings.stranded",
            f"Earnings paper: {len(rec['stranded'])} position(s) stranded at close",
            names,
        )

    # Push any fills this run produced right away instead of waiting for the next trade-notify tick.
    # Best-effort: a notify hiccup must never fail the scheduled earnings run itself.
    if ok:
        try:
            trade_notifier.run(cfg)
        except Exception:
            pass
    _emit(rec)


def _run_earnings_symbol_watch(cfg) -> None:
    """Invoked by the daily scheduled task (see cfgmod.symbol_watch_settings). Runs packages/
    earnings' own forward-preview scan (`python -m cherrypick.earnings.symbol_watch refresh`) --
    the source of scout's read-only Earnings page "Upcoming" section. Purely informational: never
    touches a paper/live ledger and never places an order, so a failure here is a WARNING, not a
    CRITICAL -- scout's Upcoming section simply keeps showing its last-known-good watch data (or
    none) until the next successful pass, same degrade-gracefully posture that page already has
    for a missing/absent snapshot file."""
    mcfg = cfg.get("modules", {}).get("earnings")
    hb_path = cfgmod.state_file("earnings_symbol_watch.last.json")
    log_path = _module_log("earnings_symbol_watch")
    sw = cfgmod.symbol_watch_settings(cfg)

    if not mcfg or not mcfg.get("enabled"):
        _emit({"ok": True, "skipped": "earnings module disabled"})
        return

    root = cfgmod.module_root(mcfg, "earnings")
    argv = ["-m", "cherrypick.earnings.symbol_watch", "refresh", "--days", str(sw["days"])]

    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=1800,
            creationflags=CREATE_NO_WINDOW,
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
        "ok": ok,
        "error": error,
        "total": (result or {}).get("total"),
        "done": (result or {}).get("done"),
    }
    hb_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    _append_log(log_path, {**rec, "result": result})

    if not ok:
        Notifier(cfg.get("notify")).notify(
            "WARNING",
            "earnings.symbol_watch",
            "Earnings symbol-watch scan failed",
            f"{error or 'see logs/earnings_symbol_watch.log'}",
        )
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


def cmd_preopen_check(cfg) -> None:
    _emit(watchdog.run_preopen(cfg))


def cmd_streamer_health(cfg) -> None:
    """One streamer-liveness pass — the supervisor's 60s in-session `streamer-health` job."""
    _emit(watchdog.run_streamer_health(cfg))


# --------------------------------------------------------------------------- supervisor
def cmd_supervise(cfg, stop: bool = False) -> None:
    """Run the supervisor daemon loop in THIS process (the anchor task launches it detached via
    ensure-supervisor; running it foreground is the manual/diagnostic path). --stop asks a running
    daemon to exit via its stop file."""
    from cherrypick.orchestrator import supervisor

    if stop:
        _emit(supervisor.request_stop())
        return
    _emit(supervisor.run(cfg))


def _spawn_supervisor_detached() -> bool:
    """Launch `run.py supervise` as a detached, windowless daemon — the same flags every other
    daemon start here uses (DETACHED | NO_WINDOW | NEW_GROUP), so it survives this process and
    never flashes a console."""
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
    try:
        subprocess.Popen(
            [cfgmod.pythonw_exe(), str(_LAUNCHER), "supervise"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def cmd_ensure_supervisor(cfg) -> None:
    """The anchor task's probe: fresh heartbeat + live PID → no-op; otherwise start the daemon
    detached. Stdlib + local files only — this is the alerting floor of last resort, so after 3
    consecutive probes that found the supervisor down despite restart attempts it raises ONE
    CRITICAL through the (stdlib, OS-shell) Notifier and holds it until a probe succeeds.
    """
    from cherrypick.orchestrator import supersnap, supervisor

    state_path = cfgmod.state_file("ensure_supervisor.json")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    if supersnap.supervisor_alive():
        if state.get("failures"):
            state = {"failures": 0, "notified": False}
            state_path.write_text(json.dumps(state), encoding="utf-8")
        _emit({"ok": True, "detail": "supervisor running"})
        return

    started = _spawn_supervisor_detached()
    failures = int(state.get("failures") or 0) + 1
    notified = bool(state.get("notified"))
    if failures >= 3 and not notified:
        try:
            Notifier(cfg.get("notify")).notify(
                "CRITICAL",
                "supervisor.down",
                "Supervisor is down and not staying up",
                f"{failures} consecutive ensure-supervisor probes found no live supervisor "
                f"(heartbeat {supervisor.heartbeat_path().name} stale/absent) despite restart "
                "attempts. Scheduled jobs — including the watchdog — are NOT running. "
                "Check logs/supervisor.log.",
            )
            notified = True
        except Exception:
            pass
    state_path.write_text(json.dumps({"failures": failures, "notified": notified}), encoding="utf-8")
    _emit(
        {
            "ok": started,
            "detail": f"supervisor not running — {'started' if started else 'START FAILED'} "
            f"(probe failure #{failures})",
        }
    )


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
    """List / set / clear a designated live-trading account (masked). With --module, the
    module's own designation (its override); WITHOUT --module, the SUITE-WIDE shared default
    every module inherits through the store fallback chain."""
    module = args.module
    if not module:
        if args.clear:
            _emit(accounts.clear_shared_account())
            return
        if args.set:
            # Setting the destination for LIVE orders — human-confirmed unless --yes.
            if not args.yes:
                print(
                    "This designates the SUITE-WIDE default account every module will use for LIVE"
                    " orders (a per-module designation still overrides). cherrypick never places"
                    " trades; it only records the destination."
                )
                if (
                    input(
                        f"Type 'yes' to set the suite's live-trading account to selection {args.set!r}: "
                    ).strip()
                    != "yes"
                ):
                    _emit({"ok": False, "error": "aborted"})
                    sys.exit(1)
            _emit(accounts.set_shared_account(cfg, args.set))
            return
        _emit(accounts.list_shared(cfg))
        return
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
    """With --module: the per-module onboarding (override layer). Without: the SUITE wizard —
    shared login once, optional migration of per-module copies, one suite-wide designation,
    opt-in webhooks, status panel."""
    if not args.module:
        _emit(connect.run_suite(cfg))
        return
    _emit(connect.run(cfg, args.module))


def cmd_reconcile(cfg, scheduled: bool = False) -> None:
    result = reconcile.run(cfg)
    report_text, _worst = reconcile.format_report(result)
    print(report_text)
    verdict = result.get("verdict")
    if scheduled and verdict != reconcile.FLAT:
        # The scheduled run (phase 5: daily during live operation) is only useful if someone
        # hears about a bad verdict -- a FLAT day stays quiet, anything else pushes.
        from cherrypick.notify import Notifier

        level = "CRITICAL" if verdict == reconcile.DRIFT else "WARNING"
        title = (
            "Reconcile: DRIFT - undesignated account holds positions"
            if verdict == reconcile.DRIFT
            else "Reconcile: could not verify accounts"
        )
        try:
            Notifier(cfg.get("notify")).notify(level, "reconcile.scheduled", title, report_text[:1500])
        except Exception:
            pass  # the report is already printed/logged; notification is best-effort
    # exit by verdict: FLAT -> 0, DRIFT (real account not flat) -> 1, UNKNOWN (couldn't check) -> 2
    sys.exit({reconcile.FLAT: 0, reconcile.DRIFT: 1, reconcile.UNKNOWN: 2}.get(verdict, 2))


def cmd_notify_trades(cfg) -> None:
    _emit(trade_notifier.run(cfg))


def cmd_notify_follow(cfg) -> None:
    _emit(follow_notifier.run(cfg))


def cmd_notify_desk(cfg) -> None:
    _emit(desk_notifier.run(cfg))


def _resolve_session(args) -> str | None:
    """The session an EOD-scoped command targets: an explicit --date wins, else --eod means today
    (ET), else None (the all-time cumulative view)."""
    if getattr(args, "date", None):
        return args.date
    if getattr(args, "eod", False):
        return timeutil.now_et().strftime("%Y-%m-%d")
    return None


def cmd_report(cfg, args) -> None:
    if args.live:
        # The live-tagged view (phase 5): the same schema readers over each module's live_db.
        # A separate function by design -- calibrate reads report.run and must only see paper.
        _emit(report.live_run(cfg, session=_resolve_session(args)))
        return
    _emit(report.run(cfg, session=_resolve_session(args)))


def cmd_eod_digest(cfg, args) -> None:
    # --date selects the day; otherwise today (ET). (--eod is redundant here but accepted.)
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    _emit(eod_digest.run(cfg, day=day))


def _non_trading_day_skip(day: str, force: bool) -> dict | None:
    """Skip envelope if `day` is a weekend/holiday and not forced, else None.

    The suite EOD tasks (`cherrypick-eod-digest`, `cherrypick-eod-insight`) are plain DAILY schtasks
    tasks, so they fire every calendar day. Without this guard a Saturday tick writes a flat
    `eod-digest-<weekend>.md`, pushes a "0 trades" notification, and — for insight — burns a paid
    Claude call synthesizing a session that never happened. The trading modules already guard
    `is_trading_day` for exactly this reason (see the flies paper loop, whose own docs warn the suite
    digest "would ingest weekends and holidays as real sessions"); these suite surfaces must too.

    `--force` (or an explicit `--date` on a day that *is* a trading day) still runs, so a manual
    weekend backfill of a real prior session works. An unparseable day is not blocked — the command
    itself reports that."""
    # Imported lazily, not at module top: `cherrypick.core` is only on sys.path once the
    # `cherrypick.orchestrator` package import (below) has run its bootstrap, which is after this
    # module's own top-level imports. By call time that has happened. (Same ordering trap the flies
    # test conftest documents.)
    from cherrypick.core import calendar as _cal

    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None
    if force or _cal.is_trading_day(d):
        return None
    return {"ok": True, "skipped": "not_a_trading_day", "session": day}


def cmd_notify_eod(cfg, args) -> None:
    """Write the suite EOD digest, then push a one-line summary through the notify channels. This is
    what the scheduled `cherrypick-eod-digest` task runs. The digest write and the push are both
    best-effort: a notify hiccup never fails the file write."""
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    skip = _non_trading_day_skip(day, args.force)
    if skip is not None:
        _emit(skip)
        return
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


def cmd_archive(cfg, args) -> None:
    """End-of-month log/report rotation: zip each finished month's dated reports + rotated log backups
    into logs/archive/ and remove the originals. What the scheduled `cherrypick-log-archive` task runs.
    Read/maintenance side, files only — never touches the current month or an active .log."""
    _emit(logrotate.run(cfg, month=args.month, dry_run=args.dry_run))


def cmd_eod_insight(cfg, args) -> None:
    """AI synthesis over the day's deterministic reports → logs/eod-insight-<day>.md. What the scheduled
    `cherrypick-eod-insight` task runs. Opt-in + feature-detected (Claude Code on PATH); read-only, no
    dangerous tools, off the reliability path. Best-effort: prints a `skipped`/`error` envelope rather
    than failing when Claude is absent, disabled, or the reports aren't written yet."""
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    skip = _non_trading_day_skip(day, args.force)
    if skip is not None:
        _emit(skip)
        return
    _emit(eod_insight.run(cfg, day=day))


def cmd_advise(cfg, args) -> None:
    """Bounded parameter advice for the NEXT session, per advise-enabled module. Opt-in twice +
    feature-detected (Claude Code on PATH); deterministic inputs only, all tools denied; the
    orchestrator validates against advice_bounds and writes the artifact. Best-effort envelope,
    off the reliability path -- loops re-validate and treat absent/invalid advice as baseline."""
    day = args.date or (timeutil.now_et().strftime("%Y-%m-%d"))
    skip = _non_trading_day_skip(day, args.force)
    if skip is not None:
        _emit(skip)
        return
    _emit(advise.run(cfg, day=day))


def cmd_settings(cfg, args) -> None:
    """The settings surface: a loopback web editor for the suite's configs + keyring secrets (the one
    mutating HTTP server in the suite — see settings_serve). With --organize it instead reorders live
    config(s) into their example's sections from the CLI and exits (dry-run unless --apply)."""
    if args.organize:
        ids = (
            [t["id"] for t in configedit.targets(cfg) if t["exists"] and t["id"] != "meic-risk"]
            if args.organize == "all"
            else [args.organize]
        )
        results = {tid: configedit.organize(cfg, tid, apply=args.apply) for tid in ids}
        _emit(
            {
                "ok": all(r.get("ok") for r in results.values()),
                "dry_run": not args.apply,
                "targets": {tid: {k: v for k, v in r.items() if k != "text"} for tid, r in results.items()},
            }
        )
        return
    _emit(settings_serve.serve(cfg, host=args.host, port=args.port, open_browser=not args.no_browser))


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
            "preopen-check",
            "streamer-health",
            "supervise",
            "ensure-supervisor",
            "report",
            "eod-digest",
            "notify-eod",
            "archive",
            "eod-insight",
            "advise",
            "reconcile",
            "connect",
            "account",
            "migrate-home",
            "calibrate",
            "run-earnings-entry",
            "run-earnings-exit",
            "run-earnings-symbol-watch",
            "ensure-dolt",
            "notify-test",
            "notify-trades",
            "notify-follow",
            "notify-desk",
            "secrets-set",
            "secrets-status",
            "secrets-delete",
            "settings",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="For init: overwrite an existing config.json. For notify-eod/eod-insight: run even on a "
        "non-trading day (weekend/holiday), which they otherwise skip.",
    )
    parser.add_argument(
        "--date", default=None, help="For report/eod-digest: a session day 'YYYY-MM-DD' (default today)"
    )
    parser.add_argument(
        "--eod", action="store_true", help="For report: restrict to today's (ET) session instead of all-time"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="For report: the live-tagged ledgers (modules' live_db) instead of paper. Never feeds "
        "calibrate/promotion -- those read paper only",
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
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="For reconcile: notify on a non-FLAT verdict (what the scheduled task passes)",
    )
    parser.add_argument("--yes", action="store_true", help="For account --set: skip the confirmation prompt")
    parser.add_argument("--host", default=None, help="For settings: bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="For settings: bind port (default 8804)")
    parser.add_argument(
        "--no-browser", action="store_true", help="For settings: do not open a browser"
    )
    parser.add_argument(
        "--organize",
        nargs="?",
        const="all",
        default=None,
        metavar="TARGET",
        help="For settings: organize live config(s) into their example's sections and exit (no server). "
        "Names one target (orchestrator/meic/earnings/flies/gex/streamer) or all when bare. "
        "Dry-run unless --apply.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="For migrate-home: perform the move (default is a dry run)"
    )
    parser.add_argument(
        "--month",
        default=None,
        help="For archive: restrict to one month 'YYYY-MM' (default: all finished months)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For archive: report what would be archived without writing or deleting",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="For supervise: ask the running supervisor daemon to exit (via its stop file)",
    )
    args = parser.parse_args()

    # `init` scaffolds config.json, so it must run before the config pre-load (a fresh user has none).
    if args.command == "init":
        cmd_init(args.force)
        return

    cfg = cfgmod.load_config()
    dispatch = {
        "install": lambda: cmd_install(cfg, force=args.force),
        "uninstall": lambda: cmd_uninstall(cfg),
        "status": lambda: cmd_status(cfg),
        "doctor": lambda: cmd_doctor(cfg, fast=args.fast),
        "watchdog": lambda: cmd_watchdog(cfg),
        "preopen-check": lambda: cmd_preopen_check(cfg),
        "streamer-health": lambda: cmd_streamer_health(cfg),
        "supervise": lambda: cmd_supervise(cfg, stop=args.stop),
        "ensure-supervisor": lambda: cmd_ensure_supervisor(cfg),
        "report": lambda: cmd_report(cfg, args),
        "eod-digest": lambda: cmd_eod_digest(cfg, args),
        "notify-eod": lambda: cmd_notify_eod(cfg, args),
        "archive": lambda: cmd_archive(cfg, args),
        "eod-insight": lambda: cmd_eod_insight(cfg, args),
        "advise": lambda: cmd_advise(cfg, args),
        "reconcile": lambda: cmd_reconcile(cfg, scheduled=args.scheduled),
        "connect": lambda: cmd_connect(cfg, args),
        "account": lambda: cmd_account(cfg, args),
        "migrate-home": lambda: cmd_migrate_home(cfg, args.apply),
        "calibrate": lambda: cmd_calibrate(cfg),
        "notify-trades": lambda: cmd_notify_trades(cfg),
        "notify-follow": lambda: cmd_notify_follow(cfg),
        "notify-desk": lambda: cmd_notify_desk(cfg),
        "run-earnings-entry": lambda: _run_earnings(cfg, "entry"),
        "run-earnings-exit": lambda: _run_earnings(cfg, "exit"),
        "run-earnings-symbol-watch": lambda: _run_earnings_symbol_watch(cfg),
        "ensure-dolt": lambda: _ensure_dolt(cfg),
        "notify-test": lambda: cmd_notify_test(cfg),
        "secrets-set": lambda: cmd_secrets_set(args.channel, args.url),
        "secrets-status": lambda: cmd_secrets_status(),
        "secrets-delete": lambda: cmd_secrets_delete(args.channel),
        "settings": lambda: cmd_settings(cfg, args),
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
