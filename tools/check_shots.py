#!/usr/bin/env python3
"""Keep the documentation screenshots of the console honest: what is in docs/images/console/ is what
docs/images/shots.json says, and a change to the code that draws a screenshot does not go unnoticed.

Two findings of different weight:

  FAIL (exit 1), a structural fault in the manifest or the image folder:
    - an image under docs/images/console/ that no manifest entry names,
    - a manifest entry whose image is missing,
    - a manifest entry whose route is not one the console serves (read from the console's own nav
      files, so a renamed tab cannot leave a screenshot pointing at nothing),
    - a manifest entry with a missing field or a `sources` file that does not exist (a source that
      names nothing could never warn).

  WARN (exit 0, a log line and a GitHub `::warning::` annotation): the pull request changes a file in
    an entry's `sources` but not the entry's image. The screenshot may now be out of date; the release
    reshoot (scripts/reshoot.py) is what refreshes it. A pull request carrying the label
    `screenshots-ok` is saying it knows, and is not warned.

    python tools/check_shots.py [BASE]

BASE is the ref the pull request is measured against (default `origin/main`, or $SHOTS_BASE). The
labels come from $PR_LABELS, a comma-separated list the CI job sets from the pull request. With no
BASE to compare against (a shallow or local checkout) the warning check is skipped, never failed.
Standard library and git only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path("docs/images/shots.json")
IMAGES = Path("docs/images/console")
OK_LABEL = "screenshots-ok"
REQUIRED = ("path", "route", "card", "scale", "full_page", "seed", "day", "sources")

WEB = Path("packages/console/web/src")
NAV_GROUPS = WEB / "lightbox" / "navGroups.ts"
MODULE_ORDER = WEB / "lightbox" / "moduleOrder.ts"
CONFIG_META = WEB / "pages" / "Config" / "fieldMeta.ts"

# Routes with a free tail: `reports/external/<id>` is a tab per report file, so its last part is data.
OPEN_TAILS = {"reports/external"}


# ---------------------------------------------------------------------------------- the routes
def _slides(text: str) -> dict[str, set[str]]:
    """{SLIDE_CONST: {tab ids}} for every `export const X_SLIDES = [ { id: "...", ... }, ... ]`."""
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"export const ([A-Z_]+_SLIDES)\b[^=]*=\s*\[(.*?)\]\s*(?:as const|;)", text, re.S):
        out[m.group(1)] = set(re.findall(r'\bid:\s*"([^"]+)"', m.group(2)))
    return out


def known_routes(root: Path = ROOT) -> dict[str, set[str]]:
    """{module: {tab ids}} as the console's nav files declare them."""
    groups = (root / NAV_GROUPS).read_text(encoding="utf-8")
    consts = _slides(groups)
    modules: dict[str, set[str]] = {}
    for m in re.finditer(r"\b(\w+):\s*\{\s*slides:\s*([A-Z_]+)\b", groups):
        if m.group(2) in consts:
            modules[m.group(1)] = set(consts[m.group(2)])
    # Config's tabs are the Config page's sections plus `prefs`, read from fieldMeta rather than copied.
    meta = (root / CONFIG_META).read_text(encoding="utf-8")
    block = re.search(r"export const SECTIONS\b[^=]*=\s*\[(.*?)\n\];", meta, re.S)
    modules["config"] = set(re.findall(r'\bid:\s*"([a-z]+)"', block.group(1) if block else "")) | {"prefs"}
    # A module the shell does not know has no page, whatever navGroups says.
    order = (root / MODULE_ORDER).read_text(encoding="utf-8")
    declared = set(re.findall(r'"([a-z]+)"', " ".join(re.findall(r"_ORDER\s*=\s*\[(.*?)\]", order, re.S))))
    return {k: v for k, v in modules.items() if k in declared}


