"""The research queue: long research tasks run by the supervisor, one at a time, from rows in a table.

A study's fetch can take nine hours and wants the vendor account's one session; run by hand from a
terminal or an assistant's session, it dies with that session and collides with the nightly
`backtest-fetch`. Here a task is a ROW (`state/research_queue.db`), added with `run.py queue-add`, and
the supervisor's `research-queue` job takes the oldest ready row every few minutes and runs it.

**What a row may run is an allow-list, never a command line** (`KINDS`). The supervisor is a Windows
service with the broker credentials in reach, so a row that could name any command would be
unreviewed code run by it. A kind builds its own argv from the row's arguments:
- `fetch-chains` / `fetch-chain-paths` / `fetch-flow`: the backtest package's resumable ThetaData
  fetches, given a request or plan file. Each is bounded by `--stop-by`, and a fetch that stops is
  queued again to resume.
- `script`: a Python file checked into the repo's `scripts/` (git must know it), with string
  arguments, no shell. It may not need the ThetaData session: it cannot be stopped mid-run.

**When a task may start** (`ready`):
- one task at a time; a `running` row whose process is gone (a reboot, a killed supervisor) is put
  back to `queued`, and a fetch resumes where it stopped;
- a ThetaData task waits while anything holds the session lock (`state/thetadata-session.lock`, the
  backtest package's), and never starts inside `BLACKOUT` ET on a trading day, the nightly
  `backtest-fetch`'s window; its `--stop-by` is the next blackout start, or the row's own stop-by if
  sooner;
- `not_before` holds a row back until then;
- `after` makes a row wait for the newest row of another name to finish `done`. One whose
  prerequisite failed, stopped or was cancelled is HELD, as is one whose `after` chain loops back on
  itself (a cycle, which would otherwise wait forever); a held row raises one notification, once.
  Re-queuing the prerequisite releases it: the new row is the newest by that name.

**Names** (2026-10-24): a row is `study/step`, both lowercase slugs checked on the way in. The study
is its category (`item17`, `flow-tags`); the step is what it does there (`plan`, `fetch-chains`,
`study`), defaulting to the kind. There is one live row per name, so the same fetch is never queued
twice, and never a free-text label.

**Outcomes** (`status`): `done`, `failed` (a non-zero exit), `stopped` (its own stop-by passed), or
back to `queued` (a fetch stopped by a blackout, to resume after it). A finished row's `result`
points to what it produced (`result_of`): a fetch's counts and the request or plan file it worked
through; a script's declared `output`, which must exist (exit 0 without it is `failed`); always its
log, `logs/research-queue/<id>.log`. Each finish and each failure is one notification.

Deterministic, no AI. This module reads and writes only its own table and log files, and drives the
fetches and scripts by subprocess -- it imports no other package.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core import progress as _progress

from .util import CREATE_NO_WINDOW, pid_alive

ET = ZoneInfo("America/New_York")
DB_NAME = "research_queue.db"
# The nightly backtest-fetch (18:45 ET, trading days) and its run: no ThetaData task starts inside it.
BLACKOUT = ("18:30", "20:30")
THETADATA = "thetadata"
SESSION_LOCK = "thetadata-session.lock"  # the backtest package's (vendor/thetadata.py SESSION_LOCK)
STATUSES = ("queued", "running", "done", "failed", "stopped", "cancelled")

# A row's name is `study/step`, both slugs: the study is its category (item17, flow-tags), the step
# what it does there (plan, fetch-chains, study). One live row per name; a free-text label never.
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LIVE = ("queued", "running")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study TEXT NOT NULL,
    step TEXT NOT NULL,
    kind TEXT NOT NULL,
    args TEXT NOT NULL,
    output TEXT,
    result TEXT,
    needs TEXT NOT NULL DEFAULT '',
    not_before TEXT,
    stop_by TEXT,
    after TEXT,
    held_at TEXT,
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


def backtest_stores() -> dict | None:
    """Where the backtest declares its vendor stores (`read stores`: {dir, files}), or None when it
    cannot say (not installed, or the read failed)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "cherrypick.backtest", "read", "stores"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        out = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return out if isinstance(out, dict) and out.get("ok") else None


def check_store_output(output: str, stores: dict | None) -> None:
    """A declared output named like a declared vendor store must BE that store: the runner judges a
    finished row by whether its output exists, so a store's name in the wrong directory turns a
    complete fetch into a failure and holds every row after it (item 19, 2026-10-26: `vendor/` for
    `vendor/thetadata/`). Unchecked when the backtest cannot say where its stores live."""
    if stores is None:
        return
    p = Path(output).expanduser()
    if p.name not in stores.get("files", []):
        return
    where = Path(stores["dir"]) / p.name
    if p.resolve() != where.resolve():
        raise Refused(f"output {output!r} names the vendor store {p.name}, which lives at {where}")


