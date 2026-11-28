"""Tests for tools/release_checksum.py: the checksum a published manifest carries for the tag's ZIP."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import release_checksum as rc  # noqa: E402


def test_the_manifest_asset_carries_the_archive_digest_and_the_source_manifest_is_untouched(tmp_path):
    archive = tmp_path / "v0.13.0.zip"
    archive.write_bytes(b"zip bytes")
    src = tmp_path / "manifest.json"
    src.write_text(json.dumps({"schema": 1, "releases": {"v0.13.0": {"upgrading": ["x"]}}}), encoding="utf-8")
    out = tmp_path / "release-manifest.json"
    assert rc.main(["v0.13.0", "--manifest", str(src), "--archive", str(archive), "--out", str(out)]) == 0
    entry = json.loads(out.read_text(encoding="utf-8"))["releases"]["v0.13.0"]
    assert entry["archive_sha256"] == hashlib.sha256(b"zip bytes").hexdigest()
    assert "archive_sha256" not in json.loads(src.read_text(encoding="utf-8"))["releases"]["v0.13.0"]


def test_a_tag_with_no_entry_is_refused():
    with pytest.raises(SystemExit, match="no entry"):
        rc.with_checksum({"releases": {}}, "v0.13.0", "0" * 64)
