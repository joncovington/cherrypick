"""End-of-month log & report rotation (read/maintenance side).

Bundles each finished month's dated reports (paper-eod / eod-analysis / eod-digest / live eod) and rotated
log backups into one zip per scope per month under ``~/.cherrypick/logs/archive/<YYYY-MM>/<scope>.zip``,
then removes the originals once the zip is written and verified. "Scope" is the suite logs root itself
(top-level files) plus each per-module logs subdirectory (meic, earnings, …) — discovered from the tree,
so a new module is covered without wiring.

Safe by construction and idempotent:
  - Only months **strictly before** the current one are touched, so a file still being written this month
    is never archived (re-runnable any day).
  - The **active** ``*.log`` a daemon holds open is never touched; only rotated backups (``*.log.N``) and
    dated report files are archived.
  - Originals are deleted **only after** the zip verifies (``testzip()``), and re-running appends just the
    files not already present — so a repeat run, or a run after a partial one, converges without loss.

Files only — no broker, no network, no AI. This is a scheduled/on-demand maintenance surface (the monthly
``cherrypick-log-archive`` task), **off** the watchdog reliability path.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from . import config as cfgmod

# A YYYY-MM-DD anywhere in the filename fixes which month a report belongs to (independent of when it is
# archived); everything else falls back to the file's mtime.
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# Rotated log backups written by RotatingFileHandler: foo.log.1, foo.log.2, …
_ROTATED_LOG_RE = re.compile(r"\.log\.\d+$")

_ARCHIVE_DIRNAME = "archive"


def _file_month(p: Path) -> tuple[int, int]:
    """(year, month) a file belongs to — from a YYYY-MM-DD in its name, else its modification time."""
    m = _DATE_RE.search(p.name)
    if m:
        return int(m.group(1)), int(m.group(2))
    dt = datetime.fromtimestamp(p.stat().st_mtime)
    return dt.year, dt.month


def _is_archivable(p: Path) -> bool:
    """Dated reports and rotated log backups are archivable; the active .log a daemon holds open, and any
    existing .zip, are not."""
    if not p.is_file():
        return False
    name = p.name
    if name.endswith(".zip"):
        return False
    if _ROTATED_LOG_RE.search(name):
        return True
    if name.endswith(".log"):  # active, still-open log — leave it
        return False
    return bool(_DATE_RE.search(name))


def _scopes(logs_root: Path) -> dict[str, Path]:
    """The archiving scopes: the suite logs root itself (its top-level files) plus each per-module logs
    subdirectory, keyed by name. The archive directory is never a scope."""
    out: dict[str, Path] = {"suite": logs_root}
    if logs_root.exists():
        for d in sorted(logs_root.iterdir()):
            if d.is_dir() and d.name != _ARCHIVE_DIRNAME:
                out[d.name] = d
    return out


def run(cfg: dict | None = None, logs_root: str | Path | None = None,
        now: datetime | None = None, month: str | None = None, dry_run: bool = False) -> dict:
    """Archive every complete prior month's logs/reports into ``logs/archive/<YYYY-MM>/<scope>.zip``.

    `month` (``'YYYY-MM'``) restricts to one month; `dry_run` reports what would move without writing or
    deleting anything. Returns a summary: per (scope, month) zip path + file count, and any errors."""
    logs_root = Path(logs_root) if logs_root else cfgmod.LOGS_DIR
    now = now or datetime.now()
    current = (now.year, now.month)
    archive_root = logs_root / _ARCHIVE_DIRNAME

    summary: dict = {
        "ok": True, "dry_run": dry_run, "logs_root": str(logs_root),
        "archive_root": str(archive_root), "archived": [], "months": {}, "files": 0,
    }

    for scope, d in _scopes(logs_root).items():
        if not d.exists():
            continue
        groups: dict[tuple[int, int], list[Path]] = {}
        for p in sorted(d.iterdir()):
            if not _is_archivable(p):
                continue
            ym = _file_month(p)
            if ym >= current:  # keep the current (and any future-dated) month live
                continue
            if month and f"{ym[0]:04d}-{ym[1]:02d}" != month:
                continue
            groups.setdefault(ym, []).append(p)

        for (y, mo), files in sorted(groups.items()):
            label = f"{y:04d}-{mo:02d}"
            zpath = archive_root / label / f"{scope}.zip"
            entry: dict = {"scope": scope, "month": label, "zip": str(zpath), "files": len(files)}

            if not dry_run:
                try:
                    _archive_into(zpath, files)
                except Exception as exc:  # never let one scope's failure abort the rest
                    entry["error"] = str(exc)
                    summary["ok"] = False

            summary["archived"].append(entry)
            summary["months"][label] = summary["months"].get(label, 0) + len(files)
            summary["files"] += len(files)

    return summary


def _archive_into(zpath: Path, files: list[Path]) -> None:
    """Append `files` to `zpath` (creating it), verify the archive, then delete the originals that made it
    in. Files already present in the zip are not re-added — so a re-run is idempotent."""
    zpath.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if zpath.exists():
        with zipfile.ZipFile(zpath) as zf:
            existing = set(zf.namelist())
    with zipfile.ZipFile(zpath, "a", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            if p.name not in existing:
                zf.write(p, arcname=p.name)
    # Verify before deleting anything — a corrupt archive must not cost the originals.
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        if zf.testzip() is not None:
            raise OSError(f"zip verification failed for {zpath}")
    for p in files:
        if p.name in names:
            p.unlink(missing_ok=True)
