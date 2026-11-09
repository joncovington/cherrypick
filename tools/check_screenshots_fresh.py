#!/usr/bin/env python3
"""Warn when a pull request changes what a guide screenshot shows but not the screenshot.

    git diff --name-only <base> HEAD | python tools/check_screenshots_fresh.py

Reads the changed paths on stdin and docs/images/shots.json. For each console screenshot whose
`sources` the change touches while the image itself is untouched, it prints a GitHub warning naming
the image and the files. It never fails the build: a change to a page's code often leaves its picture
right, and only a person can tell. Retake the shot (`python scripts/reshoot.py --only <file>`), or
label the pull request `screenshots-ok` to say it was looked at (CI skips this step under that label).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = "docs/images/shots.json"


def stale(changed: set[str], manifest: dict) -> list[tuple[str, list[str]]]:
    """(image, the changed source files) for each shot whose sources changed without it."""
    out = []
    for e in manifest.get("shots", []):
        image = f"docs/images/{e['file']}"
        hits = sorted(changed & set(e.get("sources", [])))
        if hits and image not in changed:
            out.append((e["file"], hits))
    return out


def main(stdin: str | None = None, root: Path = ROOT) -> int:
    changed = {
        line.strip() for line in (sys.stdin.read() if stdin is None else stdin).splitlines() if line.strip()
    }
    path = root / SHOTS
    if not path.is_file():
        return 0
    for image, files in stale(changed, json.loads(path.read_text(encoding="utf-8"))):
        print(
            f"::warning file=docs/images/{image}::{image} may be out of date: this change touches "
            f"{', '.join(files)}. Retake it (python scripts/reshoot.py --only {image}) "
            "or label the PR screenshots-ok."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
