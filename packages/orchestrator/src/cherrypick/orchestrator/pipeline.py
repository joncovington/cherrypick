"""`run.py pipeline <name>` -- an ordered data run: one supervisor job, its steps one after another.

The morning and evening data runs (`jobspec.PIPELINES`) were about thirty jobs chained only by start
times. When an early step ran long or failed, a later one read the previous file and nothing said so.
Here the supervisor starts ONE job and this runs its steps, each as its own subprocess with the argv the
job it replaced had. The orchestrator still drives everything by subprocess, never by import.

- A step starts only after the one before it has ended, and never before its own configured time
  (`PipelineStep.at_et`), so a step keeps the spacing its time gave it (the Yahoo fetches, for one).
- A step that exits 0 is `ok`. One that does not is `failed`, and the run stops there, the steps after
  it recorded as `not_run` -- unless the step is `independent`: then the failure is recorded and the run
  carries on. See `jobspec.PIPELINES` for which, and why.
- A step that is off in config, or whose trading-day gate fails today, is `skipped`, not failed.
- Every result -- status, exit code, seconds, the stderr tail of a failure -- is written to
  `state/pipeline.<name>.json` after each step. The supervisor copies the compact form (`summary`) into
  the job's registry row, so `status` and the console's System page show which step failed; the
  watchdog's `jobs.failed` finding names it too (`failure_text`).
- The process exits 1 when any step failed (independent ones included), 0 otherwise, and its last
  stderr lines name the pipeline and the step, which is what the registry's `last_error` keeps.

**Retry.** The supervisor retries a failed pipeline (`jobspec.RETRY_ON_FAILURE`) inside its catch-up
window. The next run of the same day resumes: steps that were `ok` are not run again, and the first
failed one, and everything after it, runs. A pipeline that finished `ok` starts afresh the next time it
is run (`--fresh` forces that after a failure too).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from . import config as cfgmod
from . import jobspec, timeutil
from .util import CREATE_NO_WINDOW

_TAIL_LINES = 8
_LINE_CHARS = 300
_TAIL_CHARS = 1200


def state_path(name: str):
    return cfgmod.state_file(f"pipeline.{name}.json")


def step_log_path(name: str, step_id: str):
    return cfgmod.log_file(f"jobs/{name}.{step_id}.stderr.log")


def load_state(name: str) -> dict[str, Any]:
    try:
        doc = json.loads(state_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(name: str, doc: dict[str, Any]) -> None:
    from cherrypick.core.jsonio import write_json_atomic

    write_json_atomic(state_path(name), doc)


def tail_of(path) -> str | None:
    """The last lines a step wrote to stderr, account numbers masked (the text travels into the state
    file, the registry, `status` and the watchdog's findings), or None when it wrote nothing."""
    try:
        data = path.read_bytes()[-64_000:]
    except OSError:
        return None
    lines = [
        line[:_LINE_CHARS]
        for line in data.decode("utf-8", errors="replace").splitlines()
        if line.strip() and not line.startswith("--- ")
    ]
    if not lines:
        return None
    from cherrypick.core.redact import redact_accounts

    return redact_accounts("\n".join(lines[-_TAIL_LINES:]))[-_TAIL_CHARS:]


def spawn_step(step: jobspec.PipelineStep, stderr_path) -> int:
    """Run one step to its end; its exit code (a launch failure is 127). stdout is discarded, stderr
    goes to a file of the step's own (a file, never a pipe, as the supervisor does for its jobs)."""
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with stderr_path.open("wb") as err:
        try:
            proc = subprocess.run(
                list(step.argv),
                cwd=step.cwd or None,
                stdout=subprocess.DEVNULL,
                stderr=err,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            err.write(f"could not start {step.argv[0]}: {exc}\n".encode())
            return 127
    return proc.returncode


def _find(cfg: dict[str, Any], name: str, now: datetime) -> jobspec.JobSpec | None:
    from . import supervisor

    jobs, _ = jobspec.derive_jobs(
        cfg,
        pythonw=cfgmod.pythonw_exe(),
        launcher=str(supervisor._LAUNCHER),
        now=now,
        arm_records=supervisor.read_arm_records(cfg),
    )
    return next((j for j in jobs if j.id == name and j.steps), None)


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = jobspec._hhmm(hhmm)
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def run(
    name: str,
    cfg: dict[str, Any] | None = None,
    *,
    fresh: bool = False,
    spec: jobspec.JobSpec | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    spawn: Callable[[jobspec.PipelineStep, Any], int] = spawn_step,
    holidays: set[str] | None = None,
) -> dict[str, Any]:
    """Run the pipeline `name`; the result document (also in `state/pipeline.<name>.json`)."""
    cfg = cfgmod.load_config() if cfg is None else cfg
    tz = cfg.get("timezone", "America/New_York")
    now_fn = now_fn or (lambda: timeutil.now_et(tz))
    started = now_fn()
    spec = spec or _find(cfg, name, started)
    if spec is None:
        return {"pipeline": name, "result": "failed", "error": f"no pipeline {name!r} in the job table"}
    holidays = timeutil.load_holidays() if holidays is None else holidays
    key = started.strftime("%Y-%m-%d")

    prior = {} if fresh else load_state(name)
    resume = prior.get("run_key") == key and prior.get("result") in ("failed", "running")
    done = {s["id"]: s for s in prior.get("steps") or [] if s.get("status") == "ok"} if resume else {}
    doc: dict[str, Any] = {
        "pipeline": name,
        "run_key": key,
        "started_at": started.isoformat(),
        "finished_at": None,
        "result": "running",
        "failed_step": None,
        "resumed": bool(done),
        "steps": [],
    }
    t0 = time.monotonic()
    stopped_by: str | None = None
    for step in spec.steps:
        entry: dict[str, Any] = {"id": step.id, "status": "not_run", "independent": step.independent}
        doc["steps"].append(entry)
        if stopped_by is not None:
            entry["reason"] = f"stopped after {stopped_by} failed"
            continue
        if step.id in done:
            entry.update({k: v for k, v in done[step.id].items() if k != "id"})
            entry["reason"] = "ok in the earlier run today"
            continue
        if not step.enabled:
            entry.update(status="skipped", reason=step.enabled_reason or "off in config")
            continue
        if step.trading_days_only and not timeutil.is_trading_day(started, holidays):
            entry.update(status="skipped", reason="not a trading day")
            continue
        wait = (_at(started, step.at_et) - now_fn()).total_seconds()
        if wait > 0:
            entry.update(status="waiting", reason=f"until {step.at_et} ET")
            _write(name, doc)
            sleep(wait)
        begin = now_fn()
        entry.update(status="running", started_at=begin.isoformat())
        entry.pop("reason", None)
        _write(name, doc)
        clock = time.monotonic()
        log = step_log_path(name, step.id)
        code = spawn(step, log)
        entry["seconds"] = round(time.monotonic() - clock, 1)
        entry["exit_code"] = code
        if code == 0:
            entry["status"] = "ok"
        else:
            entry["status"] = "failed"
            entry["stderr_tail"] = tail_of(log)
            if doc["failed_step"] is None:
                doc["failed_step"] = step.id
            if not step.independent:
                stopped_by = step.id
        _write(name, doc)
    failed = [s for s in doc["steps"] if s["status"] == "failed"]
    doc["result"] = "failed" if failed else "ok"
    doc["finished_at"] = now_fn().isoformat()
    doc["seconds"] = round(time.monotonic() - t0, 1)
    _write(name, doc)
    return doc


def report_lines(doc: dict[str, Any]) -> list[str]:
    """What the process says on stderr when anything failed: the registry keeps the last lines of it as
    the job's `last_error`, so the LAST line names the step that stopped the run (or the last that
    failed), each line leading with the pipeline's name."""
    name = doc.get("pipeline")
    failed = [s for s in doc.get("steps") or [] if s.get("status") == "failed"]
    # Independent failures first, the one that stopped the run (if any) last.
    failed.sort(key=lambda s: not s.get("independent"))
    lines = []
    not_run = [s["id"] for s in doc.get("steps") or [] if s.get("status") == "not_run"]
    if not_run:
        lines.append(f"pipeline {name}: not run: {', '.join(not_run)}")
    for s in failed:
        last = str(s.get("stderr_tail") or "").strip().splitlines()
        said = f": {last[-1][:160]}" if last else ""
        lines.append(f"pipeline {name}: step {s['id']} failed (exit {s.get('exit_code')}){said}")
    return lines


def summary(name: str) -> dict[str, Any] | None:
    """The compact result for the job's registry row: the result, the step that failed, and each
    step's status, exit code and seconds (no stderr: that is in the state file and `last_error`)."""
    doc = load_state(name)
    if not doc:
        return None
    return {
        "result": doc.get("result"),
        "run_key": doc.get("run_key"),
        "failed_step": doc.get("failed_step"),
        "seconds": doc.get("seconds"),
        "steps": [
            {k: s.get(k) for k in ("id", "status", "exit_code", "seconds", "reason") if s.get(k) is not None}
            for s in doc.get("steps") or []
        ],
    }


def failure_text(name: str) -> str | None:
    """ "step <id> (exit N: last stderr line)" for the step that stopped the last run, else the first
    that failed; None when the last run had no failed step. For the watchdog's finding."""
    doc = load_state(name)
    failed = [s for s in doc.get("steps") or [] if s.get("status") == "failed"]
    if not failed:
        return None
    s = next((x for x in failed if not x.get("independent")), failed[0])
    last = str(s.get("stderr_tail") or "").strip().splitlines()
    said = f": {last[-1][:100]}" if last else ""
    return f"step {s['id']} (exit {s.get('exit_code')}{said})"


def main(name: str, cfg: dict[str, Any] | None = None, fresh: bool = False) -> int:
    doc = run(name, cfg, fresh=fresh)
    print(json.dumps({k: doc.get(k) for k in ("pipeline", "result", "failed_step", "seconds")}))
    if doc.get("error"):
        print(f"pipeline {name}: {doc['error']}", file=sys.stderr)
        return 1
    for line in report_lines(doc):
        print(line, file=sys.stderr)
    return 0 if doc["result"] == "ok" else 1
