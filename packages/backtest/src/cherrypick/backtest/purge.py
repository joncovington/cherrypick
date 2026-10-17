"""Expunge the vendor's data (and, on request, everything derived from it).

Dry run by default: the reply lists every file and its size, and nothing is touched until `apply`.
Only directories carrying the licensed-data marker are removed, so a mistyped `store.dir` cannot point
this at anything else.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import paths


def _listing(root: Path) -> list[dict]:
    if not root.exists():
        return []
    return [{"path": str(p), "bytes": p.stat().st_size} for p in sorted(root.rglob("*")) if p.is_file()]


def plan(settings: dict, *, derived: bool = False) -> dict:
    vendor = paths.vendor_dir(settings)
    targets = []
    refused = []
    if vendor.exists():
        if (vendor / paths.LICENSED_MARKER).exists():
            targets.append(vendor)
        else:
            refused.append({"path": str(vendor), "reason": "no licensed-data marker; not removed"})
    if derived and paths.results_dir().exists():
        targets.append(paths.results_dir())
    files = [f for t in targets for f in _listing(t)]
    return {
        "targets": [str(t) for t in targets],
        "refused": refused,
        "files": len(files),
        "bytes": sum(f["bytes"] for f in files),
        "listing": files,
    }


def apply(settings: dict, *, derived: bool = False) -> dict:
    report = plan(settings, derived=derived)
    for t in report["targets"]:
        shutil.rmtree(t)
    report["applied"] = True
    report["remaining"] = [t for t in report["targets"] if Path(t).exists()]
    return report
