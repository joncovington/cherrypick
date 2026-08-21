"""The supervisor daemon — the suite's in-process replacement for ~13 Task Scheduler entries.

One long-lived process (kept alive by the single remaining OS task, `cherrypick-supervisor`, whose
`ensure-supervisor` probe restarts it within ~2 minutes) evaluates the job table derived from config
(`jobspec.derive_jobs`) on a ~1s wall-clock loop and spawns each due job as a short-lived headless
subprocess — the same `--once` ticks the scheduler used to fire, preserving per-iteration crash
isolation. The one exception is a sub-minute-cadence module loop (flies paper at 15s), which runs as
a supervised RESIDENT child using the module's own `--interval` mode, restarted on death and on
silence (the streamer's stale-restart pattern).

State surfaces (both atomic writes; the scheduler-state replacements every consumer re-sources from):
- `state/supervisor.last.json`  — heartbeat: is the daemon alive, and how recently it looped.
- `state/supervisor-jobs.json`  — per-job registry: enabled/reason, last_start, running_pid,
  last_exit_code/at, next_run, missed/backoff. `running_pid` alive is the SCHED_S_TASK_RUNNING
  (267009) equivalent the live-loop freshness check keys on.

Reliability rules carried over:
- Single instance via the shared PID lock (never steals from a live PID).
- Every child spawns with CREATE_NO_WINDOW (test_headless.py covers this file automatically).
- Overlap guard: a job whose previous run is still alive is skipped, replacing schtasks'
  no-double-fire behavior. A supervisor restart ADOPTS a still-alive prior child (marked
  `orphaned`) rather than killing or duplicating it — the MEIC lock lesson.
- Per-job crash backoff (cap 10 min) so one broken command can't hot-loop.
- Clean shutdown via a stop file (`state/supervisor.stop`) — no console events, which are exactly
  what made long-lived daemons fragile on Windows before.
- No network, no broker, no AI: the daemon decides from config + clock + local files only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import jobspec, timeutil
from .util import CREATE_NO_WINDOW, atomic_write_json, pid_alive, read_json, rotate_if_large

HEARTBEAT_FILE = "supervisor.last.json"
JOBS_FILE = "supervisor-jobs.json"
LOCK_FILE = "supervisor.lock"
STOP_FILE = "supervisor.stop"

# This module lives at src/cherrypick/orchestrator/supervisor.py, so the repo-root run.py is THREE
# parents up (same resolution as watchdog._RUN_PY — cli.py, one level shallower, uses two).
_LAUNCHER = Path(__file__).resolve().parents[3] / "run.py"

# How stale the heartbeat may be before ensure-supervisor treats the daemon as dead. The loop writes
# it every HEARTBEAT_WRITE_SECONDS; 90s tolerates a slow pass or a paused clock without flapping.
HEARTBEAT_WRITE_SECONDS = 5
HEARTBEAT_FRESH_SECONDS = 90

# A resident orphan (adopted from a prior supervisor) that died while we held no handle. Not a real
# exit status -- the code is unknowable by construction -- so it is given one that cannot collide
# with a child's own, and is treated as a failure purely so the backoff ladder bounds the respawn.
_EXIT_UNKNOWN = -3

# A windowed resident that exited 0 before it had settled. Not a session end -- a loop cannot finish
# a session in a second -- so it is recorded distinctly and takes the backoff ladder.
_EXIT_TOO_SOON = -4

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 600
# A resident child younger than this is never judged for silence — the settling grace the streamer
# taught (a just-started process hasn't had time to produce output yet).
_RESIDENT_SETTLE_SECONDS = 90


def heartbeat_path() -> Path:
    return cfgmod.STATE_DIR / HEARTBEAT_FILE


def jobs_path() -> Path:
    return cfgmod.STATE_DIR / JOBS_FILE


def lock_path() -> Path:
    return cfgmod.STATE_DIR / LOCK_FILE


def stop_path() -> Path:
    return cfgmod.STATE_DIR / STOP_FILE


def arm_record_path(module: str) -> Path:
    """The per-module live arm record (`state/<module>-live-arm.json`) — written only by the
    module's own human-confirmed arm command, read by the supervisor (job enablement) and the
    watchdog (dead-man's backstop). One file, three readers, zero ambiguity about 'armed'."""
    return cfgmod.STATE_DIR / f"{module}-live-arm.json"


def read_arm_records(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if mcfg.get("live"):
            rec = read_json(arm_record_path(name), default=None)
            if isinstance(rec, dict) and rec:
                records[name] = rec
    return records


def _log(msg: str) -> None:
    try:
        path = cfgmod.log_file("supervisor.log")
        rotate_if_large(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _terminate_pid(pid: int) -> bool:
    """Terminate one process, leaving anything it spawned alone. Only ever called on PIDs this daemon
    recorded as its own child, or on the daemon itself (`cherrypick uninstall`)."""
    try:
        import psutil  # type: ignore

        psutil.Process(pid).terminate()
        return True
    except ImportError:
        pass
    except Exception:
        return False
    try:
        if os.name == "nt":
            import ctypes

            process_terminate = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
            if not handle:
                return False
            ok = bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
            ctypes.windll.kernel32.CloseHandle(handle)
            return ok
        os.kill(pid, 15)
        return True
    except (OSError, SystemError):
        return False


def _terminate_tree(pid: int) -> bool:
    """Terminate a child and everything it spawned. Only ever called on PIDs this daemon's registry
    recorded as its own children.

    The tree, not the process, because a job's argv is not always the thing holding the resources. The
    console's `run.py` is a launcher that runs the Node server as a CHILD, so killing only the PID the
    registry holds leaves the server alive — still bound to :5070, still writing its heartbeat — while
    the supervisor starts a replacement that cannot bind and dies. That turns one silence restart into
    a permanent crash-loop, which is exactly the failure the silence check exists to end. Measured on
    2026-08-12, before this killed the tree.

    Best-effort and bounded: waits briefly for the tree to actually go, since the replacement is
    spawned right after and a still-held listening port would fail it.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore

    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            procs = parent.children(recursive=True) + [parent]
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=10)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
            return True
        except Exception:
            return False

    try:
        if os.name == "nt":
            # /T takes the tree, /F forces it. Without psutil this is the only way to reach a
            # grandchild — the Win32 API has no "children of" query.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=15,
            )
            return True
        os.killpg(os.getpgid(pid), 15)
        return True
    except (OSError, SystemError, subprocess.SubprocessError):
        return False


class Supervisor:
    """The daemon's state machine, factored so tests can drive single passes with a fake clock."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        self._cfg = cfg
        self._cfg_pinned = cfg is not None  # tests pass cfg directly; production reloads on mtime
        self._cfg_mtime: float | None = None
        self._handles: dict[str, subprocess.Popen] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._holidays: set[str] = set()
        self._holidays_day: str | None = None
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._loop_seq = 0
        self._last_heartbeat = 0.0

    # ----------------------------------------------------------------- config / derivation
    def _load_cfg(self) -> dict[str, Any]:
        if self._cfg_pinned:
            return self._cfg or {}
        try:
            path = cfgmod.effective_config_path()
            mtime = path.stat().st_mtime
            if self._cfg is None or mtime != self._cfg_mtime:
                self._cfg = cfgmod.load_config()
                self._cfg_mtime = mtime
                _log(f"config loaded ({path.name}, mtime {mtime})")
        except Exception as exc:
            # Keep running on the last good config — a transient config problem must not kill the
            # daemon that everything else's liveness depends on.
            _log(f"config reload failed, keeping last good: {type(exc).__name__}: {exc}")
        return self._cfg or {}

    def _holiday_set(self, now: datetime) -> set[str]:
        day = now.strftime("%Y-%m-%d")
        if day != self._holidays_day:
            self._holidays = timeutil.load_holidays()
            self._holidays_day = day
        return self._holidays

    # ----------------------------------------------------------------- state bookkeeping
    def adopt_prior_state(self) -> None:
        """Load the persisted registry. A still-alive prior child is ADOPTED (orphaned=true, still
        counted by the overlap guard) — never killed, never duplicated. Dead PIDs are cleared."""
        data = read_json(jobs_path())
        self._state = dict((data or {}).get("jobs") or {})
        for jid, st in self._state.items():
            pid = st.get("running_pid")
            if pid and pid_alive(pid):
                st["orphaned"] = True
                _log(f"{jid}: adopted running child pid {pid} from a prior supervisor")
            elif pid:
                st["running_pid"] = None

    def _job_running(self, spec: jobspec.JobSpec, st: dict[str, Any]) -> bool:
        handle = self._handles.get(spec.id)
        if handle is not None:
            code = handle.poll()
            if code is None:
                return True
            self._record_exit(spec, st, code)
            del self._handles[spec.id]
            return False
        pid = st.get("running_pid")
        if pid and pid_alive(pid):
            return True  # adopted orphan still going
        if pid:
            # Orphan finished while we weren't holding a handle: exit code unknowable. This used to
            # record nothing at all, which left `backoff_until` untouched and so gave a dead orphan
            # no throttle whatsoever -- the same respawn storm as the clean-exit path, by a second
            # route. We cannot tell a finished session from a crash here, so take the middle: count
            # it as a failure so the ladder bounds the rate, but keep restarting it. An unknown is
            # throttled and made visible, never silently stopped and never hot-looped.
            self._record_exit(spec, st, _EXIT_UNKNOWN)
        return False

    def _record_exit(self, spec: jobspec.JobSpec, st: dict[str, Any], code: int) -> None:
        st["running_pid"] = None
        st["last_exit_code"] = code
        st["last_exit_at"] = _utc_iso()
        # A WINDOWED resident exiting 0 is a statement -- "my own gate closed", or "another instance
        # holds my lock" -- not "the run finished, go again". Reading it as the latter is what
        # produced the 16:00 storm: the module's gate closes on the dot while `in_window` still says
        # 16:00 (whole minutes, inclusive), so the child exited 0, `code == 0` erased the only
        # throttle there is, and the ~1s loop respawned it 53 times in that minute -- with flies
        # doing the same beside it, and not one backoff line between them.
        #
        # Believe it only once it has SETTLED, though. A child that exits 0 the instant it starts is
        # a misconfiguration, not a session end, and taking that at its word would stop the job for
        # its whole window on the first tick. That one takes the ladder, which bounds the spawn rate
        # without ever declaring the job done.
        if code == 0 and self._resident_windowed(spec):
            if self._resident_settled(spec, st):
                st["module_stopped"] = True
                st["consecutive_failures"] = 0
                st["backoff_until"] = None
                _log(f"{spec.id}: module reports session complete (exit 0) — idle until its window reopens")
                return
            code = _EXIT_TOO_SOON

        if code == 0:
            st["consecutive_failures"] = 0
            st["backoff_until"] = None
        else:
            n = int(st.get("consecutive_failures") or 0) + 1
            st["consecutive_failures"] = n
            cap = spec.backoff_cap_seconds or _BACKOFF_CAP_SECONDS
            delay = min(cap, _BACKOFF_BASE_SECONDS * (2 ** (n - 1)))
            st["backoff_until"] = time.time() + delay
            _log(f"{spec.id}: exit {code} (failure #{n}), backoff {delay}s")

    def _spawn(self, spec: jobspec.JobSpec, st: dict[str, Any]) -> bool:
        try:
            handle = subprocess.Popen(
                list(spec.argv),
                cwd=spec.cwd or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self._record_exit(spec, st, -1)
            _log(f"{spec.id}: spawn failed: {exc}")
            return False
        self._handles[spec.id] = handle
        st["running_pid"] = handle.pid
        st["last_start"] = _utc_iso()
        st.pop("orphaned", None)
        return True

    # ----------------------------------------------------------------- one pass
    def pass_once(self, now: datetime | None = None) -> dict[str, Any]:
        cfg = self._load_cfg()
        tz = cfg.get("timezone", "America/New_York")
        now = now or timeutil.now_et(tz)
        holidays = self._holiday_set(now)
        jobs, errors = jobspec.derive_jobs(
            cfg,
            pythonw=cfgmod.pythonw_exe(),
            launcher=str(_LAUNCHER),
            now=now,
            arm_records=read_arm_records(cfg),
        )
        for jid, err in errors.items():
            if self._errors.get(jid) != err:
                _log(f"{jid}: derivation failed, job disabled: {err}")
        self._errors = errors
        self._prune_retired(jobs, errors)

        started: list[str] = []
        for spec in jobs:
            st = self._state.setdefault(spec.id, {})
            st.update(
                {
                    "enabled": spec.enabled,
                    "enabled_reason": spec.enabled_reason,
                    "kind": spec.kind,
                    "interval_seconds": spec.interval_seconds or None,
                    "schedule": spec.describe(),
                }
            )
            if spec.kind == jobspec.KIND_RESIDENT:
                # What this job's liveness is judged against, recorded so a reader does not have to
                # re-derive the whole job table to find out. `heartbeat_seen` false means the module
                # publishes nothing, which is NOT silence-supervised (deliberately -- restarting on
                # "I can't tell" is the failure this whole area is recovering from) and so is a gap
                # only the watchdog can report.
                st["silence_file"] = spec.silence_file
                st["heartbeat_seen"] = bool(spec.silence_file and os.path.exists(spec.silence_file))
                if self._manage_resident(spec, st, now, holidays):
                    started.append(spec.id)
                continue
            if self._job_running(spec, st):
                continue  # overlap guard — schtasks' no-double-fire, preserved
            if st.get("backoff_until") and time.time() < float(st["backoff_until"]):
                continue
            fire, reason, patch = jobspec.should_start(spec, st, now, holidays)
            st.update(patch)
            if spec.kind == jobspec.KIND_INTERVAL and spec.enabled:
                st["next_run"] = _epoch_iso(st.get("next_run_epoch"))
            if fire and self._spawn(spec, st):
                started.append(spec.id)

        self._loop_seq += 1
        self._write_registry(errors)
        self._write_heartbeat(now, len(jobs))
        return {"started": started, "jobs": len(jobs), "errors": errors}

    def _manage_resident(
        self, spec: jobspec.JobSpec, st: dict[str, Any], now: datetime, holidays: set[str]
    ) -> bool:
        want, why = jobspec.resident_should_run(spec, now, holidays)
        alive = self._job_running(spec, st)
        if not want:
            # Outside its window the child exits on its own (the module loop is session-scoped);
            # never terminate here — a settlement or final write may still be in flight.
            st["resident_state"] = why or "idle"
            # The window is shut, so the module's "I am done" is spent: the next open starts clean.
            # Cleared here rather than on the opening edge because this branch is the only place
            # that runs for certain between two windows. The start counter resets with it, which is
            # what makes it mean "starts since this window opened" and therefore readable as churn.
            st.pop("module_stopped", None)
            st.pop("starts_in_window", None)
            st.pop("starts_window_day", None)
            return False
        # A job with NO window never takes the branch above, so without this its start counter
        # accumulated for the life of the registry: the console latched a churn WARN on 2026-08-21
        # showing 27 starts — every one of them from the previous evening's deliberate
        # rebuild/restart cycles, none from the day the alert fired. A windowless resident's
        # "window" is the calendar day; windowed jobs reset at window close as before, and this
        # extra day-boundary reset is a no-op for them (no suite window spans midnight).
        today = now.date().isoformat()
        if st.get("starts_window_day") != today:
            st.pop("starts_in_window", None)
            st["starts_window_day"] = today
        if alive:
            st["resident_state"] = "running"
            if self._resident_silent(spec, st):
                pid = st.get("running_pid")
                _log(f"{spec.id}: silent > {spec.silence_seconds}s, restarting (pid {pid})")
                handle = self._handles.pop(spec.id, None)
                # Kill the tree first: a launcher-style job (the console's run.py -> node) keeps its
                # real server alive if only the tracked PID is terminated, and the replacement then
                # cannot bind. Then reap the handle so no zombie is left behind.
                if pid:
                    _terminate_tree(pid)
                if handle is not None and handle.poll() is None:
                    handle.terminate()
                    try:
                        handle.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        handle.kill()
                self._record_exit(spec, st, -2)  # silence counts as a failure → backoff
                return False
            # A resident job never exits cleanly in normal operation, so `_record_exit(code=0)` --
            # the only other place failure/backoff bookkeeping resets -- never fires for it. Left
            # alone, a scar from hours-old failures (a crash-loop the night before, say) sits in
            # `consecutive_failures` forever: it doesn't block anything while the child stays alive,
            # but the NEXT unrelated failure computes its backoff off that stale count instead of
            # starting over, jumping straight to the 10-minute cap instead of the normal 30s.
            # Settled + alive + not silent is this job's version of a clean exit: clear the scar.
            if self._resident_settled(spec, st) and (
                st.get("consecutive_failures") or st.get("backoff_until") is not None
            ):
                st["consecutive_failures"] = 0
                st["backoff_until"] = None
                _log(f"{spec.id}: settled and healthy, failure/backoff history cleared")
            return False
        if st.get("module_stopped"):
            # The module said its session was over. Believe it for the rest of this window rather
            # than restarting it into the same gate once a second. If it said so WRONGLY it now
            # stays down until the window reopens, which is the trade this makes deliberately --
            # a loud silence over a quiet restart loop -- and `watchdog._check_resident_health`
            # is what makes the silence loud.
            st["resident_state"] = "module reports session complete"
            return False
        if st.get("backoff_until") and time.time() < float(st["backoff_until"]):
            st["resident_state"] = "backoff"
            return False
        ok = self._spawn(spec, st)
        st["resident_state"] = "running" if ok else "start failed"
        if ok:
            # Starts since this window opened. Deliberately NOT `consecutive_failures`, which cannot
            # serve: a clean exit resets it, and a clean exit is exactly the storm's own signature --
            # the 2026-08-17 registry showed 0 failures beside 161 spawns. This is the only number
            # that would have made either the churn or the storm legible to anything but a human
            # reading supervisor.log.
            st["starts_in_window"] = int(st.get("starts_in_window") or 0) + 1
            _log(f"{spec.id}: resident child started (pid {st['running_pid']})")
        return ok

    def _prune_retired(self, jobs: list[jobspec.JobSpec], errors: dict[str, str]) -> None:
        """Drop registry rows for jobs config no longer derives.

        A retired job's row was kept forever, frozen at whatever it last did — and because it is no
        longer evaluated, it is never marked missed either. `earnings-entry` sat at `enabled: true`
        with a fire date from the day before its lifecycle cutover, which is indistinguishable from a
        scheduled job that has silently stopped firing, and cost a real diagnosis to tell apart.
        Registry rows are a picture of what the supervisor is driving; a job it is not driving does
        not belong in it.

        Two things are deliberately NOT pruned. A row whose child is still alive stays, or the
        overlap guard loses track of a process it would otherwise still reap. And a job whose
        derivation FAILED this pass stays too: that job is missing because something is broken, not
        because it was retired, and dropping its history would erase the evidence.
        """
        keep = {spec.id for spec in jobs} | set(errors)
        for jid in [j for j in self._state if j not in keep]:
            if self._state[jid].get("running_pid"):
                continue
            self._state.pop(jid, None)
            _log(f"{jid}: no longer derived from config — registry row dropped")

    def _resident_settled(self, spec: jobspec.JobSpec, st: dict[str, Any]) -> bool:
        """True once a resident child has been alive long enough to be judged at all -- the
        streamer's settling grace. Silence and recovery are both decided only past this point."""
        started = _iso_epoch(st.get("last_start"))
        if started is None:
            return False
        return time.time() - started >= max(_RESIDENT_SETTLE_SECONDS, spec.silence_seconds)

    def _resident_windowed(self, spec: jobspec.JobSpec) -> bool:
        """A resident job whose clean exit could mean "my session is over".

        Both halves are load-bearing. **Resident**, because an interval job exiting 0 has genuinely
        finished a run and is gated by `next_run_epoch` — nothing about it needs believing.
        **Windowed**, because the console declares no window and no trading-day gate on purpose (a
        read surface only up during RTH cannot read the session that just ended), so "terminal for
        the window" is meaningless for it and a server exiting cleanly is never expected. Believing
        an unwindowed resident would take the suite's only read surface down and leave it down.
        """
        return spec.kind == jobspec.KIND_RESIDENT and bool(spec.window_start and spec.window_end)

    def _resident_silent(self, spec: jobspec.JobSpec, st: dict[str, Any]) -> bool:
        if not spec.silence_file or not spec.silence_seconds:
            return False
        if not self._resident_settled(spec, st):
            return False  # settling — never judge a child younger than the silence window
        try:
            age = time.time() - os.path.getmtime(spec.silence_file)
        except OSError:
            return False  # no file yet: the settle grace above covers startup
        return age > spec.silence_seconds

    # ----------------------------------------------------------------- state files
    def _write_registry(self, errors: dict[str, str]) -> None:
        atomic_write_json(
            jobs_path(),
            {
                "schema": 1,
                "written_at": _utc_iso(),
                "supervisor_pid": os.getpid(),
                "derive_errors": errors,
                "jobs": self._state,
            },
        )

    def _write_heartbeat(self, now: datetime, job_count: int, force: bool = False) -> None:
        t = time.time()
        if not force and t - self._last_heartbeat < HEARTBEAT_WRITE_SECONDS:
            return
        self._last_heartbeat = t
        atomic_write_json(
            heartbeat_path(),
            {
                "ts": _utc_iso(),
                "et": now.isoformat(),
                "pid": os.getpid(),
                "started_at": self._started_at,
                "loop_seq": self._loop_seq,
                "jobs": job_count,
                "resident_children": sum(
                    1
                    for s in self._state.values()
                    if s.get("kind") == jobspec.KIND_RESIDENT and s.get("running_pid")
                ),
            },
        )


def _utc_iso() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    from datetime import timezone

    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(timespec="seconds")


def _iso_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def run(cfg: dict[str, Any] | None = None, *, max_passes: int | None = None) -> dict[str, Any]:
    """Run the daemon loop until the stop file appears (or `max_passes`, for tests).

    Single-instance: refuses to start when a live supervisor holds the lock. A stale stop file left
    by a previous shutdown is cleared at startup so it can't kill a fresh daemon.
    """
    cfgmod.ensure_dirs()
    if not os.environ.get("CHERRYPICK_SUPERVISOR_NO_LOCK"):
        from .util import acquire_pid_lock, release_pid_lock

        if not acquire_pid_lock(lock_path()):
            return {"ok": False, "detail": "supervisor already running"}
    try:
        stop_path().unlink(missing_ok=True)
        sup = Supervisor(cfg)
        sup.adopt_prior_state()
        _log(f"supervisor started (pid {os.getpid()})")
        passes = 0
        while True:
            if stop_path().exists():
                _log("stop file seen — shutting down")
                stop_path().unlink(missing_ok=True)
                break
            sup.pass_once()
            passes += 1
            if max_passes is not None and passes >= max_passes:
                break
            time.sleep(1)
        return {"ok": True, "passes": passes}
    finally:
        if not os.environ.get("CHERRYPICK_SUPERVISOR_NO_LOCK"):
            release_pid_lock(lock_path())


def request_stop() -> dict[str, Any]:
    """Ask a running supervisor to exit (it polls the stop file every pass)."""
    cfgmod.ensure_dirs()
    stop_path().write_text(json.dumps({"requested_at": _utc_iso()}), encoding="utf-8")
    return {"ok": True, "detail": f"stop requested via {stop_path().name}"}


if __name__ == "__main__":
    result = run()
    if sys.stdout is not None:
        json.dump(result, sys.stdout, indent=2)
        print()
