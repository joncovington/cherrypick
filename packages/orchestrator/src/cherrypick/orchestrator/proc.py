"""Restart, stop and start the suite's processes BY NAME -- never by a PID someone looked up.

Before this, every restart began with finding a PID (the registry, a port, `Get-NetTCPConnection`)
and ended with a `taskkill` the supervisor learned about afterwards: racy, read as a crash, and for a
supervised job undone a second later. The PIDs were always stored -- the supervisor's registry, the
daemons' own PID files -- just never the easy path. Now:

  * a SUPERVISOR JOB (`console`, `flies-paper`, ...) is restarted by the supervisor itself: this asks
    (`supervisor.request_restart`) and waits for its answer. The supervisor stops the tree, confirms
    it gone by PID AND creation time, and only then relaunches -- so a restart cannot leave two;
  * a job is STOPPED by a hold (`holds`): the supervisor stops it and will not relaunch it until
    `start`. With the supervisor down, a stop kills directly, but only a process whose PID and
    creation time still match the registry -- never a reused PID, never "whoever holds the port";
  * a DAEMON (`streamer`, a service such as `gex-recorder`) goes through its own status/stop/start
    commands, under a hold for the duration, so the watchdog does not restart it mid-restart.

Nothing here launches a supervisor job itself: starting is always the supervisor's, which is what
keeps a CLI from ever adding a second copy.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import holds, supersnap, supervisor
from .util import CREATE_NO_WINDOW, first_json

BY = "run.py"
RESTART_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 30.0
POLL_S = 0.5


# --------------------------------------------------------------------------- names


# The suite's Dolt sql-server, managed like the streamer from 2026-10-09: the supervisor's `ensure-dolt`
# job only ever started it, and under the Windows service nothing on the desktop could stop it.
DOLT_DAEMON = "dolt-server"


def daemons(cfg: dict[str, Any]) -> dict[str, tuple[Path, dict[str, Any], bool]]:
    """{name: (root, spec, is_producer)} for the streamer, every enabled service, and the Dolt
    sql-server when a module declares one (driven through `run.py dolt-server-*`)."""
    out: dict[str, tuple[Path, dict[str, Any], bool]] = {}
    spec = cfg.get("streamer") or {}
    if spec.get("enabled"):
        out["streamer"] = (cfgmod.module_root(spec, "streamer"), spec, True)
    for svc in cfgmod.enabled_services(cfg):
        out[svc["id"]] = (cfgmod.module_root(svc, svc["id"]), svc, False)
    if any((m.get("paper") or {}).get("dolt_service") for m in cfgmod.enabled_modules(cfg).values()):
        launcher = str(supervisor._LAUNCHER)
        out[DOLT_DAEMON] = (
            supervisor._LAUNCHER.parent,
            {
                "status_argv": [launcher, "dolt-server-status"],
                "start_argv": [launcher, "dolt-server-start"],
                "stop_argv": [launcher, "dolt-server-stop"],
            },
            False,
        )
    return out


def jobs() -> dict[str, dict[str, Any]]:
    return supersnap.all_job_states()


def resolve(cfg: dict[str, Any], name: str | None) -> tuple[str, str] | dict[str, Any]:
    """('job'|'daemon', name), or an error naming what exists."""
    if name in daemons(cfg):
        return "daemon", name
    if name in jobs():
        return "job", name
    known = sorted(jobs()) + sorted(daemons(cfg))
    return {"ok": False, "error": f"unknown name {name!r}", "known": known}


# --------------------------------------------------------------------------- daemons


def _run(root: Path, argv: list[str], timeout: float = 30) -> dict[str, Any]:
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return {"code": r.returncode, **first_json(r.stdout)}
    except Exception as exc:  # noqa: BLE001 -- reported, never raised into a CLI
        return {"code": -1, "error": f"{type(exc).__name__}: {exc}"}


def daemon_status(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    return _run(root, spec["status_argv"], timeout=15)


def _wait_daemon(root: Path, spec: dict[str, Any], *, running: bool, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    status: dict[str, Any] = {}
    while time.time() < deadline:
        status = daemon_status(root, spec)
        if bool(status.get("running")) is running:
            return status
        time.sleep(POLL_S)
    return status


def stop_daemon(cfg, name: str, *, hold: bool = True) -> dict[str, Any]:
    root, spec, _ = daemons(cfg)[name]
    if hold:
        holds.hold(name, by=f"{BY} stop")
    stopped = _run(root, spec["stop_argv"])
    status = _wait_daemon(root, spec, running=False, timeout=STOP_TIMEOUT_S)
    ok = not status.get("running")
    return {"ok": ok, "name": name, "stopped": ok, "held": hold, "stop": stopped}


def start_daemon(cfg, name: str, ensure: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    root, spec, producer = daemons(cfg)[name]
    holds.release(name)
    started = ensure(root, spec, name, producer=producer)
    status = _wait_daemon(root, spec, running=True, timeout=STOP_TIMEOUT_S)
    return {"ok": bool(status.get("running")), "name": name, "start": started, "pid": status.get("pid")}


def restart_daemon(cfg, name: str, ensure: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Stop, confirm stopped, start -- under a hold the whole way, so the watchdog's keep-alive does
    not start a second one in the gap. The daemons' own PID guards refuse a double start as well."""
    before = daemon_status(*daemons(cfg)[name][:2]).get("pid")
    stop = stop_daemon(cfg, name, hold=True)
    if not stop["ok"]:
        # Still running, so the hold guards nothing -- and left in place it would stop the keep-alive
        # from ever restarting the daemon if it later died (2026-10-09: a failed restart of the gex
        # recorder left it held, running old code, with nothing to bring it back).
        holds.release(name)
        return {
            "ok": False,
            "name": name,
            "error": "did not stop; not restarted",
            "stop": stop,
            "held": False,
        }
    start = start_daemon(cfg, name, ensure)  # releases the hold
    return {**start, "old_pid": before, "new_pid": start.get("pid")}