# kind -> (what it needs, whether --stop-by bounds it)
KINDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "fetch-chains": ((THETADATA,), True),
    # Each expiry's daily path (backtest `fetch-chain-paths`, 2026-10-28): the same store and session.
    "fetch-chain-paths": ((THETADATA,), True),
    "fetch-flow": ((THETADATA,), True),
    "script": ((), False),
    # A module's replay validated against the recording (backtest `validate`): local stores only, no
    # vendor session. Its report is what unlocks that module's history runs (2026-10-25).
    "validate": ((), False),
}

#: The modules `validate` knows (backtest `validate.CHECKS`), checked here so a typo never queues.
VALIDATE_MODULES = ("flies", "meic")


def build_argv(
    kind: str,
    args: dict,
    python: str,
    stop_by: str | None = None,
    root: Path | None = None,
    tracked: Callable[[Path, Path], bool] | None = None,
) -> list[str]:
    """The task's command, built by its kind from its arguments; `Refused` for anything else."""
    if kind in ("fetch-chains", "fetch-chain-paths"):
        argv = [python, "-m", "cherrypick.backtest", kind]
        argv += ["--requests", _file_arg(args, "requests"), "--workers", _workers(args, 3)]
    elif kind == "fetch-flow":
        argv = [python, "-m", "cherrypick.backtest", "fetch-flow"]
        argv += ["--plan", _file_arg(args, "plan"), "--workers", _workers(args, 3)]
    elif kind == "script":
        return [python, *_tracked_script(args, root or repo_root(), tracked or git_tracked)]
    elif kind == "validate":
        module = args.get("module")
        if module not in VALIDATE_MODULES:
            raise Refused(f"validate needs 'module', one of {', '.join(VALIDATE_MODULES)}")
        argv = [python, "-m", "cherrypick.backtest", "validate", "--module", module]
        # Which store is validated: each cadence is its own file, and only the cadence a module's
        # history runs at unlocks them. Absent, backtest's default store (15 s), which unlocks nothing
        # at 60 s (2026-10-26: a MEIC validation compared the 15 s store's 4 sessions for that reason).
        cadence = args.get("cadence")
        if cadence is not None:
            if type(cadence) is not int or not 1 <= cadence <= 3600:
                raise Refused("validate's 'cadence' is whole seconds, 1 to 3600")
            argv += ["--cadence", str(cadence)]
        # MEIC's one delta for both replays (backtest validate --delta-source, 2026-10-27).
        delta_source = args.get("delta_source")
        if delta_source is not None:
            if module != "meic" or delta_source not in ("stream", "computed"):
                raise Refused("validate's 'delta_source' is MEIC's: 'stream' or 'computed'")
            argv += ["--delta-source", delta_source]
        return argv
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


HELD = "held:"


def after_reason(row: dict, after_status: str | None) -> str | None:
    """Why a row's `after` prerequisite keeps it back, or None. One whose prerequisite failed,
    stopped, was cancelled or does not exist is HELD, never run on what the prerequisite left behind
    -- a study run once on a partial store would spend its declared test -- and so is one in a cycle
    of `after` rows, which no finish could ever release. Re-queuing the prerequisite releases a held
    row by itself: the new row is the newest by that name, and its status is the one read."""
    after = row.get("after")
    if not after or after_status == "done":
        return None
    if after_status == "cycle":
        return f"{HELD} the --after chain from {after} comes back on itself (a cycle); cancel a row in it"
    if after_status in ("queued", "running"):
        return f"waits for {after} ({after_status})"
    return f"{HELD} {after} is {after_status or 'missing'}; re-queue it and this row runs once it is done"


def ready(
    row: dict,
    now: datetime,
    is_trading_day: Callable[[date], bool],
    session_held: bool,
    window: tuple[str, str] = BLACKOUT,
    after_status: str | None = None,
) -> str | None:
    """None when the row may start now, else why not (a short reason the list shows). A row with an
    `after` prerequisite waits until that row is `done` (`after_status`, from `prereq_status`)."""
    why = after_reason(row, after_status)
    if why:
        return why
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
    _migrate(conn)
    return conn


