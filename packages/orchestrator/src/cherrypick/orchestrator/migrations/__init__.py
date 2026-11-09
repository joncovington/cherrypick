"""Data migrations that `run.py upgrade` runs after a release's code and packages are in place.

**The rule for future releases: prefer a compatibility shim or a `doctor` warning to a migration.**
A reader that accepts both the old and the new shape of a file, a config key that keeps resolving
under its old spelling, or a `doctor` line that says what to change by hand: each leaves the data
where it is, so a rollback to the previous release still reads it. A migration rewrites data in
place and so closes that door; it earns its place only when the old shape cannot be read any more
(a ledger column that must exist, a state file whose meaning changed). Ledgers already migrate
forward when a newer release opens them, which is why a rollback restores the pre-upgrade backup
rather than running old code on new data.

**What a migration is.** One `Migration` in `MIGRATIONS`, below, appended at the end:

- `id` is permanent and unique: `<release>-<nn>-<what>` (for example `v0.13.0-01-meic-ledger-column`).
  It is what `state/migrations.json` records and what the release manifest lists.
- `apply(home, dry_run)` does the work and returns one line saying what it did, or, with `dry_run`
  true, what it would do while changing nothing. It must be **idempotent**: run on data that is
  already migrated (a fresh install, or a second run after a crash) it finds nothing to do.
- It touches only the per-user home it is handed, never the network or the broker.

**How they run.** The runner (`run_pending`) takes the registry in order, skips the ids already in
`state/migrations.json`, and runs the rest, recording each as applied the moment it succeeds, so a
failure on the third leaves the first two recorded and the third pending. The registry holds every
migration a release has ever had, which is what makes skipping releases work: a machine upgrading
from v0.12 straight to v0.14 has v0.13's migrations pending as well and runs them first.

`run.py migrations` lists applied and pending ones; `--dry-run` previews and `--apply` runs them.
`run.py upgrade` calls both against the TARGET release's code.

v0.12.0 ships with an empty registry.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core.jsonio import write_json_atomic

from .. import config as cfgmod
from ..util import read_json

FILE = "migrations.json"
_ID = re.compile(r"^v\d+\.\d+\.\d+-\d{2}-[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    apply: Callable[[Path, bool], str]


# Every migration a release has ever shipped, oldest first. Append; never reorder or remove.
MIGRATIONS: tuple[Migration, ...] = ()


class MigrationError(Exception):
    """A migration failed, or the registry itself is malformed."""


def validate(registry: Sequence[Migration]) -> None:
    """Ids well formed and unique. Raises MigrationError."""
    seen: set[str] = set()
    for m in registry:
        if not _ID.match(m.id):
            raise MigrationError(f"migration id {m.id!r} is not v<release>-<nn>-<name>")
        if m.id in seen:
            raise MigrationError(f"migration id {m.id!r} appears twice")
        seen.add(m.id)


def state_path() -> Path:
    return cfgmod.STATE_DIR / FILE


def applied_ids() -> list[str]:
    rec = read_json(state_path(), default=None)
    rows = rec.get("applied") if isinstance(rec, dict) else None
    return [str(r["id"]) for r in rows or [] if isinstance(r, dict) and r.get("id")]


def pending(registry: Sequence[Migration] | None = None) -> list[Migration]:
    reg = MIGRATIONS if registry is None else registry
    done = set(applied_ids())
    return [m for m in reg if m.id not in done]


def _record(migration_id: str, detail: str) -> None:
    rec = read_json(state_path(), default=None)
    rows = list(rec.get("applied") or []) if isinstance(rec, dict) else []
    rows.append({"id": migration_id, "at": datetime.now(timezone.utc).isoformat(), "detail": detail})
    write_json_atomic(state_path(), {"applied": rows})


def run_pending(
    *, dry_run: bool = False, registry: Sequence[Migration] | None = None, home: Path | None = None
) -> dict[str, Any]:
    """Run (or, with `dry_run`, preview) every pending migration in registry order.

    Stops at the first failure and says which: the ones before it stay recorded, it and the rest stay
    pending. A dry run records nothing."""
    reg = MIGRATIONS if registry is None else registry
    validate(reg)
    root = home or _home.home()
    results: list[dict[str, Any]] = []
    for m in pending(reg):
        try:
            detail = m.apply(root, dry_run)
        except Exception as exc:  # noqa: BLE001 -- any failure stops the run and is reported by id
            results.append({"id": m.id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return {"ok": False, "dry_run": dry_run, "results": results, "failed": m.id}
        results.append({"id": m.id, "ok": True, "detail": detail})
        if not dry_run:
            _record(m.id, detail)
    return {"ok": True, "dry_run": dry_run, "results": results, "failed": None}


def listing(registry: Sequence[Migration] | None = None) -> dict[str, Any]:
    reg = MIGRATIONS if registry is None else registry
    return {
        "ok": True,
        "applied": applied_ids(),
        "pending": [{"id": m.id, "description": m.description} for m in pending(reg)],
    }
