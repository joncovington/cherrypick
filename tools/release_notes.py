"""Print one version's section of CHANGELOG.md, for the GitHub Release that tag publishes.

The release workflow (.github/workflows/release.yml) runs this on every pushed `v*` tag. The
changelog is the one place a release is described, so a tag whose section is missing or empty
fails here, before anything is published -- a release with no notes says nothing about what
changed, and the person upgrading reads exactly that page.

    python tools/release_notes.py v0.11.0             # the section's body
    python tools/release_notes.py --title v0.11.0     # "v0.11.0 — <its heading's name>"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
# A version heading: `## v0.10.0 — 2026-10-01 — the public release`. The date and the name are
# separated by an em dash; the name is optional.
_HEADING = re.compile(r"^## (v\d+\.\d+\.\d+)\b(?:\s+—\s+(\d{4}-\d{2}-\d{2}))?(?:\s+—\s+(.+))?\s*$")


class NotesError(Exception):
    pass


def section(text: str, version: str) -> tuple[str, str]:
    """(title, body) for `version`'s section. The body runs to the next `## ` heading. Raises
    NotesError when the version has no heading, its heading has no date, or its body is empty."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m or m.group(1) != version:
            continue
        if not m.group(2):
            raise NotesError(f"{version}'s heading has no date: {line!r}")
        body = []
        for later in lines[i + 1 :]:
            if later.startswith("## "):
                break
            body.append(later)
        notes = "\n".join(body).strip()
        if not notes:
            raise NotesError(f"{version}'s section in the changelog is empty")
        title = f"{version} — {m.group(3)}" if m.group(3) else version
        return title, notes + "\n"
    raise NotesError(f"no '## {version} — <date>' heading in the changelog")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("version", help="the tag, e.g. v0.11.0")
    ap.add_argument("--title", action="store_true", help="print the release title instead of the notes")
    ap.add_argument("--changelog", type=Path, default=CHANGELOG)
    args = ap.parse_args(argv)
    try:
        title, notes = section(args.changelog.read_text(encoding="utf-8"), args.version)
    except (NotesError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(title + "\n" if args.title else notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
