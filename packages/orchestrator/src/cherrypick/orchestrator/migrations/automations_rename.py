"""`v0.14.0-01-automations-rename`: the automations module's data under its own name.

The module was `ensemble` until v0.13.0, and v0.13.0's code read its old file, folder, config block and
import name through shims. v0.14.0 drops the shims, so a home set up before the rename is moved here, once,
with the suite stopped (`run.py upgrade` runs it after its backup; a rollback restores that backup):

- `config/ensemble.json` becomes `config/automations.json` and `data/ensemble/` becomes `data/automations/`,
  each only when the new one does not exist yet (where both exist, the new one was the one in use, and the
  old one is left where it is and named in the result).
- In the automations ledger(s) in that folder, every `ensemble_` table becomes `automations_` and its
  indexes are rebuilt under the new names. A new-named table that already exists and is empty (code that
  ran before this migration made it) is dropped first; one that holds rows stops the migration, since two
  sets of rows cannot be merged by a rule.
- In `config.json`, `modules.ensemble` becomes `modules.automations`, with the old default spellings
  (`../ensemble`, `cherrypick.ensemble.*`, `data/ensemble/`, `ensemble_paper.log`) written anew, and the
  trade schema `ensemble_trade` becomes `automations_trade`. A config holding both blocks stops the
  migration: delete the old one by hand.
- In `config/automations.json`, the group `ensemble` becomes `default`, as does each automation's
  `"group": "ensemble"`. A file that already has a `default` group stops the migration.
- In the research register (`data/research/hypotheses.json`), each `ensemble_trade` becomes
  `automations_trade`.
- The stale `state/stream_requests/ensemble.json` and `state/ensemble.heartbeat` are removed. Old logs stay.

Config files are edited as text, one exact replacement at a time, and each result is checked to parse to
exactly the document the rename intends; anything else stops the migration before it writes. Every write
is atomic. Run twice, the second run finds nothing to do.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from cherrypick.core.jsonio import replace_with_retry

ID = "v0.14.0-01-automations-rename"
DESCRIPTION = (
    "The automations module's data under its own name: config/ensemble.json and data/ensemble/ moved, "
    "the ledger's ensemble_ tables renamed, modules.ensemble and ensemble_trade in config.json, the "
    "ensemble group in automations.json (now default), and ensemble_trade in the research register."
)

OLD, NEW = "ensemble", "automations"
OLD_SCHEMA, NEW_SCHEMA = "ensemble_trade", "automations_trade"
OLD_GROUP, NEW_GROUP = "ensemble", "default"


class RenameError(Exception):
    """The home is in a shape this migration will not guess about."""


# ---------------------------------------------------------------- files and folders


def _move(old: Path, new: Path, dry_run: bool, done: list[str], label: str) -> None:
    if not old.exists():
        return
    if new.exists():
        done.append(f"{label}: kept {old.name} beside {new.name} (the new one was in use)")
        return
    if not dry_run:
        shutil.move(str(old), str(new))
    done.append(f"{label}: {old.name} -> {new.name}")


# ---------------------------------------------------------------- the ledger


def _old_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'ensemble\\_%' ESCAPE '\\'"
        )
    ]


def _rename_ledger(db: Path, dry_run: bool) -> str | None:
    """Rename one ledger's `ensemble_` tables and indexes. None when it has none."""
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        tables = _old_tables(conn)
        if not tables:
            return None
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        drop_new: list[str] = []
        for old in tables:
            new = NEW + old[len(OLD) :]
            if new in existing:
                rows = conn.execute(f'SELECT COUNT(*) FROM "{new}"').fetchone()[0]
                if rows:
                    raise RenameError(
                        f"{db.name}: both {old} and {new} hold rows; merge them by hand before upgrading"
                    )
                drop_new.append(new)
        indexes = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL "
            "AND tbl_name LIKE 'ensemble\\_%' ESCAPE '\\'"
        ).fetchall()
        if dry_run:
            return f"{db.name}: {len(tables)} tables and {len(indexes)} indexes to rename"
        conn.execute("BEGIN IMMEDIATE")
        try:
            for new in drop_new:
                conn.execute(f'DROP TABLE "{new}"')
            for name, _sql in indexes:
                conn.execute(f'DROP INDEX "{name}"')
            for old in tables:
                conn.execute(f'ALTER TABLE "{old}" RENAME TO "{NEW + old[len(OLD) :]}"')
            for _name, sql in indexes:
                # The index's own name (`idx_ensemble_...`) and its table's: the module's columns hold no
                # `ensemble_`, so a plain replacement is exact.
                conn.execute(sql.replace("ensemble_", "automations_"))
            if _old_tables(conn):
                raise RenameError(f"{db.name}: an ensemble_ table is left after the rename")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return f"{db.name}: {len(tables)} tables and {len(indexes)} indexes renamed" + (
            f" ({len(drop_new)} empty new tables dropped first)" if drop_new else ""
        )
    finally:
        conn.close()


