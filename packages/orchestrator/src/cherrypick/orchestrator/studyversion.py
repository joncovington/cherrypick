"""Which version of a research script's replay produced a result, and whether a correction has
landed since.

A research script (`scripts/*_itemNN.py`) keeps its corrections in a module-level `REPLAY_CORRECTIONS`, a
literal tuple of dicts, oldest first:

    REPLAY_CORRECTIONS = (
        {"on": "2026-11-01", "what": "...", "why": "...", "decided": "user", "before_read": True},
    )

`on` is the date it landed, `what` the change, `why` the reading that prompted it, `decided` who
made the call, `before_read` whether it came before any arm or outcome was read. The script's
version is the number of entries; a script with none is version 0. The log is read by parsing the
file, never by importing it.

When the research queue finishes a script row whose declared output exists, it writes a stamp
beside the output (`results.json` -> `results.version.json`): the script, its version, the commit
it ran from and the corrections as they stood. The studies board compares a result's stamp with the
script's log now. A result is stale when the log has grown since it was written, and a result
written before stamping began counts as version 0.
"""

from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime
from pathlib import Path

from .util import CREATE_NO_WINDOW

FIELDS = ("on", "what", "why", "decided", "before_read")


class BadLog(ValueError):
    """A `REPLAY_CORRECTIONS` that is not a literal tuple of complete entries."""


def corrections(script: Path) -> list[dict]:
    """The script's correction log, oldest first; empty when the file has none or is missing."""
    try:
        tree = ast.parse(Path(script).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "REPLAY_CORRECTIONS" for t in node.targets
        ):
            try:
                log = ast.literal_eval(node.value)
            except ValueError as exc:
                raise BadLog(f"{Path(script).name}: REPLAY_CORRECTIONS must be a literal") from exc
            return check(log, Path(script).name)
    return []


def check(log, name: str = "REPLAY_CORRECTIONS") -> list[dict]:
    """Each entry carries every field, `before_read` a bool, and the dates run oldest first."""
    if not isinstance(log, (tuple, list)):
        raise BadLog(f"{name}: REPLAY_CORRECTIONS must be a tuple of entries")
    out = []
    for i, e in enumerate(log):
        if not isinstance(e, dict) or set(e) != set(FIELDS):
            raise BadLog(f"{name}: correction {i + 1} needs exactly {', '.join(FIELDS)}")
        if not isinstance(e["before_read"], bool):
            raise BadLog(f"{name}: correction {i + 1}'s before_read must be true or false")
        try:
            datetime.strptime(e["on"], "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise BadLog(f"{name}: correction {i + 1}'s date is not YYYY-MM-DD") from exc
        if out and e["on"] < out[-1]["on"]:
            raise BadLog(f"{name}: correction {i + 1} is dated before the one above it")
        out.append(dict(e))
    return out


def stamp_path(output: Path) -> Path:
    """Where a result's stamp lives: beside it, `<stem>.version.json`."""
    output = Path(output)
    return output.with_name(f"{output.stem}.version.json")


def commit(root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def stamp(output: Path, script: Path, root: Path, *, now: str, sha: str | None = None) -> dict:
    """Write the stamp beside `output` and return it."""
    log = corrections(script)
    doc = {
        "script": Path(script).resolve().relative_to(Path(root).resolve()).as_posix(),
        "version": len(log),
        "commit": sha if sha is not None else commit(root),
        "stamped_at": now,
        "corrections": log,
    }
    stamp_path(output).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def read_stamp(output: Path) -> dict | None:
    try:
        doc = json.loads(stamp_path(output).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("version"), int) else None


def state(stamped: dict | None, now_log: list[dict] | None) -> dict | None:
    """A result's version against its script's log now. None when neither is known. `since` lists
    the corrections the result predates, so a stale reading says what changed."""
    if stamped is None and now_log is None:
        return None
    version = stamped["version"] if stamped else None
    current = len(now_log) if now_log is not None else None
    since = (now_log or [])[version or 0 :]
    return {
        "version": version,
        "current": current,
        "commit": (stamped or {}).get("commit"),
        "script": (stamped or {}).get("script"),
        "stale": current is not None and current > (version or 0),
        "since": [{k: c[k] for k in ("on", "what", "before_read")} for c in since],
    }
