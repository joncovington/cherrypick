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
  review               Build the cross-module end-of-day fact set (packages/review) and its render.
                       --final marks the session final and re-runs reconciliation; without it the
                       pass is provisional. What the two daily review-provisional/review-final
                       supervisor jobs run.
  archive              End-of-month rotation: zip each finished month's dated reports + rotated log
                       backups into logs/archive/<YYYY-MM>/<scope>.zip and remove the originals (the
                       scheduled cherrypick-log-archive task runs this). --month YYYY-MM; --dry-run.
  backup               Nightly backup of the suite's own data (configs, state, every ledger and artifact;
                       not the Dolt clones, stream caches or logs) into one verified zip under
                       backup.dest (default ~/.cherrypick/backups). Only the latest is kept, rewritten
                       nightly; a night with problems never replaces it. The suite-backup job (01:30
                       ET) runs this. --dry-run lists what it would hold; --list; --verify re-checks
                       it; --restore-to DIR extracts it into DIR (never into the live home).
  ps                   Every job and daemon by name: running PID, held or not, last exit and whether
                       it was asked for. Read-only.
  restart NAME         Restart a job or daemon by name. The supervisor stops the old process,
                       confirms it gone by PID and creation time, then starts one; the command waits
                       for that answer. Never kills anything itself.
  stop NAME | --all    Hold a job or daemon (state/holds.json) so nothing starts it, and stop it.
                       --all is the full stop after uninstall, refused while anything could restart.
  start NAME           Release a hold; the supervisor (or ensure) starts it again.
  restart-console      Alias for `restart console`. Never scheduled, never called from the watchdog.
  reconcile            Paper↔live isolation guard: query the real broker account (read-only) and flag
                       any open positions/BP a paper-only suite shouldn't have. On-demand; never trades.
  positions            Live P/L by underlying for the REAL broker account: positions priced from the
                       stream cache first, the feed only for what the cache lacks. --detail for legs,
                       --account <last4> for one account, --json. Read-only; never trades.
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
  notify-desk          Card manual-desk orders and watch them to fill (own task, broker + network call).
  notify-status        Post the hourly suite-status digest card (own job, webhook push). --close posts
                       the day's CLOSE card (the daily 16:35 job); --force posts on a non-trading day too.
  notify-send          Post a hand-written message (--channel, --file; --kind correction|note,
                       --refers-to <message id>, --image, --date <session>, --dry-run) through the
                       same send and outbound record as every scheduled post.
  sent                 What the suite sent to webhooks: a session's (--date) or the last --days.
                       --stale names inputs changed since a post went out; --verify asks Discord
                       whether each message still exists; --kind narrows; --json for the records.
  secrets-set          Store a webhook URL in the keyring (--channel; --url or prompt).
  secrets-status       Show which push-channel secrets are configured (secret-free).
  secrets-delete       Remove a stored secret (--channel).
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
    backup,
    calibrate,
    configedit,
    connect,
    desk_notifier,
    doctor,
    init,
    logrotate,
    migrate,
    reconcile,
    report,
    servicecfg,
    settings_serve,
    status_digest,
    tasks,
    timeutil,
    trade_notifier,
    watchdog,
)
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import (
    positions as positions_mod,
)
from cherrypick.orchestrator import proc as _proc
from cherrypick.orchestrator.util import CREATE_NO_WINDOW, NEW_SESSION, atomic_write_json, first_json

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

    # "Turn the suite on" means everything: a process someone stopped (`run.py stop`) is started
    # again here, and the report says which, so a hold is never silently carried into a fresh install.
    from cherrypick.orchestrator import holds as _holds

    released = _holds.release_all()
    results["holds"] = {"ok": True, "detail": f"released: {', '.join(released)}" if released else "none held"}

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
    service = _service_mode(cfg)
    if service and tasks.exists(supersnap.ANCHOR_TASK):
        # Service mode (optional, `run.py service`): the anchor was set to run with nobody logged on;
        # re-creating it here would quietly put it back to logged-on only.
        results["anchor_task"] = {"ok": True, "detail": "kept as configured (service mode)"}
    else:
        results["anchor_task"] = tasks.create_minute_task(supersnap.ANCHOR_TASK, anchor_tr, 2, run_now=False)

    if os.name == "nt":
        # An unattended Windows PC restarts itself for updates outside its active hours: say so now if
        # that window falls on the suite's day, while the person installing is there (2026-10-09).
        from cherrypick.orchestrator import winupdate

        try:
            results["windows_update"] = winupdate.check_config(cfg)
        except Exception as exc:  # noqa: BLE001 -- advice only; never fails an install
            results["windows_update"] = {"status": "unknown", "detail": f"{type(exc).__name__}: {exc}"}
        if results["windows_update"].get("status") == "warn":
            print(f"WARNING: {results['windows_update']['detail']}", file=sys.stderr)

    # Start the supervisor now rather than waiting for the anchor's first fire.
    if supersnap.supervisor_alive():
        results["supervisor"] = {"ok": True, "detail": "already running"}
    else:
        started = _start_supervisor(cfg)
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
        "  - the console (packages/console) on :5070 -- the supervisor owned it, but node is its",
        "    GRANDchild (run.py is a launcher), so it outlives the daemon. Kill the TREE to stop it.",
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
            start_new_session=NEW_SESSION,  # POSIX: out of this process's group (DETACHED's twin)
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