# ---------------------------------------------------------------- config text


def _replace_once(text: str, pattern: str, repl: str, label: str) -> str:
    out, n = re.subn(pattern, repl, text)
    if n != 1:
        raise RenameError(f"{label}: expected one match for {pattern!r}, found {n}; edit it by hand")
    return out


def _respell(value: Any) -> Any:
    """The old block's default spellings written anew (what v0.13.0's shim did on every read)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "path" and isinstance(v, str) and Path(v).name == OLD:
                v = str(Path(v).with_name(NEW).as_posix())
            elif k == "log" and v == f"{OLD}_paper.log":
                v = f"{NEW}_paper.log"
            elif k == "trade_schema" and v == OLD_SCHEMA:
                v = NEW_SCHEMA
            elif k == "paper_db" and isinstance(v, str):
                v = v.replace(f"/{OLD}/", f"/{NEW}/").replace(f"\\{OLD}\\", f"\\{NEW}\\")
            out[k] = _respell(v)
        return out
    if isinstance(value, list):
        return [_respell(v) for v in value]
    if isinstance(value, str) and value.startswith(f"cherrypick.{OLD}."):
        return f"cherrypick.{NEW}." + value[len(f"cherrypick.{OLD}.") :]
    return value


def _schema_renamed(value: Any) -> Any:
    """`value` with every `trade_schema: ensemble_trade` written anew, nothing else touched."""
    if isinstance(value, dict):
        return {
            k: (NEW_SCHEMA if k == "trade_schema" and v == OLD_SCHEMA else _schema_renamed(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_schema_renamed(v) for v in value]
    return value


def _suite_config(text: str) -> tuple[str, list[str]]:
    """config.json renamed: the block's key and spellings, and the schema id. Returns (text, what changed).

    The intended document is built from the parsed one; the text is edited by exact replacements; the
    two must agree, or nothing is written."""
    doc = json.loads(text)
    modules = doc.get("modules") if isinstance(doc, dict) else None
    if not isinstance(modules, dict):
        return text, []
    changed: list[str] = []
    want = copy.deepcopy(doc)
    if OLD in modules:
        if NEW in modules:
            raise RenameError(
                "config.json: modules holds both ensemble and automations; delete the ensemble block by hand"
            )
        want["modules"] = {
            (NEW if k == OLD else k): (_respell(v) if k == OLD else v) for k, v in modules.items()
        }
        text = _replace_once(text, r'"ensemble"(\s*):', r'"automations"\1:', "config.json")
        text = re.sub(r'("path"\s*:\s*"(?:[^"]*[/\\])?)ensemble"', r'\1automations"', text, count=1)
        text = text.replace('"cherrypick.ensemble.', '"cherrypick.automations.')
        text = text.replace('"ensemble_paper.log"', '"automations_paper.log"')
        for sep in ("/", "\\\\"):
            text = text.replace(f"{sep}ensemble{sep}paper_trades.db", f"{sep}automations{sep}paper_trades.db")
        changed.append("modules.ensemble -> modules.automations")
    want = _schema_renamed(want)
    if re.search(r'"trade_schema"\s*:\s*"ensemble_trade"', text):
        text = re.sub(r'("trade_schema"\s*:\s*)"ensemble_trade"', r'\1"automations_trade"', text)
        changed.append("trade_schema ensemble_trade -> automations_trade")
    if not changed:
        return text, []
    if json.loads(text) != want:
        raise RenameError("config.json: the edit would change more than the rename; nothing was written")
    return text, changed


def _module_config(text: str) -> tuple[str, list[str]]:
    """automations.json renamed: the `ensemble` group, and each automation's `"group": "ensemble"`."""
    doc = json.loads(text)
    if not isinstance(doc, dict):
        return text, []
    groups = doc.get("groups")
    named = [
        n
        for n, a in (doc.get("automations") or doc.get("arms") or {}).items()
        if isinstance(a, dict) and a.get("group") == OLD_GROUP
    ]
    has_group = isinstance(groups, dict) and OLD_GROUP in groups
    if not has_group and not named and f'"{OLD_SCHEMA}"' not in text:
        return text, []
    if isinstance(groups, dict) and NEW_GROUP in groups and has_group:
        raise RenameError("automations.json: it has both an ensemble and a default group; rename one by hand")
    want = copy.deepcopy(doc)
    changed: list[str] = []
    if has_group:
        want["groups"] = {(NEW_GROUP if k == OLD_GROUP else k): v for k, v in groups.items()}
        text = _replace_once(text, r'"ensemble"(\s*):', r'"default"\1:', "automations.json")
        changed.append("group ensemble -> default")
    for key in ("automations", "arms"):
        block = want.get(key)
        if isinstance(block, dict):
            for n in named:
                if isinstance(block.get(n), dict) and block[n].get("group") == OLD_GROUP:
                    block[n]["group"] = NEW_GROUP
    if named:
        text, n = re.subn(r'("group"\s*:\s*)"ensemble"', r'\1"default"', text)
        if n != len(named):
            raise RenameError(f"automations.json: {len(named)} automations name the group, {n} matched")
        changed.append(f"{len(named)} automations' group -> default")
    if f'"{OLD_SCHEMA}"' in text:
        text = text.replace(f'"{OLD_SCHEMA}"', f'"{NEW_SCHEMA}"')
        want = json.loads(json.dumps(want).replace(f'"{OLD_SCHEMA}"', f'"{NEW_SCHEMA}"'))
        changed.append("ensemble_trade -> automations_trade")
    if json.loads(text) != want:
        raise RenameError("automations.json: the edit would change more than the rename; nothing was written")
    return text, changed


