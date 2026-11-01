"""Reminders: a dated note to the user, pushed through the suite's notifier until a condition holds.

Some follow-ups are weeks away and depend on the suite's own state: "after curve's spreads expire on
11-20, switch the module off". A session reminder dies with the session, and a research-queue row
runs research, not a nudge. Here a reminder is a ROW (`state/reminders.db`), added with
`run.py remind-add`, and the supervisor's `reminders` job looks at the due rows every few minutes.

**A row's life** (`status`):
- `pending` until its `due_at` (ET);
- then its `done_when` conditions are read. All met: `done`, with no push if nothing was ever sent
  (the work was done before the reminder fell due), and one "done" push if it had been;
- otherwise one push, and the row is `open`; it pushes again every `renotify_hours` while any
  condition is unmet, and turns `done` the first tick they all hold;
- a row with no `done_when` is `done` once its one push lands. `remind-done` closes any row by hand
  (with a note), `remind-cancel` drops it.
A push that reached no channel (`notifier.delivered`) changes nothing, so the next tick tries again.

**What a condition may read is an allow-list, never a command line** (`KINDS`), as the research
queue's: the supervisor is a service with broker credentials in reach. `checks` take the same kinds
and are reported in the push without deciding anything.
- `module-disabled` `{module}`: the module's `enabled` is false in config.json (or it is absent).
- `module-status` `{module, field, equals}`: the field of the module's declared paper `status_argv`
  JSON equals the value, read by subprocess as the watchdog's settlement check reads it. An
  unreadable status is unknown, never met.

Deterministic, no AI. This module reads config and the modules' own status commands, writes only its
own table, and acts on nothing: a reminder tells a person, it never changes config itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

from . import config as cfgmod
from .util import CREATE_NO_WINDOW, first_json

ET = ZoneInfo("America/New_York")
DB_NAME = "reminders.db"
STATUSES = ("pending", "open", "done", "cancelled")
LIVE = ("pending", "open")
DUE_FORMAT = "%Y-%m-%dT%H:%M"
TITLE_MAX = 120
MESSAGE_MAX = 1500  # leaves room in a 2000-character Discord message for the condition lines
DEFAULT_RENOTIFY_HOURS = 24.0
NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
KEY = "reminders"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,
    done_when TEXT NOT NULL DEFAULT '[]',
    checks TEXT NOT NULL DEFAULT '[]',
    renotify_hours REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    notified_at TEXT,
    notify_count INTEGER NOT NULL DEFAULT 0,
    done_at TEXT,
    note TEXT
);
"""


class Refused(ValueError):
    """A reminder the allow-list will not take, with why."""


# ------------------------------------------------------------------------------------------------
# Conditions: the allow-list.


def _module(cond: dict) -> str:
    m = cond.get("module")
    if not isinstance(m, str) or not NAME.match(m):
        raise Refused(f"{cond.get('kind')}: 'module' must be a module name, got {m!r}")
    return m


def _check_module_disabled(cond: dict) -> None:
    _module(cond)


def _check_module_status(cond: dict) -> None:
    _module(cond)
    if not isinstance(cond.get("field"), str) or not NAME.match(cond["field"]):
        raise Refused(f"module-status: 'field' must be a status field name, got {cond.get('field')!r}")
    if "equals" not in cond or not isinstance(cond["equals"], (bool, int, float, str, type(None))):
        raise Refused("module-status: 'equals' must be a plain value (number, text, true/false, null)")


KINDS: dict[str, Callable[[dict], None]] = {
    "module-disabled": _check_module_disabled,
    "module-status": _check_module_status,
}


def validate(conds: Any, label: str) -> list[dict]:
    if not isinstance(conds, list):
        raise Refused(f"{label} must be a JSON list of conditions")
    out = []
    for c in conds:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            raise Refused(f"{label}: unknown kind {c.get('kind') if isinstance(c, dict) else c!r}; "
                          f"allowed: {', '.join(KINDS)}")  # fmt: skip
        KINDS[c["kind"]](c)
        out.append(c)
    return out


def module_status(cfg: dict, module: str, timeout: int = 20) -> dict | None:
    """The module's paper `--status` JSON, or None when it has none or it cannot be read."""
    mcfg = (cfg.get("modules") or {}).get(module) or {}
    argv = (mcfg.get("paper") or {}).get("status_argv")
    if not argv:
        return None
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), *[str(a) for a in argv]],
            cwd=str(cfgmod.module_root(mcfg, module)),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (first_json(r.stdout) or None) if r.returncode == 0 else None


def evaluate(cond: dict, cfg: dict, status: Callable[[dict, str], dict | None] = module_status):
    """(met: True / False / None for unknown, a line saying what was read)."""
    kind, module = cond["kind"], cond["module"]
    if kind == "module-disabled":
        mcfg = (cfg.get("modules") or {}).get(module)
        on = isinstance(mcfg, dict) and bool(mcfg.get("enabled", False))
        return (not on), f"{module} is {'enabled' if on else 'disabled'} in config (wanted disabled)"
    st = status(cfg, module)
    field, want = cond["field"], cond["equals"]
    if not st or field not in st:
        return None, f"{module} status {field}: could not read it (wanted {want!r})"
    return st[field] == want, f"{module} status {field} = {st[field]!r} (wanted {want!r})"


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


def _stamp(now: datetime) -> str:
    return now.astimezone(ET).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_due(due: str) -> datetime:
    try:
        return datetime.strptime(due, DUE_FORMAT).replace(tzinfo=ET)
    except (TypeError, ValueError) as exc:
        raise Refused(f"due must be ET YYYY-MM-DDTHH:MM, got {due!r}") from exc


