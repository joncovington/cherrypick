"""The research queue: long research tasks run by the supervisor, one at a time, from rows in a table.

A study's fetch can take nine hours and wants the vendor account's one session; run by hand from a
terminal or an assistant's session, it dies with that session and collides with the nightly
`backtest-fetch`. Here a task is a ROW (`state/research_queue.db`), added with `run.py queue-add`, and
the supervisor's `research-queue` job takes the oldest ready row every few minutes and runs it.

**What a row may run is an allow-list, never a command line** (`KINDS`). The supervisor is a Windows
service with the broker credentials in reach, so a row that could name any command would be
unreviewed code run by it. A kind builds its own argv from the row's arguments:
- `fetch-chains` / `fetch-flow`: the backtest package's resumable ThetaData fetches, given a request
  or plan file. Each is bounded by `--stop-by`, and a fetch that stops is queued again to resume.
- `script`: a Python file checked into the repo's `scripts/` (git must know it), with string
  arguments, no shell. It may not need the ThetaData session: it cannot be stopped mid-run.

**When a task may start** (`ready`):
- one task at a time; a `running` row whose process is gone (a reboot, a killed supervisor) is put
  back to `queued`, and a fetch resumes where it stopped;
- a ThetaData task waits while anything holds the session lock (`state/thetadata-session.lock`, the
  backtest package's), and never starts inside `BLACKOUT` ET on a trading day, the nightly
  `backtest-fetch`'s window; its `--stop-by` is the next blackout start, or the row's own stop-by if
  sooner;
- `not_before` holds a row back until then.

**Outcomes** (`status`): `done`, `failed` (a non-zero exit), `stopped` (its own stop-by passed), or
back to `queued` (a fetch stopped by a blackout, to resume after it). Each finish and each failure is
one notification. The task's output goes to `logs/research-queue/<id>.log`.

Deterministic, no AI. This module reads and writes only its own table and log files, and drives the
fetches and scripts by subprocess -- it imports no other package.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

from .util import CREATE_NO_WINDOW, pid_alive

ET = ZoneInfo("America/New_York")
DB_NAME = "research_queue.db"
# The nightly backtest-fetch (18:45 ET, trading days) and its run: no ThetaData task starts inside it.
BLACKOUT = ("18:30", "20:30")
THETADATA = "thetadata"
SESSION_LOCK = "thetadata-session.lock"  # the backtest package's (vendor/thetadata.py SESSION_LOCK)
STATUSES = ("queued", "running", "done", "failed", "stopped", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    args TEXT NOT NULL,
    label TEXT,
    needs TEXT NOT NULL DEFAULT '',
    not_before TEXT,
    stop_by TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    runs INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    exit_code INTEGER,
    note TEXT
);
"""


# ------------------------------------------------------------------------------------------------
# Kinds: the allow-list.


class Refused(ValueError):
    """A row the allow-list will not run, with why."""


def repo_root() -> Path:
    """The monorepo checkout this module runs from: the first parent holding `scripts/` and `packages/`."""
    for p in Path(__file__).resolve().parents:
        if (p / "scripts").is_dir() and (p / "packages").is_dir():
            return p
    raise Refused("no repo checkout found above the orchestrator package")