def _run_review(cfg, *, final: bool) -> None:
    """Invoked by the two daily review passes (see cfgmod.review_settings). Runs packages/review,
    which reads every module's ledger read-only and writes only into its own home.

    Two passes because the modules do not all finish together: the provisional pass captures the
    0DTE modules complete with earnings still carrying overnight, and the final pass the next
    morning closes that session out. `--final` also re-runs the reconciliation, which re-counts each
    module with independent SQL -- a scope difference between the fact set and a ledger is worth
    knowing about the morning it appears, not whenever someone next looks.

    WHICH session the final pass closes out is review's own contract, not this scheduler's, so no
    `--session` is passed: `cherrypick.review build --final` resolves the prior trading day itself
    (facts.session_to_finalise). This docstring described that behaviour for a while before the
    code did -- review defaulted to today, so the 10:15 pass finalised the session it was running
    45 minutes into. Keep the knowledge on review's side; a second copy here is a second thing to
    drift.

    A failure here is a WARNING, never CRITICAL: nothing downstream of this places, closes or sizes
    anything, so the cost of a bad pass is a missing report. The suite keeps trading either way.
    """
    hb_path = cfgmod.state_file("review.last.json")
    log_path = _module_log("review")
    verb = "final" if final else "provisional"

    argv = ["-m", "cherrypick.review", "build"] + (["--final"] if final else [])
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *argv],
            capture_output=True,
            text=True,
            timeout=900,
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

    reconciled = None
    if ok and final:
        try:
            rr = subprocess.run(
                [cfgmod.python_exe(), "-m", "cherrypick.review", "reconcile"],
                capture_output=True,
                text=True,
                timeout=900,
                creationflags=CREATE_NO_WINDOW,
            )
            reconciled = json.loads(rr.stdout or "{}")
        except Exception as exc:  # noqa: BLE001 -- a failed check must not fail the report
            reconciled = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    rec = {
        "ok": ok,
        "pass": verb,
        "error": error,
        "session": (result or {}).get("session"),
        "status": (result or {}).get("status"),
        "reconciled": reconciled,
    }
    hb_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    _append_log(log_path, {**rec, "result": result})

    notifier = Notifier(cfg.get("notify"))
    if not ok:
        notifier.notify(
            "WARNING", "review", f"Suite review ({verb}) failed", f"{error or 'see logs/review.log'}"
        )
    elif reconciled and reconciled.get("ok") is False:
        # The fact set built, but it does not agree with the ledgers it claims to summarise. That is
        # a data-integrity finding, not a cosmetic one -- every surface reads this artifact.
        notifier.notify(
            "WARNING",
            "review",
            "Suite review reconciliation mismatch",
            f"{len(reconciled.get('failures') or [])} session(s) disagree with their ledgers",
        )
    _emit(rec)


def _run_morning(cfg) -> None:
    """Invoked by the daily morning-factpack job (see cfgmod.morning_settings). Runs
    packages/overview, a pure stream-cache + GEX-history consumer that writes only into its own
    home — so like the review, the cost of a bad pass is a missing report, never a trade.

    WHICH session the pack describes is overview's own contract: `cherrypick.overview build`
    resolves today's ET trading day itself, so no `--session` is passed."""
    hb_path = cfgmod.state_file("morning.last.json")
    log_path = _module_log("overview")

    try:
        r = subprocess.run(
            [cfgmod.python_exe(), "-m", "cherrypick.overview", "build"],
            capture_output=True,
            text=True,
            timeout=900,
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
        "session": (result or {}).get("session"),
        "phase": (result or {}).get("phase"),
    }
    hb_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    _append_log(log_path, {**rec, "result": result})

    if not ok:
        Notifier(cfg.get("notify")).notify(
            "WARNING", "overview", "Morning overview pack failed", f"{error or 'see logs/overview.log'}"
        )
    _emit(rec)


def _console_port(cfg) -> int:
    """The console's listen port: `serve.port` in console.json, else 5070.

    Mirrors `packages/console/shared/src/paths.ts`'s `consolePort()` exactly -- same config path
    (`cherrypick.core.home`, the same resolver this package already uses for `CHERRYPICK_HOME`), same
    default -- so this can never disagree with what the console itself resolved. `cfg` is unused
    today (the port is not itself a `config.json` key) but kept so a future config-level override
    does not change this function's signature. Only reached by `cmd_restart_console`'s port-scan
    fallback; every other command in this file spawns the console without ever needing its port.
    """
    from cherrypick.core import home as corehome

    try:
        raw = json.loads(corehome.config_path("console").read_text(encoding="utf-8"))
        port = (raw.get("serve") or {}).get("port")
        if isinstance(port, int) and 0 < port < 65536:
            return port
    except (OSError, ValueError, AttributeError):
        pass
    return 5070  # DEFAULT_CONSOLE_PORT in packages/console/shared/src/paths.ts


