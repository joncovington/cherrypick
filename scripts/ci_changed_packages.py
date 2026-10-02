#!/usr/bin/env python3
"""Which CI jobs a change needs: read changed paths on stdin, print GitHub step outputs.

    git diff --name-only HEAD^1 HEAD | python scripts/ci_changed_packages.py >> "$GITHUB_OUTPUT"
    python scripts/ci_changed_packages.py --all >> "$GITHUB_OUTPUT"

Prints three lines:
  packages=<JSON list>  the Python packages whose test job must run
  console=true|false    whether the console job must run
  guards=true|false     whether the guard-mutant job must run

The rules, in the order they apply to each changed path:
  - packages/core/**              everything: every package imports core.
  - packages/console/**           the console job.
  - packages/orchestrator/**      orchestrator, and the console (its Config page bridges the
                                  orchestrator's config editor).
  - packages/<p>/**               that package's job. No package imports another except core
                                  (checked 2026-10-02), so nothing else can break.
  - packages/<unknown>/**         everything: a directory this script does not recognise is not one
                                  it can reason about.
  - any other *.md file           nothing: the docs job always runs and is the only one that reads it.
  - anything else                 everything: scripts/, tools/, .github/, ruff.toml and the
                                  installers are shared by every job.
The guard job runs when a changed package carries a guard mutant (`guard_mutants_plugin.MUTANTS`,
read rather than listed here) or when everything runs.

Every job still REPORTS: the workflow skips an unneeded job's steps rather than the job, because
branch protection requires each check by name and a check that never reports blocks the merge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def python_packages(root: Path = ROOT) -> list[str]:
    """Every package with a pyproject.toml and a tests/ directory: the CI test matrix."""
    return sorted(
        d.name
        for d in (root / "packages").iterdir()
        if (d / "pyproject.toml").is_file() and (d / "tests").is_dir()
    )


def mutant_packages() -> set[str]:
    from guard_mutants_plugin import MUTANTS

    return {m.package for m in MUTANTS}


def plan(paths: list[str], *, all_jobs: bool = False, root: Path = ROOT) -> dict:
    """{"packages": [...], "console": bool, "guards": bool} for these changed paths."""
    known = python_packages(root)
    everything = {"packages": known, "console": True, "guards": True}
    if all_jobs:
        return everything
    packages: set[str] = set()
    console = False
    for raw in paths:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        parts = path.split("/")
        if parts[0] == "packages" and len(parts) > 2:
            name = parts[1]
            if name == "core":
                return everything
            if name == "console":
                console = True
            elif name in known:
                packages.add(name)
                if name == "orchestrator":
                    console = True
            else:
                return everything
        elif path.endswith(".md"):
            continue
        else:
            return everything
    return {"packages": sorted(packages), "console": console, "guards": bool(packages & mutant_packages())}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = plan([] if "--all" in argv else sys.stdin.read().splitlines(), all_jobs="--all" in argv)
    print(f"packages={json.dumps(out['packages'])}")
    print(f"console={'true' if out['console'] else 'false'}")
    print(f"guards={'true' if out['guards'] else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