# --------------------------------------------------------------------------- supervisor jobs


def _job(name: str) -> dict[str, Any]:
    return supersnap.job_state(name) or {}


def restart_job(name: str, *, timeout: float = RESTART_TIMEOUT_S) -> dict[str, Any]:
    """Ask the supervisor to restart `name` and wait for its answer."""
    if not supersnap.supervisor_alive():
        return {
            "ok": False,
            "name": name,
            "error": "the supervisor is not running, so nothing would start it again; "
            "`run.py install` starts the supervisor and every job",
        }
    old = _job(name).get("running_pid")
    rid = uuid.uuid4().hex
    supervisor.request_restart(name, by=f"{BY} restart", request_id=rid)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _job(name)
        answer = st.get("last_restart") or {}
        if answer.get("id") == rid:
            if answer.get("result") == "old process did not exit":
                return {"ok": False, "name": name, "old_pid": old, "error": answer["result"]}
            new = st.get("running_pid")
            if st.get("kind") != "resident" or (new and new != old):
                return {
                    "ok": True,
                    "name": name,
                    "old_pid": old,
                    "new_pid": new,
                    "result": answer.get("result"),
                }
            if st.get("resident_state") not in (None, "running"):
                # Stopped and not relaunched because its window is shut: it starts when that opens.
                return {
                    "ok": True,
                    "name": name,
                    "old_pid": old,
                    "new_pid": None,
                    "state": st["resident_state"],
                }
        time.sleep(POLL_S)
    return {
        "ok": False,
        "name": name,
        "old_pid": old,
        "error": f"no answer from the supervisor in {timeout:.0f}s",
    }