def route_problem(route: str, routes: dict[str, set[str]]) -> str | None:
    """Why `route` is not one the console serves, or None. A query string is not part of the route."""
    parts = route.split("?")[0].strip("/").split("/")
    module = parts[0]
    if module not in routes:
        return f"unknown page {module!r}"
    if len(parts) == 1:
        return None
    if f"{module}/{parts[1]}" in OPEN_TAILS and len(parts) in (2, 3):
        return None
    if len(parts) > 2 or parts[1] not in routes[module]:
        return f"{module!r} has no tab {'/'.join(parts[1:])!r}"
    return None


# ---------------------------------------------------------------------------------- the findings
def load_manifest(root: Path = ROOT) -> list[dict]:
    doc = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    return doc["shots"] if isinstance(doc, dict) else doc


def failures(root: Path = ROOT) -> list[str]:
    """Structural faults, one line each (empty when the manifest and the image folder agree)."""
    try:
        shots = load_manifest(root)
    except (OSError, ValueError, KeyError) as exc:
        return [f"{MANIFEST}: cannot read the manifest ({exc})"]
    routes = known_routes(root)
    out: list[str] = []
    named: set[str] = set()
    for i, shot in enumerate(shots):
        where = f"{MANIFEST}: entry {i} ({shot.get('path', '?')})"
        missing = [k for k in REQUIRED if k not in shot]
        if missing:
            out.append(f"{where}: missing field(s) {', '.join(missing)}")
            continue
        named.add(shot["path"])
        if not (root / shot["path"]).is_file():
            out.append(f"{where}: the image is missing")
        problem = route_problem(shot["route"], routes)
        if problem:
            out.append(f"{where}: route {shot['route']!r}: {problem}")
        for src in shot["sources"]:
            if not (root / src).is_file():
                out.append(f"{where}: source {src} does not exist")
    folder = root / IMAGES
    if folder.is_dir():
        for img in sorted(folder.glob("*.png")):
            rel = img.relative_to(root).as_posix()
            if rel not in named:
                out.append(f"{rel}: no manifest entry in {MANIFEST} (add one, or delete the image)")
    return out


def stale(shots: list[dict], changed: set[str]) -> list[tuple[str, list[str]]]:
    """[(image, changed source files)] for each shot whose code changed while its image did not."""
    out = []
    for shot in shots:
        hit = sorted(s for s in shot["sources"] if s in changed)
        if hit and shot["path"] not in changed:
            out.append((shot["path"], hit))
    return out


def changed_files(base: str, root: Path = ROOT) -> set[str] | None:
    """Files the change touches against `base`, or None when git cannot say (no such ref, no git)."""
    try:
        done = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return {line.strip() for line in done.stdout.splitlines() if line.strip()}


def annotate(image: str, sources: list[str]) -> list[str]:
    """The log line, and the GitHub annotation after it when running there."""
    text = (
        f"{image}: the code that draws it changed ({', '.join(sources)}) but the image did not. "
        "Reshoot it at release (scripts/reshoot.py), or label the pull request "
        f"{OK_LABEL} if the change is not visible."
    )
    lines = [f"warning: {text}"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        lines.append(f"::warning file={image}::{text}")
    return lines


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    base = args[0] if args else os.environ.get("SHOTS_BASE", "origin/main")
    bad = failures(root)
    for line in bad:
        print(line)
    if bad:
        print(f"check_shots: {len(bad)} finding(s)")
        return 1
    labels = {x.strip() for x in os.environ.get("PR_LABELS", "").split(",") if x.strip()}
    if OK_LABEL in labels:
        print(f"check_shots: ok ({OK_LABEL} label: stale-image warnings skipped)")
        return 0
    changed = changed_files(base, root)
    if changed is None:
        print(f"check_shots: ok (cannot diff against {base}; stale-image warnings skipped)")
        return 0
    found = stale(load_manifest(root), changed)
    for image, sources in found:
        for line in annotate(image, sources):
            print(line)
    print(f"check_shots: ok ({len(found)} image(s) possibly stale)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