def _register(text: str) -> tuple[str, list[str]]:
    if f'"{OLD_SCHEMA}"' not in text:
        return text, []
    n = text.count(f'"{OLD_SCHEMA}"')
    out = text.replace(f'"{OLD_SCHEMA}"', f'"{NEW_SCHEMA}"')
    want = json.loads(json.dumps(json.loads(text)).replace(f'"{OLD_SCHEMA}"', f'"{NEW_SCHEMA}"'))
    if json.loads(out) != want:
        raise RenameError("hypotheses.json: the edit would change more than the schema id")
    return out, [f"{n} ensemble_trade -> automations_trade"]


def _edit_file(path: Path, fn, dry_run: bool, done: list[str], label: str) -> None:
    if not path.is_file():
        return
    with path.open(encoding="utf-8", newline="") as f:  # keep the file's own line endings
        text = f.read()
    new, changed = fn(text)
    if not changed:
        return
    if not dry_run:
        _write_atomic(path, new)
    done.append(f"{label}: " + ", ".join(changed))


def _write_atomic(path: Path, text: str) -> None:
    """`text` to `path` byte for byte (no newline translation, so a CRLF file stays CRLF), through a
    temporary file beside it and `replace_with_retry`."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    replace_with_retry(tmp, path)


def _where(new: Path, old: Path) -> Path:
    """Where a moved file or folder is now: the new place, or (a dry run, which moves nothing) the old one
    while only it exists."""
    return old if not new.exists() and old.exists() else new


# ---------------------------------------------------------------- the step


def apply(home: Path, dry_run: bool) -> str:
    home = Path(home)
    done: list[str] = []
    _move(home / "config" / f"{OLD}.json", home / "config" / f"{NEW}.json", dry_run, done, "config")
    _move(home / "data" / OLD, home / "data" / NEW, dry_run, done, "data")
    data = _where(home / "data" / NEW, home / "data" / OLD)
    if data.is_dir():
        for db in sorted(data.glob("*.db")):
            line = _rename_ledger(db, dry_run)
            if line:
                done.append(f"ledger {line}")
    _edit_file(home / "config.json", _suite_config, dry_run, done, "config.json")
    module_cfg = _where(home / "config" / f"{NEW}.json", home / "config" / f"{OLD}.json")
    _edit_file(module_cfg, _module_config, dry_run, done, "automations.json")
    _edit_file(home / "data" / "research" / "hypotheses.json", _register, dry_run, done, "register")
    for stale in (home / "state" / "stream_requests" / f"{OLD}.json", home / "state" / f"{OLD}.heartbeat"):
        if stale.exists():
            if not dry_run:
                stale.unlink()
            done.append(f"removed {stale.relative_to(home).as_posix()}")
    if not done:
        return "nothing to rename"
    return ("would: " if dry_run else "") + "; ".join(done)