def _find_listening_pid(port: int) -> int | None:
    """Whoever is listening on `port` right now, independent of what the supervisor's own registry
    believes.

    Windows parses `netstat -ano`; elsewhere it is `util.port_owner_pid` (/proc on Linux, lsof on
    macOS -- 2026-10-08 OS audit). Returns None rather than guessing when nothing matches or the probe
    itself fails.
    """
    if os.name != "nt":
        from cherrypick.orchestrator.util import port_owner_pid

        return port_owner_pid(port)
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    needle = f":{port} "
    for line in (out or "").splitlines():
        if "LISTENING" in line and needle in line:
            parts = line.split()
            if parts:
                try:
                    return int(parts[-1])
                except ValueError:
                    continue
    return None


def cmd_restart_console(cfg) -> None:
    """`run.py restart console`, kept under its old name (it is in the /console skill and in
    people's hands). Writes `state/restart_console.last.json`, as it always has.

    It used to find a PID itself -- the registry, else whoever listened on the port -- and kill it,
    with the supervisor learning afterwards. Now the supervisor does it (see `orchestrator.proc`):
    stop the tree, confirm it gone, relaunch. The port is still read, but only to REPORT a stray
    listener that is not the new console (the 2026-08-13 case: a registry PID and a different real
    listener). Reclaiming that port is the supervisor's job (`_reclaim_stuck_port`), which kills an
    untracked holder only after its own launches keep failing to bind -- never on a guess here."""
    rec = _proc.restart_job("console")
    port = _console_port(cfg)
    listener = _find_listening_pid(port)
    tree = {rec.get("new_pid")}
    if listener and listener not in tree and rec.get("ok"):
        # node is the launcher's CHILD, so the listener is normally not new_pid itself; say what is
        # on the port and let `ps` / the duplicate check judge it rather than guessing here.
        rec["port_listener"] = {"port": port, "pid": listener}
    cfgmod.state_file("restart_console.last.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    _emit(rec)
    if not rec.get("ok"):
        sys.exit(1)


# --------------------------------------------------------------------------- process control
def cmd_ps(cfg) -> None:
    _emit(_proc.ps(cfg))


def _named(cfg, name: str | None):
    if not name:
        _emit({"ok": False, "error": "name a process: `run.py ps` lists them"})
        sys.exit(2)
    found = _proc.resolve(cfg, name)
    if isinstance(found, dict):
        _emit(found)
        sys.exit(2)
    return found


def _via_supervisor(cfg) -> bool:
    """Whether a daemon action must be handed to the supervisor (`daemonreq`): under the Windows
    service the daemons run in session 0, where the desktop cannot stop them. Only while that
    supervisor is alive to take the request; with none, the caller acts directly, as before."""
    from cherrypick.orchestrator import supersnap, winservice

    return bool(winservice.settings(cfg).get("enabled")) and supersnap.supervisor_alive()


def _daemon_action(cfg, verb: str, name: str, direct: bool) -> dict:
    if not direct and _via_supervisor(cfg):
        from cherrypick.orchestrator import daemonreq

        rec = daemonreq.await_result(daemonreq.request(verb, name))
        return {**rec, "via": "supervisor"}
    if verb == "restart":
        return _proc.restart_daemon(cfg, name, _ensure_daemon)
    if verb == "stop":
        return _proc.stop_daemon(cfg, name)
    return _proc.start_daemon(cfg, name, _ensure_daemon)


def _finish(rec: dict, result: str | None) -> None:
    """Emit, file the record where a supervisor-run child was told to (`--result`), and exit."""
    if result:
        atomic_write_json(result, rec)
    _emit(rec)
    sys.exit(0 if rec.get("ok") else 1)


def cmd_restart(cfg, name: str | None, *, direct: bool = False, result: str | None = None) -> None:
    kind, name = _named(cfg, name)
    rec = _proc.restart_job(name) if kind == "job" else _daemon_action(cfg, "restart", name, direct)
    _finish(rec, result)


def cmd_stop(
    cfg, name: str | None, everything: bool = False, *, direct: bool = False, result: str | None = None
) -> None:
    if everything:
        rec = _proc.stop_all(cfg, anchor_registered=tasks.exists(supersnap_anchor()))
    else:
        kind, name = _named(cfg, name)
        rec = _proc.stop_job(name) if kind == "job" else _daemon_action(cfg, "stop", name, direct)
    _finish(rec, result)


def cmd_start(cfg, name: str | None, *, direct: bool = False, result: str | None = None) -> None:
    kind, name = _named(cfg, name)
    rec = _proc.start_job(name) if kind == "job" else _daemon_action(cfg, "start", name, direct)
    _finish(rec, result)


def supersnap_anchor() -> str:
    from cherrypick.orchestrator import supersnap

    return supersnap.ANCHOR_TASK


# --------------------------------------------------------------------------- misc
def cmd_capabilities(cfg, args) -> None:
    """Show (default), detect (--detect [--write]) or set (--cap name=bool ...) what this machine has.
    See orchestrator/capabilities.py; the installer runs `--detect --write`."""
    from .orchestrator import capabilities as caps

    if args.cap_set:
        try:
            values = caps.parse_set(args.cap_set)
        except ValueError as exc:
            _emit({"ok": False, "error": str(exc)})
            sys.exit(2)
        _emit(caps.write(values))
        return
    if args.detect:
        found = caps.detect(cfg)
        out: dict = {"ok": True, "detected": found}
        if args.write:
            out["written"] = caps.write({k: v["present"] for k, v in found.items()})
        _emit(out)
        return
    _emit(caps.gated_features(cfg))


def cmd_config_backup(cfg, args) -> None:
    """One config-history pass (default), set it up (--init [--remote URL]), or switch the
    scheduled job (--enable / --disable). See orchestrator/config_backup.py."""
    from .orchestrator import config_backup as cb

    if args.enable and args.disable:
        _emit({"ok": False, "error": "--enable and --disable together"})
        sys.exit(2)
    out: dict = {}
    if args.init:
        out["init"] = cb.init(remote=args.remote)
    if args.enable or args.disable:
        out["switch"] = cb.set_enabled(bool(args.enable))
    if not out:
        out = cb.run(push=cfgmod.config_backup_settings(cfg)["push"])
    _emit(out)
    if args.init or args.enable or args.disable:
        ok = all(v.get("ok", True) for v in out.values())
    else:
        ok = out.get("ok", True)
    sys.exit(0 if ok else 1)


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
def cmd_supervise(cfg, stop: bool = False, restart: bool = False) -> None:
    """Run the supervisor daemon loop in THIS process (the anchor task launches it detached via
    ensure-supervisor; running it foreground is the manual/diagnostic path). --stop asks a running
    daemon to exit via its stop file."""
    from cherrypick.orchestrator import supervisor

    if stop or restart:
        _emit(supervisor.request_stop(restart=restart))
        return
    # Deliberately NOT the pre-loaded cfg: a non-None cfg PINS the daemon to that snapshot (the
    # test affordance in Supervisor.__init__), and passing it here silently disabled the mtime
    # reload — every config edit needed a daemon restart nobody knew to perform. The daemon loads
    # its own config so edits apply on the next pass, as the scheduling docs promise.
    result = supervisor.run()
    _emit(result)
    if result.get("restart"):
        sys.exit(supervisor.RESTART_EXIT)


def _service_mode(cfg) -> dict | None:
    """The service's settings when the optional Windows-service mode is on AND the service is
    installed (`run.py service`), else None -- the default, logged-on-anchor posture."""
    from cherrypick.orchestrator import winservice

    s = winservice.settings(cfg or {})
    if not s["enabled"] or os.name != "nt":
        return None
    return s if winservice.query(s["id"]).get("installed") else None


# How long the anchor lets a stopped service restart itself before starting a supervisor outside
# it: longer than its longest restart delay (2 min). A fallback started inside that gap holds the lock,
# and the service's own supervisor then refuses to start (2026-10-09).
SERVICE_RESTART_GRACE_S = 300


def _start_supervisor(cfg, state: dict | None = None) -> bool:
    """Start the supervisor the way this machine runs it: through the service in service mode (a
    detached spawn there would be a rival the service cannot see), else the detached daemon."""
    service = _service_mode(cfg)
    if service:
        from cherrypick.orchestrator import winservice

        if winservice.start(service["id"]):
            return True
        # `sc start` is refused without administrator rights even when the service is already up --
        # a fallback then would start a rival beside it (2026-10-08). Running or starting is started.
        if winservice.query(service["id"]).get("state") in ("RUNNING", "START_PENDING"):
            return True
        if state is not None:  # the anchor: give the service its own restart first
            import time as _time

            since = float(state.setdefault("service_down_since", _time.time()))
            if _time.time() - since < SERVICE_RESTART_GRACE_S:
                return False
        # The service would not start (a logon failure, a refused `sc start`): a supervisor outside
        # it beats none at all -- the single-instance lock still stops the service from adding a
        # second once it is fixed. Said loudly, because the machine is no longer running as chosen.
        # Found on the first real install (2026-10-08): this path gave up, and nothing restarted the
        # suite after the old supervisor had been stopped for the service to take over.
        started = _spawn_supervisor_detached()
        try:
            Notifier(cfg.get("notify")).notify(
                "CRITICAL",
                "service.start_failed",
                "Supervisor service would not start -- running it the usual way",
                f"`sc start {service['id']}` failed, so the supervisor was started outside the service "
                f"({'started' if started else 'that failed too'}). Windows' System event log (Service "
                "Control Manager, 7000/7038) says why -- most often the service account's password. "
                "`run.py service status` and `run.py doctor` show the state.",
            )
        except Exception:
            pass
        return started
    return _spawn_supervisor_detached()


def cmd_service(cfg, action: str | None) -> None:
    """OPTIONAL Windows-service mode (orchestrator/winservice.py): prepare | status | uninstall."""
    from cherrypick.orchestrator import supersnap, winservice

    if action == "prepare":
        out = winservice.prepare(
            cfg, launcher=str(_LAUNCHER), workdir=str(_LAUNCHER.parents[2]), anchor_task=supersnap.ANCHOR_TASK
        )
    elif action == "uninstall":
        out = winservice.uninstall(cfg, anchor_task=supersnap.ANCHOR_TASK)
    elif action in (None, "status"):
        out = winservice.status(cfg)
    else:
        out = {"ok": False, "error": f"unknown service action {action!r}: prepare | status | uninstall"}
    _emit(out)
    if out.get("ok") is False:
        sys.exit(1)


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
            # POSIX: its own session, so a Ctrl-C in the installer's terminal does not reach it.
            start_new_session=NEW_SESSION,
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

    # Alive but not beating: it holds the lock, so a fresh start would refuse forever. End that one
    # process -- never its children, which the new supervisor adopts (a live tick may be mid-order)
    # -- and say so (2026-10-08 audit).
    import time

    from cherrypick.core import looplock as _looplock

    from cherrypick.orchestrator.util import read_json

    hb = read_json(supervisor.heartbeat_path())
    wedged = supersnap.wedged_supervisor_pid(
        hb, start_time_fn=_looplock.process_start_time, alive_fn=_looplock.pid_alive
    )
    if wedged is not None:
        supervisor._terminate_pid(wedged)
        for _ in range(20):
            if not _looplock.pid_alive(wedged):
                break
            time.sleep(0.5)
        try:
            Notifier(cfg.get("notify")).notify(
                "WARNING",
                "supervisor.wedged",
                "Supervisor was hung -- restarted",
                f"pid {wedged} was alive with a heartbeat {supersnap.heartbeat_age_seconds(hb) or 0:.0f} s "
                "old, holding the lock so no restart could take it. Ended that process alone (its "
                "jobs keep running and are adopted) and started a new supervisor. logs/supervisor.log "
                "and logs/supervisor-fault.log may say why it stopped.",
            )
        except Exception:
            pass

    started = _start_supervisor(cfg, state)
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
    keep = {k: state[k] for k in ("service_down_since",) if k in state}
    state_path.write_text(json.dumps({"failures": failures, "notified": notified, **keep}), encoding="utf-8")
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
            Notifier(cfg.get("notify")).notify(level, "reconcile.scheduled", title, reconcile.summary(result))
        except Exception:
            pass  # the report is already printed/logged; notification is best-effort
    # exit by verdict: FLAT -> 0, DRIFT (real account not flat) -> 1, UNKNOWN (couldn't check) -> 2
    sys.exit({reconcile.FLAT: 0, reconcile.DRIFT: 1, reconcile.UNKNOWN: 2}.get(verdict, 2))


def cmd_positions(cfg, args) -> None:
    """Live P/L by underlying. Read-only and advisory, so it exits 0 whenever a report was produced — a
    losing book is not a failed command. An unreachable broker is the failure (2), and a leg nothing
    could price exits 3 so a caller notices rather than reading a total that quietly omits it."""
    result = positions_mod.run(cfg, account=args.account)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(positions_mod.format_report(result, detail=args.detail))
    if not result.get("ok"):
        sys.exit(2)
    if any(a.get("unpriced_count") for a in result.get("accounts") or []):
        sys.exit(3)


def cmd_notify_trades(cfg, dry_run: bool = False) -> None:
    _emit(trade_notifier.run(cfg, dry_run=dry_run))


def cmd_notify_desk(cfg) -> None:
    _emit(desk_notifier.run(cfg))


def cmd_power_watch(cfg) -> None:
    """Notify every channel while this machine is on battery (orchestrator/powerwatch.py)."""
    from cherrypick.orchestrator import powerwatch

    _emit(powerwatch.run(cfg))


def cmd_live_positions(cfg) -> None:
    """Live accounts' positions vs the live ledgers (orchestrator/livepositions.py). Read-only."""
    from cherrypick.orchestrator import livepositions

    _emit(livepositions.run(cfg))


def cmd_settle_overdue_live(cfg) -> None:
    """Catch-up settlement of past live sessions at the official close (orchestrator/livesettle.py)."""
    from cherrypick.orchestrator import livesettle

    _emit(livesettle.run(cfg))


def cmd_notify_status(cfg, force: bool = False, close: bool = False) -> None:
    _emit(status_digest.run(cfg, force=force, close=close))


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


def cmd_archive(cfg, args) -> None:
    """End-of-month log/report rotation: zip each finished month's dated reports + rotated log backups
    into logs/archive/ and remove the originals. What the scheduled `cherrypick-log-archive` task runs.
    Read/maintenance side, files only — never touches the current month or an active .log."""
    _emit(logrotate.run(cfg, month=args.month, dry_run=args.dry_run))


def cmd_backup(cfg, args) -> None:
    """The nightly backup (orchestrator.backup). A failed night -- a copy that errored or a ledger copy
    that failed quick_check -- notifies, because the supervisor records a job's exit code but tells no
    one, and a backup that fails quietly is found out on the day it is needed."""
    if args.list:
        _emit(backup.listing(cfg))
        return
    if args.verify:
        _emit(backup.verify(cfg))
        return
    if args.restore_to:
        _emit(backup.restore(cfg, args.restore_to))
        return
    try:
        res = backup.run(cfg, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 -- a crash is a failed night, and must be heard
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not res.get("ok") and not args.dry_run:
        detail = res.get("error") or "; ".join(res.get("problems", [])[:5])
        try:
            Notifier(cfg.get("notify")).notify(
                "WARNING", "backup", "Nightly backup FAILED (previous backup kept)", detail
            )
        except Exception:  # noqa: BLE001 -- best effort; the exit code still says so
            pass
    _emit(res)
    if not res.get("ok"):
        sys.exit(1)


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


def cmd_sent(args) -> None:
    """Everything the suite sent to a webhook (notify.notifier's outbound record): a session's with
    --date, else the last --days. --stale names the inputs that changed after a post went out;
    --verify asks Discord whether each message still exists. --json for the raw records."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from cherrypick.notify import notifier

    et = ZoneInfo("America/New_York")
    since = None
    if not args.date:
        since = (datetime.now(UTC) - timedelta(days=args.days)).isoformat()
    entries = notifier.read_outbound(session=args.date, since=since, kind=args.kind)
    corrected_by: dict[str, list[str]] = {}
    for e in entries:
        if e.get("refers_to"):
            corrected_by.setdefault(e["refers_to"], []).append(e["ts"])
    for e in entries:
        if args.stale:
            e["changed_inputs"] = notifier.changed_inputs(e)
        if args.verify:
            e["on_discord"] = notifier.verify_message(e)
        if e.get("message_id") in corrected_by:
            e["followed_by"] = corrected_by[e["message_id"]]
    if args.json:
        _emit(entries)
        return
    if not entries:
        print("nothing sent" + (f" for {args.date}" if args.date else f" in the last {args.days} day(s)"))
        return
    for e in entries:
        when = datetime.fromisoformat(e["ts"]).astimezone(et).strftime("%m-%d %H:%M:%S ET")
        state = "sent" if e.get("ok") else f"FAILED ({e.get('error')})"
        text = (e.get("content") or "").strip().splitlines() or [
            ((e.get("embeds") or [{}])[0].get("title") or "") if e.get("embeds") else ""
        ]
        head = f"{when}  {e.get('kind', '?'):<15} {e.get('source', '?'):<22} {e.get('channel', '?'):<18}"
        print(f"{head} {e.get('session') or '-':<10} {state}")
        bits = [f"msg {e['message_id']}"] if e.get("message_id") else []
        if e.get("attachments"):
            bits.append(f"{len(e['attachments'])} image(s)")
        if e.get("refers_to"):
            bits.append(f"follows up {e['refers_to']}")
        if e.get("followed_by"):
            bits.append("followed up " + ", ".join(e["followed_by"]))
        if args.verify:
            bits.append(f"on Discord: {e['on_discord']}")
        print(f"    {text[0][:100]}" + (f"  [{'; '.join(bits)}]" if bits else ""))
        if args.stale:
            if not e.get("inputs"):
                print("    inputs: none recorded (cannot say whether it is stale)")
            for c in e["changed_inputs"]:
                now = "gone" if c["now"].get("missing") else f"changed {c['now'].get('mtime')}"
                print(f"    STALE: {c['path']} {now}")


def cmd_notify_send(args) -> None:
    """Post a hand-written message (a correction, a note) to a webhook through the same send and
    record as every scheduled post. The text comes from --file, so nothing a shell would expand
    (a `$1` in `$17.55M`) can be lost on the way. --dry-run shows what would go."""
    from cherrypick.notify import notifier

    if not args.channel or not args.file:
        _emit({"ok": False, "error": "notify-send needs --channel and --file"})
        sys.exit(2)
    text = Path(args.file).read_text(encoding="utf-8").strip()
    images = [Path(p) for p in (args.image or [])]
    missing = [str(p) for p in images if not p.exists()]
    if not text or missing or len(text) > 2000:
        why = (
            "empty message"
            if not text
            else f"missing image(s): {missing}"
            if missing
            else "over 2000 characters"
        )
        _emit({"ok": False, "error": why})
        sys.exit(2)
    payload = {"content": text, "allowed_mentions": {"parse": []}}
    if images:
        payload["attachments"] = [{"id": i, "filename": p.name} for i, p in enumerate(images)]
    if args.dry_run:
        _emit(
            {
                "ok": True,
                "dry_run": True,
                "channel": args.channel,
                "payload": payload,
                "images": [str(p) for p in images],
            }
        )
        return
    url = notify_secrets.read_entry(args.channel)
    if not url or url is notify_secrets.KEYRING_UNAVAILABLE:
        _emit(
            {
                "ok": False,
                "error": f"no {args.channel} webhook readable "
                f"(cherrypick secrets-set --channel {args.channel})",
            }
        )
        sys.exit(1)
    if not str(args.channel).startswith("discord"):
        payload = {"text": text}
    sent = notifier.send_webhook(
        str(url),
        payload,
        images,
        channel=args.channel,
        source="notify-send",
        kind=args.kind or "note",
        session=args.date,
        refers_to=args.refers_to,
    )
    _emit({"ok": sent["ok"], "message_id": sent["message_id"], "error": sent["error"]})
    if not sent["ok"]:
        sys.exit(1)


def cmd_secrets_set(channel: str | None, url: str | None) -> None:
    if channel not in notify_secrets.WEBHOOKS:
        _emit({"ok": False, "error": f"--channel must be one of {list(notify_secrets.WEBHOOKS)}"})
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
    if channel not in notify_secrets.WEBHOOKS:
        _emit({"ok": False, "error": f"--channel must be one of {list(notify_secrets.WEBHOOKS)}"})
        sys.exit(2)
    removed = notify_secrets.delete_webhook(channel)
    _emit({"ok": removed, "channel": channel, "status": notify_secrets.status()})


def build_parser() -> argparse.ArgumentParser:
    """The CLI's argument parser, built separately from `main` so it can be inspected.

    Extracted so a test can assert that every argv the supervisor DERIVES is an argv this parser
    accepts. It was not, silently: the two review jobs passed flags the parser had never been taught
    and exited 2 every day while the job table reported them healthy.
    """
    parser = argparse.ArgumentParser(
        prog="cherrypick", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "init",
            "capabilities",
            "config-backup",
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
            "archive",
            "backup",
            "reconcile",
            "positions",
            "connect",
            "account",
            "migrate-home",
            "calibrate",
            "run-earnings-entry",
            "run-earnings-exit",
            "run-earnings-symbol-watch",
            "review",
            "morning",
            "restart-console",
            "power-watch",
            "live-positions",
            "settle-overdue-live",
            "service",
            "ps",
            "restart",
            "stop",
            "start",
            "ensure-dolt",
            "notify-test",
            "notify-trades",
            "notify-desk",
            "notify-status",
            "notify-send",
            "sent",
            "secrets-set",
            "secrets-status",
            "secrets-delete",
            "settings",
        ],
    )
    parser.add_argument(
        "--channel",
        choices=list(notify_secrets.WEBHOOKS),
        help="Webhook for secrets-set/secrets-delete: a push channel, or a dedicated one "
        "(discord_reporting: the reporting channel the QuikOptions series posts to, never suite alerts)",
    )
    parser.add_argument(
        "--url", default=None, help="Webhook URL for secrets-set (omit to be prompted without echo)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="For init: overwrite an existing config.json. For install: proceed while flies is "
        "live-armed today. For notify-status: post on a non-trading day too.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="For report/eod-digest: a session day 'YYYY-MM-DD' (default today). For sent: that "
        "session's posts. For notify-send: the session the message is about",
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
    parser.add_argument(
        "--detail",
        action="store_true",
        help="For positions: expand each underlying into its individual legs",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="For positions: restrict to one account by its last 4 digits",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="For positions: emit the structured result instead of the text report",
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
        "--detect", action="store_true", help="For capabilities: probe this machine for claude and dolt"
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="For config-backup: make the cherrypick home a config-only git repo",
    )
    parser.add_argument(
        "--remote", default=None, help="For config-backup --init: a git remote URL to push to"
    )
    parser.add_argument(
        "--enable", action="store_true", help="For config-backup: switch the scheduled job on"
    )
    parser.add_argument(
        "--disable", action="store_true", help="For config-backup: switch the scheduled job off"
    )
    parser.add_argument(
        "--write", action="store_true", help="For capabilities --detect: record the answers in config.json"
    )
    parser.add_argument(
        "--cap",
        dest="cap_set",
        nargs="+",
        metavar="NAME=true|false",
        default=None,
        help="For capabilities: record a capability by hand, e.g. --cap claude=false",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="For reconcile: notify on a non-FLAT verdict (what the scheduled task passes)",
    )
    # The two review passes. `_run_review` reads --final off sys.argv, but argparse has to KNOW the
    # flag or it rejects the whole command first: both scheduled review jobs were exiting 2 at the
    # parser every day, so no fact set was being built at all. --provisional is accepted and does
    # nothing, because that is the default and a job whose argv states its intent should not fail.
    parser.add_argument(
        "--final",
        action="store_true",
        help="For review: finalise the session (the next-morning pass, after earnings settles)",
    )
    parser.add_argument(
        "--provisional",
        action="store_true",
        help="For review: build the provisional pass (the default; accepted so the job argv is explicit)",
    )
    parser.add_argument("--yes", action="store_true", help="For account --set: skip the confirmation prompt")
    parser.add_argument("--host", default=None, help="For settings: bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="For settings: bind port (default 8804)")
    parser.add_argument("--no-browser", action="store_true", help="For settings: do not open a browser")
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
        help=(
            "For archive: report what would be archived without writing or deleting. "
            "For notify-trades: format every pending event and report it, sending and saving nothing. "
            "For backup: report what tonight's backup would hold, writing nothing"
        ),
    )
    parser.add_argument("--list", action="store_true", help="For backup: show the backup on hand")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="For backup: re-check the backup on hand. "
        "For sent: ask Discord whether each message still exists",
    )
    parser.add_argument(
        "--stale",
        action="store_true",
        help="For sent: name each post's inputs that changed after it went out",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="For sent: how far back, without --date (default 7)"
    )
    parser.add_argument(
        "--kind",
        default=None,
        help="For sent: only this kind (notify, digest, report, morning, weekly, payoff, payoff-intraday, "
        "correction, note). For notify-send: correction or note (default note)",
    )
    parser.add_argument("--file", default=None, help="For notify-send: the message text, from a file")
    parser.add_argument(
        "--image", action="append", default=None, help="For notify-send: attach an image (repeatable)"
    )
    parser.add_argument(
        "--refers-to",
        dest="refers_to",
        default=None,
        help="For notify-send: the Discord message id this one corrects or follows up (see `sent`)",
    )
    parser.add_argument(
        "--restore-to",
        dest="restore_to",
        default=None,
        help="For backup: extract the backup into this directory",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="For supervise: ask the running supervisor daemon to exit (via its stop file)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="For supervise: ask the running supervisor to exit so it is started again with fresh code "
        "(by the Windows service after its restart delay, else by the anchor's next probe)",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        help="For notify-status: post the day's CLOSE card (what the daily status-digest-close job passes)",
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="For restart/stop/start: a supervisor job (console, flies-paper, ...), `streamer`, or a "
        "service id -- `ps` lists them. For service: prepare | status | uninstall",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="For restart/stop/start of a daemon: act from this process even under the Windows service "
        "(what the supervisor's own child passes; from the desktop it is refused by session 0)",
    )
    parser.add_argument(
        "--result",
        default=None,
        help="For restart/stop/start: also write the result record to this path (the supervisor's hand-off)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="For stop: every daemon and every job still running (after `uninstall`; refused before)",
    )
    return parser


def _market_clock_on_posix(environ=None) -> bool:
    """Run this process (and every child it starts) on the suite's market clock when nothing chose a
    zone: many call sites take `date.today()` as the session date, and on a UTC host the date turns
    over at 19:00/20:00 ET, inside the evening jobs. The cron lines carry TZ already; this covers a
    run.py started by hand or by anything else. An explicit TZ always wins. Windows has no tzset and
    keeps the machine's own zone. True when it set the zone."""
    environ = os.environ if environ is None else environ
    if os.name == "nt" or environ.get("TZ"):
        return False
    environ["TZ"] = "America/New_York"
    import time as _time

    if hasattr(_time, "tzset"):
        _time.tzset()
    return True


def main() -> None:
    _market_clock_on_posix()
    # A default Windows console is cp1252, and the help text and several reports carry glyphs it
    # cannot encode (↔, ×, –) — argparse printing usage tracebacked before any command ran, which
    # made `--help` the first command a new user saw fail. Degrade the odd glyph to '?' instead;
    # a UTF-8 console is unaffected.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass

    args = build_parser().parse_args()

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
        "capabilities": lambda: cmd_capabilities(cfg, args),
        "config-backup": lambda: cmd_config_backup(cfg, args),
        "watchdog": lambda: cmd_watchdog(cfg),
        "preopen-check": lambda: cmd_preopen_check(cfg),
        "streamer-health": lambda: cmd_streamer_health(cfg),
        "supervise": lambda: cmd_supervise(cfg, stop=args.stop, restart=args.restart),
        "ensure-supervisor": lambda: cmd_ensure_supervisor(cfg),
        "report": lambda: cmd_report(cfg, args),
        "archive": lambda: cmd_archive(cfg, args),
        "backup": lambda: cmd_backup(cfg, args),
        "reconcile": lambda: cmd_reconcile(cfg, scheduled=args.scheduled),
        "positions": lambda: cmd_positions(cfg, args),
        "connect": lambda: cmd_connect(cfg, args),
        "account": lambda: cmd_account(cfg, args),
        "migrate-home": lambda: cmd_migrate_home(cfg, args.apply),
        "calibrate": lambda: cmd_calibrate(cfg),
        "notify-trades": lambda: cmd_notify_trades(cfg, dry_run=args.dry_run),
        "notify-desk": lambda: cmd_notify_desk(cfg),
        "notify-status": lambda: cmd_notify_status(cfg, force=args.force, close=args.close),
        "power-watch": lambda: cmd_power_watch(cfg),
        "live-positions": lambda: cmd_live_positions(cfg),
        "service": lambda: cmd_service(cfg, args.name),
        "settle-overdue-live": lambda: cmd_settle_overdue_live(cfg),
        "notify-send": lambda: cmd_notify_send(args),
        "sent": lambda: cmd_sent(args),
        "run-earnings-entry": lambda: _run_earnings(cfg, "entry"),
        "run-earnings-exit": lambda: _run_earnings(cfg, "exit"),
        "run-earnings-symbol-watch": lambda: _run_earnings_symbol_watch(cfg),
        "review": lambda: _run_review(cfg, final="--final" in sys.argv),
        "morning": lambda: _run_morning(cfg),
        "restart-console": lambda: cmd_restart_console(cfg),
        "ps": lambda: cmd_ps(cfg),
        "restart": lambda: cmd_restart(cfg, args.name, direct=args.direct, result=args.result),
        "stop": lambda: cmd_stop(cfg, args.name, everything=args.all, direct=args.direct, result=args.result),
        "start": lambda: cmd_start(cfg, args.name, direct=args.direct, result=args.result),
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
