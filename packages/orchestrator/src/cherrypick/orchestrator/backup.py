"""Nightly backup of the suite's own data: ONE verified zip, the latest, rewritten every night.

**What is in it.** Everything under the per-user home that the suite cannot get back from somewhere
else: the configs, `state/` (arming, halt flag, advice artifacts, stream requests), every ledger and
recorded history under `data/` (the paper and live trade books, GEX history, the advisor's store,
the technicals store) and every JSON/CSV/Markdown artifact beside them.

**What is not, and why.** The Dolt stores (`.dolt/` under `data/earnings`, ~14 GB) are copies of
public DoltHub databases and are re-pulled by `refresh_dolt_data.py`; the stream caches are rebuilt by
the streamer on its next session; the vendor collector's browser profile holds a login and is a
secret, not data; `logs/` has its own monthly archive; and backups, archives and retired folders are
already copies. `data/earnings` is BOTH a Dolt server root and the earnings module's home, so only the
`.dolt/`/`.doltcfg/` directories themselves are skipped -- skipping a directory that merely contains
one dropped the earnings trade ledgers from the first draft of this.

**How a live ledger is copied.** Through SQLite's online backup API, never a file copy: a paper loop
can be writing when this runs, and a byte copy of a WAL database mid-write is not a database. Each
copy is then `PRAGMA quick_check`ed, and its result recorded in the zip's `MANIFEST.json` with every
file's size and sha256.

**Only the latest is kept** (the user's choice, 2026-09-29): `cherrypick-backup.zip`, replaced each
night. The new zip is written beside it as `.partial` and swapped in only once complete, so there is
never a moment with no backup. A night with a problem (a copy that errored, a copy that failed
quick_check) does NOT replace the last good one: it lands as `cherrypick-backup.failed.zip` and the
run reports failure, because overwriting the only good copy with a damaged one is the one outcome a
single-copy backup cannot recover from.

**Restore never touches the live tree.** `restore` extracts the backup into a directory you name,
which must not be inside the cherrypick home; putting a file back is a deliberate copy by a person,
with the suite stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import config as cfgmod

LATEST = "cherrypick-backup.zip"
FAILED = "cherrypick-backup.failed.zip"
MANIFEST = "MANIFEST.json"

# Directory names skipped wherever they appear, and top-level home entries never walked.
_SKIP_DIR_NAMES = {"__pycache__", "browser-profile", "node_modules"}
_SKIP_TOP = {"archive", "logs", "backups", "modules", "worktrees"}
# Dolt keeps a database's whole store inside `.dolt/` (and server state in `.doltcfg/`); only those
# are skipped, never the directory holding them (see the module docstring).
_DOLT_DIRS = {".dolt", ".doltcfg"}
# A file whose name says it is already a copy, a scratch file, a SQLite sidecar (the database
# itself is copied through the backup API, which reads the WAL), or a running process's PID file --
# runtime state that is meaningless once restored (it would name a dead process) and that comes and
# goes as daemons stop: one vanishing mid-run failed the 2026-10-08 night.
_SKIP_FILE_RE = re.compile(
    r"(\.bak|\.pre-|\.pre_|pre-prune|advisor-bak|pre-reset|\.retired|\.tmp$|\.partial$|\.lock$|\.pid$"
    r"|-wal$|-shm$|-journal$)",
    re.I,
)
# Rebuilt by the streamer on its next session; never the only copy of anything.
_REGENERABLE_RE = re.compile(r"^stream_cache.*\.db$", re.I)


def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """The resolved `backup` block (`config.backup_settings`, where the job reads it too)."""
    return cfgmod.backup_settings(cfg)


def latest_path(cfg: dict[str, Any]) -> Path:
    return settings(cfg)["dest"] / LATEST


def collect(root: Path) -> tuple[list[Path], dict[str, int]]:
    """Every file to back up under `root`, and a count of what was skipped by reason."""
    skipped = {"dolt_store": 0, "excluded_dir": 0, "copy_or_scratch": 0, "regenerable": 0}
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        at_top = d == root
        keep = []
        for name in dirnames:
            if (at_top and name in _SKIP_TOP) or name in _SKIP_DIR_NAMES or "retired" in name.lower():
                skipped["excluded_dir"] += 1
            elif name in _DOLT_DIRS:
                skipped["dolt_store"] += 1
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            if _SKIP_FILE_RE.search(name):
                skipped["copy_or_scratch"] += 1
            elif _REGENERABLE_RE.match(name):
                skipped["regenerable"] += 1
            else:
                out.append(d / name)
    return sorted(out), skipped


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_snapshot(src: Path, scratch: Path) -> tuple[Path, str]:
    """A consistent copy of a live SQLite database, and its quick_check verdict ("ok" or the error)."""
    dst = scratch / (hashlib.sha1(str(src).encode()).hexdigest() + ".db")
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
            verdict = target.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            target.close()
    finally:
        source.close()
    return dst, str(verdict)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(cfg: dict[str, Any], *, dry_run: bool = False, today: date | None = None) -> dict[str, Any]:
    """Write tonight's backup over the last one (or, with dry_run, report what it would hold)."""
    s = settings(cfg)
    root = _home.home()
    night = (today or datetime.now().date()).isoformat()
    files, skipped = collect(root)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "night": night,
            "dest": str(s["dest"]),
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files if f.exists()),
            "skipped": skipped,
        }
    s["dest"].mkdir(parents=True, exist_ok=True)
    partial = s["dest"] / (LATEST + ".partial")
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    vanished: list[str] = []
    raw_bytes = 0
    started = datetime.now(timezone.utc).isoformat()
    with (
        tempfile.TemporaryDirectory(prefix="cp-backup-") as tmp,
        zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf,
    ):
        scratch = Path(tmp)
        for f in files:
            arc = f.relative_to(root).as_posix()
            try:
                if _is_sqlite(f):
                    snap, verdict = _sqlite_snapshot(f, scratch)
                    data = snap.read_bytes()
                    snap.unlink()
                    entry = {"path": arc, "kind": "sqlite", "quick_check": verdict}
                    if verdict != "ok":
                        problems.append(f"{arc}: quick_check {verdict}")
                else:
                    data = f.read_bytes()
                    entry = {"path": arc, "kind": "file"}
            except FileNotFoundError:
                # Listed by the walk, gone by the copy: something deleted it in between (a daemon
                # stopping, a rotation). Nothing was lost that existed when the copy ran, so it is
                # recorded, not a failed night. A file that exists and cannot be read still is.
                vanished.append(arc)
                continue
            except (OSError, sqlite3.Error) as exc:
                problems.append(f"{arc}: {type(exc).__name__}: {exc}")
                continue
            zf.writestr(arc, data)
            raw_bytes += len(data)
            entries.append({**entry, "bytes": len(data), "sha256": _sha256(data)})
        manifest = {
            "night": night,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "home": cfgmod.portable_path(root),
            "files": entries,
            "vanished": vanished,
            "skipped": skipped,
            "problems": problems,
        }
        zf.writestr(MANIFEST, json.dumps(manifest, indent=1))
    # Only a clean night replaces the backup; a night with problems never overwrites the good one.
    final = s["dest"] / (LATEST if not problems else FAILED)
    os.replace(partial, final)
    if not problems and (s["dest"] / FAILED).exists():
        (s["dest"] / FAILED).unlink()  # superseded by a good night
    return {
        "ok": not problems,
        "night": night,
        "path": cfgmod.portable_path(final),
        "kept_previous": bool(problems),
        "files": len(entries),
        "sqlite": sum(1 for e in entries if e["kind"] == "sqlite"),
        "bytes_raw": raw_bytes,
        "bytes_zip": final.stat().st_size,
        "skipped": skipped,
        "problems": problems,
    }