def _file_arg(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v:
        raise Refused(f"needs {key!r}, a file path")
    p = Path(v).expanduser()
    if not p.is_file():
        raise Refused(f"{key} {v!r} is not a file")
    return str(p)


def _workers(args: dict, default: int, most: int = 4) -> str:
    n = args.get("workers", default)
    if not isinstance(n, int) or not 1 <= n <= most:
        raise Refused(f"workers must be 1-{most}")
    return str(n)


def _tracked_script(args: dict, root: Path, tracked: Callable[[Path, Path], bool]) -> list[str]:
    rel = args.get("path")
    if not isinstance(rel, str) or not rel.endswith(".py"):
        raise Refused("a script needs 'path', a .py file under scripts/")
    p = (root / rel).resolve()
    scripts = (root / "scripts").resolve()
    if scripts not in p.parents or not p.is_file():
        raise Refused(f"{rel!r} is not a file under scripts/")
    if not tracked(root, p):
        raise Refused(f"{rel!r} is not checked into the repo: commit it first")
    extra = args.get("args", [])
    if not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
        raise Refused("a script's 'args' must be a list of strings")
    return [str(p), *extra]


def git_tracked(root: Path, path: Path) -> bool:
    """Whether git knows `path` in the checkout at `root` (committed or staged, not merely present)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# kind -> (what it needs, whether --stop-by bounds it)
KINDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "fetch-chains": ((THETADATA,), True),
    "fetch-flow": ((THETADATA,), True),
    "script": ((), False),
}


def build_argv(
    kind: str,
    args: dict,
    python: str,
    stop_by: str | None = None,
    root: Path | None = None,
    tracked: Callable[[Path, Path], bool] = git_tracked,
) -> list[str]:
    """The task's command, built by its kind from its arguments; `Refused` for anything else."""
    if kind == "fetch-chains":
        argv = [python, "-m", "cherrypick.backtest", "fetch-chains"]
        argv += ["--requests", _file_arg(args, "requests"), "--workers", _workers(args, 3)]
    elif kind == "fetch-flow":
        argv = [python, "-m", "cherrypick.backtest", "fetch-flow"]
        argv += ["--plan", _file_arg(args, "plan"), "--workers", _workers(args, 3)]
    elif kind == "script":
        return [python, *_tracked_script(args, root or repo_root(), tracked)]
    else:
        raise Refused(f"unknown kind {kind!r}; allowed: {', '.join(KINDS)}")
    if stop_by:
        argv += ["--stop-by", stop_by]
    return argv


# ------------------------------------------------------------------------------------------------
# Pure: when a task may run.


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def blackout(cfg: dict[str, Any] | None) -> tuple[str, str]:
    """The ThetaData blackout ("HH:MM", "HH:MM" ET) around `backtest.fetch_at`, by the queue's
    settings: 18:30-20:30 at the defaults."""
    from . import config as cfgmod

    at = cfgmod.backtest_settings(cfg or {})["fetch_at"]
    q = cfgmod.research_queue_settings(cfg or {})
    h, m = _hm(at)
    base = datetime(2000, 1, 1, h, m)

    def fmt(d: datetime) -> str:
        return d.strftime("%H:%M")

    return (
        fmt(base - timedelta(minutes=q["blackout_before_minutes"])),
        fmt(base + timedelta(minutes=q["blackout_after_minutes"])),
    )


def in_blackout(
    now: datetime, is_trading_day: Callable[[date], bool], window: tuple[str, str] = BLACKOUT
) -> bool:
    """Inside the nightly backtest-fetch window on a trading day (ET)."""
    t = now.astimezone(ET)
    lo, hi = (t.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in map(_hm, window))
    return is_trading_day(t.date()) and lo <= t < hi


def next_blackout(
    now: datetime, is_trading_day: Callable[[date], bool], window: tuple[str, str] = BLACKOUT
) -> datetime:
    """The next blackout start (ET) at or after `now`."""
    t = now.astimezone(ET)
    h, m = _hm(window[0])
    for k in range(15):
        d = t.date() + timedelta(days=k)
        start = datetime(d.year, d.month, d.day, h, m, tzinfo=ET)
        if start >= t and is_trading_day(d):
            return start
    return t + timedelta(days=14)


def stop_by_for(
    row: dict, now: datetime, is_trading_day: Callable[[date], bool], window: tuple[str, str] = BLACKOUT
) -> str | None:
    """The `--stop-by` (ET, YYYY-MM-DDTHH:MM) a bounded task runs under: the next blackout for a
    ThetaData task, or the row's own stop-by if sooner; None for a task nothing bounds."""
    needs = set(filter(None, (row.get("needs") or "").split(",")))
    if not KINDS.get(row["kind"], ((), False))[1]:
        return None
    bounds = []
    if THETADATA in needs:
        bounds.append(next_blackout(now, is_trading_day, window))
    if row.get("stop_by"):
        bounds.append(datetime.fromisoformat(row["stop_by"]).replace(tzinfo=ET))
    return min(bounds).strftime("%Y-%m-%dT%H:%M") if bounds else None


def ready(
    row: dict,
    now: datetime,
    is_trading_day: Callable[[date], bool],
    session_held: bool,
    window: tuple[str, str] = BLACKOUT,
) -> str | None:
    """None when the row may start now, else why not (a short reason the list shows)."""
    t = now.astimezone(ET)
    if row.get("not_before") and t < datetime.fromisoformat(row["not_before"]).replace(tzinfo=ET):
        return f"held until {row['not_before']} ET"
    if row.get("stop_by") and t >= datetime.fromisoformat(row["stop_by"]).replace(tzinfo=ET):
        return "its stop-by has passed"
    if THETADATA in (row.get("needs") or "").split(","):
        if in_blackout(now, is_trading_day, window):
            return f"inside the {window[0]}-{window[1]} ET backtest-fetch window"
        if session_held:
            return "the ThetaData session is held by another process"
    return None


def last_json(text: str) -> dict:
    """The report a backtest verb printed last: the JSON object starting on the last line that opens
    with `{` (nested keys are indented, so only the report's own brace is at column 0), or {}."""
    idx = text.rfind("\n{")
    if idx >= 0:
        start = idx + 1
    elif text.startswith("{"):
        start = 0
    else:
        return {}
    try:
        out = json.loads(text[start:])
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def outcome(row: dict, exit_code: int, report: dict, now: datetime) -> str:
    """The row's status after a run. A fetch that stopped goes back to the queue to resume, unless
    the row's own stop-by is what stopped it (within a minute: `--stop-by` has minute precision)."""
    if exit_code != 0:
        return "failed"
    if report.get("stopped"):
        own = row.get("stop_by")
        if own and now.astimezone(ET) >= datetime.fromisoformat(own).replace(tzinfo=ET) - timedelta(
            minutes=1
        ):
            return "stopped"
        return "queued"
    return "done"


# ------------------------------------------------------------------------------------------------
# The table.


def db_path() -> Path:
    return _home.state_dir() / DB_NAME


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S")


def add(
    kind: str,
    args: dict,
    *,
    label: str | None = None,
    not_before: str | None = None,
    stop_by: str | None = None,
    path: Path | None = None,
    root: Path | None = None,
    tracked: Callable[[Path, Path], bool] = git_tracked,
) -> dict:
    """Queue one task. Its kind and arguments are checked now (`build_argv`), so a row the runner
    would refuse is never added."""
    build_argv(kind, args, sys.executable, root=root, tracked=tracked)
    for v in (not_before, stop_by):
        if v is not None:
            datetime.fromisoformat(v)  # ValueError on a malformed time
    needs = ",".join(KINDS[kind][0])
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (kind, args, label, needs, not_before, stop_by, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (kind, json.dumps(args), label, needs, not_before, stop_by, _now_iso()),
        )
        return {"id": cur.lastrowid, "kind": kind, "needs": needs or None, "status": "queued"}


def rows(path: Path | None = None, *, limit: int = 100) -> list[dict]:
    with connect(path) as conn:
        out = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in out]


