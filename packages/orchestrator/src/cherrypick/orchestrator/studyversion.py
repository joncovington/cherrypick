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

**The code it ran on** (#1082). The stamp also records `depends`: a hash of each repo file the
script rests on, found by reading it, never by importing it: the script itself; any study script
it loads by name (a string naming a sibling `.py`, as `_load("iwm_condor_item59")` or
`HERE / "x_item59.py"` do), followed through those; and each `cherrypick` module it imports
directly. Direct imports only: following core's own imports would mark every result stale on any
core change. `state` then marks a result stale when one of those files differs now, and names it
(`changed_files`), so a fix to shared code shows on every result it touched without a re-run.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
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


#: A string a study script uses to load a sibling script by name (`iwm_condor_item59`, `x_item59.py`).
SCRIPT_NAME = re.compile(r"^[A-Za-z0-9_]{1,80}(\.py)?$")


def _module_file(module: str, root: Path) -> Path | None:
    """The repo file a `cherrypick.*` module lives in (a package's src layout, or core's)."""
    parts = module.split(".")
    if parts[0] != "cherrypick" or len(parts) < 2:
        return None
    rel = Path(*parts)
    bases = [root / "packages" / "core"] + sorted((root / "packages").glob("*/src"))
    for base in bases:
        for cand in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
            if cand.is_file():
                return cand
    return None


def dependencies(script: Path, root: Path) -> list[str]:
    """The repo files `script` rests on, repo-relative and sorted: itself, the study scripts it
    loads by name (followed through), and the `cherrypick` modules it imports directly."""
    root = Path(root).resolve()
    seen: set[Path] = set()
    todo = [Path(script).resolve()]
    while todo:
        p = todo.pop()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        if root not in p.parents:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        is_script = p.parent.name == "scripts"
        for node in ast.walk(tree):
            found: list[Path] = []
            if isinstance(node, ast.Import):
                found = [f for f in (_module_file(a.name, root) for a in node.names) if f]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                # `from cherrypick.core import bs` rests on bs.py, not on the package's __init__:
                # the package file counts only when no imported name is a submodule of it.
                found = [f for f in (_module_file(f"{node.module}.{a.name}", root) for a in node.names) if f]
                if not found:
                    f = _module_file(node.module, root)
                    found = [f] if f else []
            for f in found:
                seen.add(f.resolve())  # direct imports: recorded, not followed
            if is_script and isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Only a string shaped like a script's name: a docstring is not a path, and asking the
                # filesystem about one fails outright on Linux (a name too long).
                if not SCRIPT_NAME.match(node.value):
                    continue
                name = node.value if node.value.endswith(".py") else f"{node.value}.py"
                sib = p.parent / name
                try:
                    found = sib.is_file()
                except OSError:
                    found = False
                if found and sib.resolve() != p:
                    todo.append(sib.resolve())
    return sorted(q.relative_to(root).as_posix() for q in seen if root in q.parents)


def file_hashes(paths: list[str], root: Path) -> dict[str, str | None]:
    """A sha256 of each repo-relative file's bytes now, None for one that is gone."""
    out: dict[str, str | None] = {}
    for rel in paths:
        try:
            out[rel] = hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
        except OSError:
            out[rel] = None
    return out


def stamp(output: Path, script: Path, root: Path, *, now: str, sha: str | None = None) -> dict:
    """Write the stamp beside `output` and return it."""
    log = corrections(script)
    deps = dependencies(script, root)
    doc = {
        "script": Path(script).resolve().relative_to(Path(root).resolve()).as_posix(),
        "version": len(log),
        "commit": sha if sha is not None else commit(root),
        "stamped_at": now,
        "corrections": log,
        "depends": file_hashes(deps, root),
    }
    stamp_path(output).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def read_stamp(output: Path) -> dict | None:
    try:
        doc = json.loads(stamp_path(output).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("version"), int) else None


def state(stamped: dict | None, now_log: list[dict] | None, root: Path | None = None) -> dict | None:
    """A result's version against its script's log now. None when neither is known. `since` lists
    the corrections the result predates, so a stale reading says what changed. Given the repo
    `root`, a stamp's `depends` is compared with the files now: `changed_files` names each that
    differs (or is gone), and any makes the result stale too."""
    if stamped is None and now_log is None:
        return None
    version = stamped["version"] if stamped else None
    current = len(now_log) if now_log is not None else None
    since = (now_log or [])[version or 0 :]
    depends = (stamped or {}).get("depends") or {}
    changed = []
    if root is not None and depends:
        now = file_hashes(sorted(depends), root)
        changed = sorted(p for p, h in depends.items() if now.get(p) != h)
    return {
        "version": version,
        "current": current,
        "commit": (stamped or {}).get("commit"),
        "script": (stamped or {}).get("script"),
        "stale": (current is not None and current > (version or 0)) or bool(changed),
        "since": [{k: c[k] for k in ("on", "what", "before_read")} for c in since],
        "changed_files": changed,
    }
