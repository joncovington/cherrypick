"""Write the release manifest asset with the SHA-256 of the tag's source ZIP in the tag's entry.

The release workflow runs this just before it publishes, so the checksum is computed from the very
archive GitHub serves for the tag. `run.py upgrade` downloads that ZIP and refuses to unzip it unless
its SHA-256 equals `archive_sha256` in the entry. The checked-in release/manifest.json never holds the
value: the archive does not exist until the tag does.

    python tools/release_checksum.py v0.13.0 --out release-manifest.json
    python tools/release_checksum.py v0.13.0 --archive path/to/v0.13.0.zip --out release-manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO = "joncovington/cherrypick"
MANIFEST = Path(__file__).resolve().parent.parent / "release" / "manifest.json"


def archive_url(tag: str) -> str:
    return f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_archive(tag: str, tries: int = 4) -> bytes:
    """The tag's ZIP as GitHub serves it, retried a few times (it can lag the tag push by seconds)."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(archive_url(tag), headers={"User-Agent": "cherrypick-release"})
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 -- https URL built here
                return resp.read()
        except OSError as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"could not download {archive_url(tag)}: {last}")


def with_checksum(manifest: dict, tag: str, digest: str) -> dict:
    """The manifest with `archive_sha256` set in `tag`'s entry; the input is not changed."""
    releases = manifest.get("releases")
    if not isinstance(releases, dict) or not isinstance(releases.get(tag), dict):
        raise SystemExit(f"the manifest has no entry for {tag}")
    out = json.loads(json.dumps(manifest))
    out["releases"][tag]["archive_sha256"] = digest
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("version", help="the tag, e.g. v0.13.0")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--archive", type=Path, help="a local copy of the ZIP (default: download the tag's)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    data = args.archive.read_bytes() if args.archive else fetch_archive(args.version)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    out = with_checksum(manifest, args.version, sha256_of(data))
    args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{args.version}: archive_sha256 {out['releases'][args.version]['archive_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