def add(
    title: str,
    message: str,
    due: str,
    *,
    done_when: list | None = None,
    checks: list | None = None,
    renotify_hours: float | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict:
    title, message = (title or "").strip(), (message or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise Refused(f"a title is required, at most {TITLE_MAX} characters")
    if not message or len(message) > MESSAGE_MAX:
        raise Refused(f"a message is required, at most {MESSAGE_MAX} characters")
    _parse_due(due)
    dw = validate(done_when or [], "done_when")
    ck = validate(checks or [], "checks")
    hours = DEFAULT_RENOTIFY_HOURS if renotify_hours is None else float(renotify_hours)
    if hours <= 0:
        raise Refused("renotify_hours must be above zero")
    now = now or datetime.now(ET)
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO reminders (title, message, due_at, done_when, checks, renotify_hours, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, message, due, json.dumps(dw), json.dumps(ck), hours, _stamp(now)),
        )
        return {"id": cur.lastrowid, "title": title, "due_at": due, "status": "pending"}


def rows(path: Path | None = None, include_closed: bool = True) -> list[dict]:
    with connect(path) as conn:
        sql = "SELECT * FROM reminders"
        if not include_closed:
            sql += " WHERE status IN ('pending', 'open')"
        out = [dict(r) for r in conn.execute(sql + " ORDER BY due_at, id")]
    for r in out:
        r["done_when"] = json.loads(r["done_when"])
        r["checks"] = json.loads(r["checks"])
    return out


def _close(reminder_id: int, status: str, note: str | None, now: datetime | None, path: Path | None) -> dict:
    now = now or datetime.now(ET)
    with connect(path) as conn:
        row = conn.execute("SELECT status FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": f"no reminder {reminder_id}"}
        if row["status"] not in LIVE:
            return {"ok": False, "error": f"reminder {reminder_id} is already {row['status']}"}
        conn.execute(
            "UPDATE reminders SET status = ?, done_at = ?, note = ? WHERE id = ?",
            (status, _stamp(now), note, reminder_id),
        )
    return {"ok": True, "id": reminder_id, "status": status}


def mark_done(reminder_id: int, note: str | None = None, *, now=None, path=None) -> dict:
    return _close(reminder_id, "done", note or "marked done by hand", now, path)


def cancel(reminder_id: int, *, now=None, path=None) -> dict:
    return _close(reminder_id, "cancelled", "cancelled", now, path)


# ------------------------------------------------------------------------------------------------
# The supervisor's tick.


def settings(cfg: dict) -> dict:
    return cfgmod.reminders_settings(cfg)


def _lines(results: list[tuple[bool | None, str]]) -> list[str]:
    mark = {True: "met", False: "not yet", None: "unknown"}
    return [f"- [{mark[m]}] {line}" for m, line in results]


def message_for(row: dict, done: list, checks: list) -> str:
    parts = [row["message"]]
    if done:
        parts += ["", "Done when:", *_lines(done)]
    if checks:
        parts += ["", "Checks:", *_lines(checks)]
    parts += ["", f"Close it by hand: run.py remind-done --reminder-id {row['id']}"]
    return "\n".join(parts)


def due_for_push(row: dict, now: datetime) -> bool:
    if row["status"] == "pending" or not row["notified_at"]:
        return True
    last = datetime.fromisoformat(row["notified_at"]).replace(tzinfo=ET)
    return now - last >= timedelta(hours=float(row["renotify_hours"]))


def run(
    cfg: dict,
    *,
    now: datetime | None = None,
    notifier=None,
    status: Callable[[dict, str], dict | None] = module_status,
    path: Path | None = None,
) -> dict:
    """One tick: every live row past its due time is read, closed, or pushed."""
    from cherrypick.notify import notifier as _notifier

    now = now or datetime.now(ET)
    if notifier is None:
        notifier = _notifier.Notifier({**(cfg.get("notify") or {}), "channels": settings(cfg)["channels"]})
    acted = []
    for row in rows(path, include_closed=False):
        if now < _parse_due(row["due_at"]):
            continue
        done = [evaluate(c, cfg, status) for c in row["done_when"]]
        checks = [evaluate(c, cfg, status) for c in row["checks"]]
        title = f"Reminder: {row['title']}"
        if done and all(m is True for m, _ in done):
            note = "; ".join(line for _, line in done)
            if row["notify_count"]:
                notifier.notify("INFO", f"{KEY}.{row['id']}", f"Done: {row['title']}", note)
            else:
                notifier.record("INFO", f"{KEY}.{row['id']}", f"Done before due: {row['title']}", note)
            _set(path, row["id"], status="done", done_at=_stamp(now), note=note)
            acted.append({"id": row["id"], "action": "done"})
            continue
        if not due_for_push(row, now):
            continue
        res = notifier.notify("INFO", f"{KEY}.{row['id']}", title, message_for(row, done, checks))
        if not _notifier.delivered(res):
            acted.append({"id": row["id"], "action": "push failed; retries next tick"})
            continue
        fields = {"notified_at": _stamp(now), "notify_count": row["notify_count"] + 1}
        if done:
            fields["status"] = "open"
        else:  # a plain reminder: one push and it is done
            fields |= {"status": "done", "done_at": _stamp(now), "note": "delivered"}
        _set(path, row["id"], **fields)
        acted.append({"id": row["id"], "action": "pushed", "status": fields["status"]})
    return {"ok": True, "acted": acted}


def _set(path: Path | None, reminder_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect(path) as conn:
        conn.execute(f"UPDATE reminders SET {cols} WHERE id = ?", (*fields.values(), reminder_id))