def cancel(task_id: int, path: Path | None = None) -> dict:
    """Cancel a queued row. A running one is left alone: stop its process with `run.py stop`."""
    with connect(path) as conn:
        r = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if r is None:
            return {"ok": False, "error": f"no task {task_id}"}
        if r["status"] != "queued":
            return {"ok": False, "error": f"task {task_id} is {r['status']}; only a queued task is cancelled"}
        conn.execute(
            "UPDATE tasks SET status = 'cancelled', finished_at = ? WHERE id = ?", (_now_iso(), task_id)
        )
    return {"ok": True, "id": task_id, "status": "cancelled"}


def session_held(state_dir: Path | None = None) -> bool:
    """Whether a live process holds the ThetaData session lock (a dead holder's lock is no hold)."""
    lock = (state_dir or _home.state_dir()) / SESSION_LOCK
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return pid_alive(pid)


def recover(conn: sqlite3.Connection) -> list[int]:
    """Rows left `running` by a process that is gone go back to the queue; their ids."""
    back = []
    for r in conn.execute("SELECT id, pid FROM tasks WHERE status = 'running'").fetchall():
        if not (r["pid"] and pid_alive(r["pid"])):
            conn.execute(
                "UPDATE tasks SET status = 'queued', pid = NULL, note = ? WHERE id = ?",
                ("its process ended without a result (a restart?); queued again", r["id"]),
            )
            back.append(r["id"])
    return back