def verify(cfg: dict[str, Any]) -> dict[str, Any]:
    """Re-read the backup: the zip's CRCs, every file against its manifest sha256, and every SQLite
    copy through quick_check again -- from the zip, not from the live tree."""
    path = latest_path(cfg)
    if not path.exists():
        return {"ok": False, "error": f"no backup at {path}"}
    problems: list[str] = []
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            problems.append(f"CRC mismatch in {bad}")
        manifest = json.loads(zf.read(MANIFEST))
        with tempfile.TemporaryDirectory(prefix="cp-verify-") as tmp:
            for e in manifest["files"]:
                data = zf.read(e["path"])
                if _sha256(data) != e["sha256"]:
                    problems.append(f"{e['path']}: sha256 differs from the manifest")
                if e["kind"] == "sqlite":
                    p = Path(tmp) / "check.db"
                    p.write_bytes(data)
                    con = sqlite3.connect(p)
                    try:
                        verdict = con.execute("PRAGMA quick_check").fetchone()[0]
                    finally:
                        con.close()
                    p.unlink()
                    if verdict != "ok":
                        problems.append(f"{e['path']}: quick_check {verdict}")
    return {
        "ok": not problems,
        "night": manifest.get("night"),
        "path": cfgmod.portable_path(path),
        "files": len(manifest["files"]),
        "problems": problems,
    }


def listing(cfg: dict[str, Any]) -> dict[str, Any]:
    """The backup on hand, and a failed night's zip if the last run had problems."""
    dest = settings(cfg)["dest"]
    out: dict[str, Any] = {"ok": True, "dest": str(dest)}
    for key, name in (("latest", LATEST), ("failed", FAILED)):
        p = dest / name
        if p.exists():
            with zipfile.ZipFile(p) as zf:
                night = json.loads(zf.read(MANIFEST)).get("night")
            out[key] = {"night": night, "path": cfgmod.portable_path(p), "bytes": p.stat().st_size}
    return out


def restore(cfg: dict[str, Any], to_dir: str) -> dict[str, Any]:
    """Extract the backup into `to_dir` -- never into the live home, and never over existing files."""
    path = latest_path(cfg)
    if not path.exists():
        return {"ok": False, "error": f"no backup at {path}"}
    target = Path(os.path.expandvars(os.path.expanduser(to_dir))).resolve()
    home = _home.home().resolve()
    if target == home or home in target.parents:
        return {"ok": False, "error": f"refusing to restore inside the live cherrypick home ({home})"}
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "error": f"{target} is not empty"}
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        night = json.loads(zf.read(MANIFEST)).get("night")
        zf.extractall(target)
        count = len(zf.namelist())
    return {"ok": True, "night": night, "restored_to": str(target), "files": count}


def newest_age_hours(cfg: dict[str, Any], now: datetime | None = None) -> float | None:
    """Hours since the backup was written (by file time), or None when there is none."""
    path = latest_path(cfg)
    if not path.exists():
        return None
    return ((now or datetime.now()).timestamp() - path.stat().st_mtime) / 3600.0
