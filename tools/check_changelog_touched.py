#!/usr/bin/env python3
"""Warn when a pull request changes shipped code but not CHANGELOG.md.

    git diff --name-only <base> HEAD | python tools/check_changelog_touched.py

Reads the changed paths on stdin. "Shipped code" is a file under `packages/<name>/src/`, under
`scripts/`, or an `install.*` file. When any is changed and CHANGELOG.md is not, it prints a GitHub
warning. It never fails the build: plenty of changes need no entry (a refactor, a test-only fix), and
only a person can tell. Add a line under `## [Unreleased]`, or label the pull request `no-changelog`
(CI skips this step under that label).
"""

from __future__ import annotations

import re
import sys

CHANGELOG = "CHANGELOG.md"
_SHIPPED = re.compile(r"^(packages/[^/]+/src/|scripts/|install\.[^/]+$)")


def shipped(changed: set[str]) -> list[str]:
    """The changed paths that are shipped code, sorted."""
    return sorted(p for p in changed if _SHIPPED.match(p))


def main(stdin: str | None = None) -> int:
    changed = {
        line.strip() for line in (sys.stdin.read() if stdin is None else stdin).splitlines() if line.strip()
    }
    hits = shipped(changed)
    if hits and CHANGELOG not in changed:
        more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
        print(
            f"::warning file={CHANGELOG}::{hits[0]}{more} changed, but CHANGELOG.md did not. Add a line "
            "under '## [Unreleased]' saying what a user would notice, or label the PR no-changelog."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