def log_dir() -> Path:
    return _home.home() / "logs" / "research-queue"


# ------------------------------------------------------------------------------------------------
# The run: one task per call, as the supervisor's job.


def run_next(
    cfg: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(ET),
    is_trading_day: Callable[[date], bool] | None = None,
    spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
    notify: Callable[[str, str, str], Any] | None = None,
) -> dict:
    """Recover orphaned rows, then run the oldest ready row to its end. Returns what it did."""
    if is_trading_day is None:
        from cherrypick.core import calendar as cal

        is_trading_day = cal.is_trading_day
    if notify is None:
        from cherrypick.notify.notifier import Notifier

        notifier = Notifier((cfg or {}).get("notify"))
        notify = lambda level, title, msg: notifier.notify(level, "research-queue", title, msg)  # noqa: E731
    window = blackout(cfg)
    conn = connect(path)
    try:
        with conn:
            recovered = recover(conn)
            if conn.execute("SELECT 1 FROM tasks WHERE status = 'running'").fetchone():
                return {"ok": True, "ran": None, "why": "a task is running", "recovered": recovered}
            held = session_held()
            waiting = {}
            pick = None
            for r in conn.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY id").fetchall():
                row = dict(r)
                why = ready(row, now(), is_trading_day, held, window)
                if why is None:
                    pick = row
                    break
                waiting[row["id"]] = why
            if pick is None:
                return {"ok": True, "ran": None, "waiting": waiting, "recovered": recovered}
            stop_by = stop_by_for(pick, now(), is_trading_day, window)
            try:
                argv = build_argv(pick["kind"], json.loads(pick["args"]), sys.executable, stop_by)
            except Refused as exc:
                conn.execute(
                    "UPDATE tasks SET status = 'failed', finished_at = ?, note = ? WHERE id = ?",
                    (_now_iso(), f"refused: {exc}", pick["id"]),
                )
                notify("WARNING", f"Research task {pick['id']} refused", str(exc))
                return {"ok": False, "ran": pick["id"], "refused": str(exc)}
            log = log_dir() / f"{pick['id']}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            fh = log.open("a", encoding="utf-8")
            fh.write(f"\n=== run {pick['runs'] + 1} at {_now_iso()} ET: {' '.join(argv)}\n")
            fh.flush()
            proc = spawn(
                argv,
                stdout=subprocess.PIPE,
                stderr=fh,
                cwd=str(repo_root()),
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            conn.execute(
                "UPDATE tasks SET status = 'running', started_at = ?, runs = runs + 1, pid = ?, note = NULL"
                " WHERE id = ?",
                (_now_iso(), proc.pid, pick["id"]),
            )
        out, _ = proc.communicate()
        fh.write(out or "")
        fh.close()
        report = last_json(out or "")
        status = outcome(pick, proc.returncode, report, now())
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, pid = NULL, exit_code = ?, note = ?"
                " WHERE id = ?",
                (
                    status,
                    _now_iso() if status != "queued" else None,
                    proc.returncode,
                    "stopped for the backtest-fetch window; resumes after it" if status == "queued" else None,
                    pick["id"],
                ),
            )
        name = pick["label"] or f"{pick['kind']} #{pick['id']}"
        if status == "failed":
            notify("WARNING", f"Research task failed: {name}", f"exit {proc.returncode}; log {log}")
        elif status in ("done", "stopped"):
            notify("INFO", f"Research task {status}: {name}", _summary(report) or f"log {log}")
        return {
            "ok": status != "failed",
            "ran": pick["id"],
            "status": status,
            "exit_code": proc.returncode,
            "recovered": recovered,
        }
    finally:
        conn.close()


def _summary(report: dict) -> str:
    keys = ("complete", "empty", "refused", "rows", "seconds", "stopped")
    return ", ".join(f"{k} {report[k]}" for k in keys if k in report)