# Columns added after the table first shipped, each added to an older queue on connect: `after`
# (2026-10-25), a row that waits for another row's success; `held_at`, when a held row was notified.
ADDED_COLUMNS = (("after", "TEXT"), ("held_at", "TEXT"))


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any missing column. Two processes (the supervisor's job and a `queue-list`) can both see
    it missing and both add it; the second's duplicate-column error means it is there, so it passes."""
    have = _columns(conn)
    for col, typ in ADDED_COLUMNS:
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _newest(conn, full_name: str):
    study, _, step = full_name.partition("/")
    return conn.execute(
        "SELECT id, status, after FROM tasks WHERE study = ? AND step = ? ORDER BY id DESC LIMIT 1",
        (study, step),
    ).fetchone()


def prereq_status(conn, after: str | None, row_id: int | None = None) -> str | None:
    """The status of the newest row named `after` (study/step): None when a row has no prerequisite,
    "missing" when none by that name exists. Given the dependent row's id, "cycle" when the chain of
    queued prerequisites -- each the newest row by its `after` name -- comes back to a row already on
    it: `s/study --after s/study`, or A after B and B after A, would otherwise wait forever."""
    if not after:
        return None
    r = _newest(conn, after)
    if r is None:
        return "missing"
    if row_id is not None:
        seen = {row_id}
        cur = r
        while cur is not None and cur["status"] == "queued":
            if cur["id"] in seen:
                return "cycle"
            seen.add(cur["id"])
            cur = _newest(conn, cur["after"]) if cur["after"] else None
    return r["status"]


def _now_iso() -> str:
    return datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S")


def name(row: dict) -> str:
    """A row's one display name: `study/step`."""
    return f"{row['study']}/{row['step']}"


def add(
    study: str,
    kind: str,
    args: dict,
    *,
    step: str | None = None,
    output: str | None = None,
    not_before: str | None = None,
    stop_by: str | None = None,
    after: str | None = None,
    path: Path | None = None,
    root: Path | None = None,
    tracked: Callable[[Path, Path], bool] | None = None,
    stores: Callable[[], dict | None] | None = None,
) -> dict:
    """Queue one task, named `study/step` (step defaults to the kind). Everything is checked now, so
    a row the runner would refuse is never added: the kind and arguments (`build_argv`), both names
    as slugs, the times, a script's declared `output` (where its conclusion will be; a vendor store's
    name only at that store's own path, `check_store_output`), and that no other live row has the
    same name."""
    step = step or kind
    for what, v in (("study", study), ("step", step)):
        if not isinstance(v, str) or not SLUG.match(v):
            raise Refused(
                f"{what} {v!r} must be a lowercase slug (letters, digits, single dashes), e.g. item17"
            )
    build_argv(kind, args, sys.executable, root=root, tracked=tracked)
    for v in (not_before, stop_by):
        if v is not None:
            datetime.fromisoformat(v)  # ValueError on a malformed time
    if output is not None and not output.strip():
        output = None
    if output is not None:
        check_store_output(output, (stores or backtest_stores)())
    needs = ",".join(KINDS[kind][0])
    with connect(path) as conn:
        live = conn.execute(
            "SELECT id, status FROM tasks WHERE study = ? AND step = ? AND status IN (?, ?)",
            (study, step, *LIVE),
        ).fetchone()
        if live is not None:
            raise Refused(f"{study}/{step} is already {live['status']} as task {live['id']}")
        if after is not None:
            if after == f"{study}/{step}":
                raise Refused(f"--after {after!r} is this row's own name: a row cannot wait for itself")
            if prereq_status(conn, after) in (None, "missing"):
                raise Refused(f"--after {after!r} names no row (study/step)")
            # This row becomes the newest by its name, so a queued prerequisite chain that comes back
            # to the name would wait on it forever.
            cur, seen = _newest(conn, after), set()
            while cur is not None and cur["status"] == "queued" and cur["after"] and cur["id"] not in seen:
                if cur["after"] == f"{study}/{step}":
                    raise Refused(f"--after {after!r} waits, through --after, on {study}/{step}: a cycle")
                seen.add(cur["id"])
                cur = _newest(conn, cur["after"])
        cur = conn.execute(
            "INSERT INTO tasks (study, step, kind, args, output, needs, not_before, stop_by, after,"
            " created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (study, step, kind, json.dumps(args), output, needs, not_before, stop_by, after, _now_iso()),
        )
        return {
            "id": cur.lastrowid,
            "name": f"{study}/{step}",
            "kind": kind,
            "needs": needs or None,
            "status": "queued",
        }


def result_of(row: dict, report: dict, log: Path) -> dict:
    """What a finished row points to: where its conclusion or fetched data is. A fetch: its report's
    counts and the request or plan file it worked through (the store is the backtest package's). A
    script: its declared output, and whether it exists now. Always the log."""
    args = json.loads(row["args"]) if isinstance(row.get("args"), str) else (row.get("args") or {})
    out: dict[str, Any] = {"log": str(log)}
    keys = ("asked", "already", "todo", "complete", "empty", "refused", "rows", "seconds", "stopped")
    counts = {k: report[k] for k in keys if k in report}
    if counts:
        out["report"] = counts
    if row["kind"] in ("fetch-chains", "fetch-chain-paths", "fetch-flow"):
        out["input"] = args.get("requests") or args.get("plan")
        out["store"] = "backtest " + ("flow store" if row["kind"] == "fetch-flow" else "eod_chains.db")
    if row.get("output"):
        out["output"] = row["output"]
        out["output_exists"] = Path(row["output"]).expanduser().exists()
    return out


def progress_file(task_id: int) -> Path:
    """Where queued task `task_id` reports its progress (`core.progress`)."""
    return _progress.progress_dir() / f"q{task_id}.json"


def rows(path: Path | None = None, *, study: str | None = None, limit: int = 200) -> list[dict]:
    """The rows, newest first; one study's only with `study`."""
    with connect(path) as conn:
        if study:
            out = conn.execute(
                "SELECT * FROM tasks WHERE study = ? ORDER BY id DESC LIMIT ?", (study, limit)
            ).fetchall()
        else:
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


def mark_held(conn: sqlite3.Connection) -> list[tuple[dict, str]]:
    """Stamp `held_at` on each queued row that is newly held by its prerequisite, and clear it on one
    no longer held; the newly held rows with why. Every queued row is read, not only those ahead of
    the one that runs, so a hold is noticed while another task runs."""
    out = []
    for r in conn.execute(
        "SELECT id, study, step, after, held_at FROM tasks WHERE status = 'queued' AND after IS NOT NULL"
    ).fetchall():
        row = dict(r)
        why = after_reason(row, prereq_status(conn, row["after"], row["id"]))
        if why and why.startswith(HELD):
            if not row["held_at"]:
                conn.execute("UPDATE tasks SET held_at = ? WHERE id = ?", (_now_iso(), row["id"]))
                out.append((row, why))
        elif row["held_at"]:
            conn.execute("UPDATE tasks SET held_at = NULL WHERE id = ?", (row["id"],))
    return out


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
            newly_held = mark_held(conn)
        # Each newly held row is one notification, sent after its mark is committed: never repeated
        # every pass, and a row released (its prerequisite re-queued) and held again notifies again.
        for row, why in newly_held:
            notify("WARNING", f"Research task held: {name(row)}", why.removeprefix(HELD).strip())
        with conn:
            if conn.execute("SELECT 1 FROM tasks WHERE status = 'running'").fetchone():
                return {"ok": True, "ran": None, "why": "a task is running", "recovered": recovered}
            held = session_held()
            waiting = {}
            pick = None
            for r in conn.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY id").fetchall():
                row = dict(r)
                after_status = prereq_status(conn, row.get("after"), row["id"])
                why = ready(row, now(), is_trading_day, held, window, after_status)
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
                notify("WARNING", f"Research task refused: {name(pick)}", str(exc))
                return {"ok": False, "ran": pick["id"], "refused": str(exc)}
            log = log_dir() / f"{pick['id']}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            fh = log.open("a", encoding="utf-8")
            fh.write(f"\n=== run {pick['runs'] + 1} at {_now_iso()} ET: {' '.join(argv)}\n")
            fh.flush()
            # The task's progress file (core.progress): the job writes it, its children report beside
            # it, and queue-list reads it -- no counting subprocess per row.
            env = dict(os.environ, **{_progress.ENV: str(progress_file(pick["id"]))})
            proc = spawn(
                argv,
                stdout=subprocess.PIPE,
                stderr=fh,
                cwd=str(repo_root()),
                text=True,
                creationflags=CREATE_NO_WINDOW,
                env=env,
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
        result = result_of(pick, report, log)
        note = "stopped for the backtest-fetch window; resumes after it" if status == "queued" else None
        # A script that exits 0 without writing the conclusion it declared has not concluded.
        if status == "done" and result.get("output_exists") is False:
            status, note = "failed", f"exited 0 but its declared output {pick['output']} was not written"
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, pid = NULL, exit_code = ?, note = ?,"
                " result = ? WHERE id = ?",
                (
                    status,
                    _now_iso() if status != "queued" else None,
                    proc.returncode,
                    note,
                    json.dumps(result),
                    pick["id"],
                ),
            )
        title = name(pick)
        if status == "failed":
            notify("WARNING", f"Research task failed: {title}", note or f"exit {proc.returncode}; log {log}")
        elif status in ("done", "stopped"):
            where = result.get("output") or result.get("input") or str(log)
            notify("INFO", f"Research task {status}: {title}", f"{_summary(report) or 'finished'}; {where}")
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
