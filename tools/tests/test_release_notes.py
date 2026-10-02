"""Tests for tools/release_notes.py -- the release workflow's notes, cut from CHANGELOG.md.

What must not happen: a release published with someone else's notes (the next section bleeding in,
or `[Unreleased]` passed off as a version), or with none at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import release_notes  # noqa: E402

CHANGELOG = """# Changelog

Preface.

## [Unreleased]

- Hot options in the morning pack.

## v0.11.0 — 2026-10-15 — hot options

- The morning pack ranks OCC's volume.
- One OCC fetch.

## v0.10.0 — 2026-10-01 — the public release
The first release.

## v0.9.0 — 2026-09-18
- bwb's live path.

## v0.8.0 — 2026-08-23 — empty

## v0.7.0
- no date.
"""


def test_the_body_stops_at_the_next_heading():
    title, notes = release_notes.section(CHANGELOG, "v0.11.0")
    assert title == "v0.11.0 — hot options"
    assert notes == "- The morning pack ranks OCC's volume.\n- One OCC fetch.\n"


def test_a_heading_without_a_name_is_titled_by_its_version():
    assert release_notes.section(CHANGELOG, "v0.9.0") == ("v0.9.0", "- bwb's live path.\n")


def test_a_body_with_no_blank_line_after_its_heading_is_kept():
    assert release_notes.section(CHANGELOG, "v0.10.0")[1] == "The first release.\n"


@pytest.mark.parametrize(
    ("version", "why"),
    [
        ("v0.12.0", "no '## v0.12.0"),  # tagged before its changelog section was written
        ("v0.8.0", "empty"),
        ("v0.7.0", "no date"),
        ("v0.1", "no '## v0.1"),  # never matches v0.10.0 or v0.11.0 by prefix
    ],
)
def test_a_tag_the_changelog_does_not_describe_is_refused(version, why):
    with pytest.raises(release_notes.NotesError, match=why):
        release_notes.section(CHANGELOG, version)


def test_unreleased_is_never_a_version():
    with pytest.raises(release_notes.NotesError):
        release_notes.section(CHANGELOG, "[Unreleased]")


def test_the_cli_fails_before_printing_anything(tmp_path, capsys):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    assert release_notes.main(["v0.12.0", "--changelog", str(path)]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "v0.12.0" in out.err
    assert release_notes.main(["--title", "v0.11.0", "--changelog", str(path)]) == 0
    assert capsys.readouterr().out == "v0.11.0 — hot options\n"


def test_every_released_version_in_the_real_changelog_has_notes():
    text = release_notes.CHANGELOG.read_text(encoding="utf-8")
    versions = [m.group(1) for line in text.splitlines() if (m := release_notes._HEADING.match(line))]
    assert "v0.10.0" in versions
    for version in versions:
        assert release_notes.section(text, version)[1].strip()
