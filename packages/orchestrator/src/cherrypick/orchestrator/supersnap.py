"""The supervisor-state snapshot — what `tasks.registry_snapshot` was to the OS scheduler.

One source of truth for "what jobs exist and their state", shared by `cherrypick status`, the
dashboard System panel, doctor, and the watchdog's job-presence checks so they can't drift. Local
file reads only (plus ONE `schtasks` query for the anchor task on Windows) — no broker, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import supervisor, tasks
from .util import pid_alive, read_json

ANCHOR_TASK = "cherrypick-supervisor"


def heartbeat_age_seconds(hb: dict[str, Any] | None = None) -> float | None:
    """Age of the supervisor heartbeat in seconds, or None when absent/unreadable."""
    hb = hb if hb is not None else read_json(supervisor.heartbeat_path())
    ts = (hb or {}).get("ts")
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(str(ts))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    except ValueError:
        return None


def supervisor_alive(hb: dict[str, Any] | None = None) -> bool:
    """Fresh heartbeat AND a live PID. Either alone can lie: a fresh file with a dead PID is a
    just-crashed daemon; a live PID with a stale file is a wedged loop — both count as down."""
    hb = hb if hb is not None else read_json(supervisor.heartbeat_path())
    age = heartbeat_age_seconds(hb)
    if age is None or age > supervisor.HEARTBEAT_FRESH_SECONDS:
        return False
    return pid_alive((hb or {}).get("pid"))


def supervisor_snapshot(cfg: dict[str, Any] | None = None, *, query_anchor: bool = True) -> dict[str, Any]:
    """Everything a status surface needs: daemon liveness, the anchor task's OS-truth, the per-job
    registry, and any derivation errors. `query_anchor=False` skips the one schtasks spawn for
    callers that only need the file-backed parts (e.g. per-tick dashboard renders)."""
    hb = read_json(supervisor.heartbeat_path())
    reg = read_json(supervisor.jobs_path())
    snap: dict[str, Any] = {
        "supervisor": {
            "running": supervisor_alive(hb),
            "pid": hb.get("pid"),
            "heartbeat_age_seconds": heartbeat_age_seconds(hb),
            "started_at": hb.get("started_at"),
            "loop_seq": hb.get("loop_seq"),
        },
        "jobs": dict(reg.get("jobs") or {}),
        "derive_errors": dict(reg.get("derive_errors") or {}),
    }
    if query_anchor:
        snap["anchor"] = {"task": ANCHOR_TASK, **tasks.query_verbose(ANCHOR_TASK)}
    return snap


def job_state(job_id: str, snap: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One job's registry entry from a snapshot (or a fresh file read). None when the supervisor
    has never derived it — callers treat that as 'job missing', the old 'task not registered'."""
    if snap is None:
        reg = read_json(supervisor.jobs_path())
        return (reg.get("jobs") or {}).get(job_id)
    return (snap.get("jobs") or {}).get(job_id)


def job_run_info(job_id: str, snap: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The `tasks.last_run_info` equivalent, sourced from the registry: when the job last started
    and how it last exited, with `still_running` standing in for SCHED_S_TASK_RUNNING (267009).
    None when the job is unknown or has never started."""
    st = job_state(job_id, snap)
    if not st or not st.get("last_start"):
        return None
    return {
        "last_run_time": st.get("last_start"),
        "last_exit_code": st.get("last_exit_code"),
        "last_exit_at": st.get("last_exit_at"),
        "still_running": bool(st.get("running_pid") and pid_alive(st.get("running_pid"))),
    }
