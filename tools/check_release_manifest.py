"""Refuse a release tag that has no entry in release/manifest.json, or an entry that is not well formed.

The release workflow (.github/workflows/release.yml) runs this on every pushed `v*` tag, before
anything is published. The manifest is what `run.py upgrade` reads from the release page to show the
person upgrading what the release needs from them, so a release without an entry would upgrade
people blind.

    python tools/check_release_manifest.py v0.12.0

An entry holds, for one version:
    first_with_upgrade_tooling  optional bool; true on the first release that has `run.py upgrade`
    upgrading                   list of strings: what the person upgrading should know (not empty)
    manual_steps                list of strings: what they have to do by hand
    config_changes              list of {file, key, note}: config keys the release added, renamed or removed
    measurement_boundaries      list of strings: changes that move a measured number, so a series breaks here
    migrations                  list of migration ids, in the order they run

Exit 0 when the entry is present and well formed; one line per problem and exit 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "release" / "manifest.json"
_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
_MIGRATION_ID = re.compile(r"^v\d+\.\d+\.\d+-\d{2}-[a-z0-9][a-z0-9-]*$")
_STRING_LISTS = ("upgrading", "manual_steps", "measurement_boundaries")


def problems(manifest: object, version: str) -> list[str]:
    """Everything wrong with `version`'s entry in a parsed manifest (empty when it is fine)."""
    if not _TAG.match(version):
        return [f"{version!r} is not a vMAJOR.MINOR.PATCH tag"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("releases"), dict):
        return ["the manifest has no `releases` object"]
    releases = manifest["releases"]
    bad_keys = [k for k in releases if not _TAG.match(str(k))]
    out = [f"`releases` has a key that is not a version: {k!r}" for k in bad_keys]
    entry = releases.get(version)
    if entry is None:
        return out + [f"no entry for {version} in release/manifest.json"]
    if not isinstance(entry, dict):
        return out + [f"{version}: the entry is not an object"]
    for key in _STRING_LISTS:
        value = entry.get(key)
        if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
            out.append(f"{version}: `{key}` must be a list of non-empty strings")
    if not entry.get("upgrading"):
        out.append(f"{version}: `upgrading` is empty; say what a person upgrading should know")
    changes = entry.get("config_changes")
    if not isinstance(changes, list) or not all(
        isinstance(c, dict)
        and all(isinstance(c.get(f), str) and c[f].strip() for f in ("file", "key", "note"))
        for c in changes
    ):
        out.append(f"{version}: `config_changes` must be a list of {{file, key, note}} objects")
    migrations = entry.get("migrations")
    if not isinstance(migrations, list) or not all(
        isinstance(m, str) and _MIGRATION_ID.match(m) for m in migrations
    ):
        out.append(f"{version}: `migrations` must be a list of migration ids like v0.13.0-01-name")
    elif len(set(migrations)) != len(migrations):
        out.append(f"{version}: `migrations` repeats an id")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("version", help="the tag, e.g. v0.12.0")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{args.manifest}: cannot read: {exc}", file=sys.stderr)
        return 1
    found = problems(manifest, args.version)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