def stop_job(name: str, *, timeout: float = STOP_TIMEOUT_S) -> dict[str, Any]:
    """Hold `name`. The supervisor stops it and will not relaunch it; with the supervisor down,
    stop it here -- only if the registry's PID is still that process (PID and creation time)."""
    holds.hold(name, by=f"{BY} stop")
    if supersnap.supervisor_alive():
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = _job(name)
            if st.get("held") and not st.get("running_pid"):
                return {"ok": True, "name": name, "held": True, "by": "supervisor"}
            time.sleep(POLL_S)
        return {"ok": False, "name": name, "held": True, "error": f"still running after {timeout:.0f}s"}
    st = _job(name)
    pid, started = st.get("running_pid"), st.get("pid_started_at")
    if not pid or not supervisor.same_process(pid, started):
        return {"ok": True, "name": name, "held": True, "detail": "not running"}
    supervisor._terminate_tree(int(pid))
    deadline = time.time() + 10
    while supervisor.same_process(pid, started) and time.time() < deadline:
        time.sleep(POLL_S)
    gone = not supervisor.same_process(pid, started)
    return {"ok": gone, "name": name, "held": True, "killed_pid": pid, "by": "cli (supervisor down)"}


def start_job(name: str, *, timeout: float = STOP_TIMEOUT_S) -> dict[str, Any]:
    """Release the hold; the supervisor launches it (a resident now, a scheduled job on schedule)."""
    released = holds.release(name)
    if not supersnap.supervisor_alive():
        return {
            "ok": True,
            "name": name,
            "released": released,
            "detail": "the supervisor is not running; `run.py install`",
        }
    if _job(name).get("kind") != "resident":
        return {"ok": True, "name": name, "released": released, "detail": "runs on its schedule"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _job(name)
        if st.get("running_pid"):
            return {"ok": True, "name": name, "released": released, "pid": st["running_pid"]}
        if st.get("resident_state") not in (None, "held", "running"):
            return {"ok": True, "name": name, "released": released, "state": st["resident_state"]}
        time.sleep(POLL_S)
    return {"ok": False, "name": name, "released": released, "error": f"not running after {timeout:.0f}s"}


# --------------------------------------------------------------------------- everything


def stop_all(cfg, *, anchor_registered: bool) -> dict[str, Any]:
    """The tail of a full shutdown: every daemon, and every registry job still running. Refused while
    anything could start them again -- a live supervisor, or the anchor task that restarts it."""
    if supersnap.supervisor_alive() or anchor_registered:
        return {
            "ok": False,
            "error": "the supervisor (or its anchor task) would start everything again; "
            "run `run.py uninstall` first, or stop jobs by name",
        }
    results: dict[str, Any] = {}
    for name in daemons(cfg):
        results[name] = stop_daemon(cfg, name, hold=False)
    for name, st in jobs().items():
        pid, started = st.get("running_pid"), st.get("pid_started_at")
        if pid and supervisor.same_process(pid, started):
            supervisor._terminate_tree(int(pid))
            results[name] = {"ok": not supervisor.same_process(pid, started), "killed_pid": pid}
    return {"ok": all(r.get("ok") for r in results.values()), "stopped": results}


def ps(cfg) -> dict[str, Any]:
    """What is running, by name: PID, whether it is still provably that process, state and holds."""
    held = holds.all_holds()
    rows = []
    for name, st in sorted(jobs().items()):
        pid = st.get("running_pid")
        if not pid and name not in held and st.get("kind") != "resident":
            continue
        last = (st.get("last_error") or "").strip().splitlines()
        rows.append(
            {
                "name": name,
                "kind": "job",
                "pid": pid,
                "identity_ok": supervisor.same_process(pid, st.get("pid_started_at")) if pid else None,
                "state": st.get("resident_state") or ("running" if pid else "idle"),
                "held": holds.describe(name, held[name]) if name in held else None,
                "last_exit": st.get("last_exit_code"),
                "last_error": last[-1] if last else None,
            }
        )
    for name, (root, spec, _) in sorted(daemons(cfg).items()):
        status = daemon_status(root, spec)
        rows.append(
            {
                "name": name,
                "kind": "daemon",
                "pid": status.get("pid"),
                "state": "running" if status.get("running") else "stopped",
                "held": holds.describe(name, held[name]) if name in held else None,
            }
        )
    return {"ok": True, "supervisor_alive": supersnap.supervisor_alive(), "processes": rows}
