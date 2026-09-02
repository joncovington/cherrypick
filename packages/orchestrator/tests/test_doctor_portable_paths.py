"""No doctor check may print an absolute machine path.

"Portable paths only — never hardcode absolute paths, usernames, drive letters" is a suite-wide
guardrail, and `tools/check_docs.py` enforces it for tracked files. Nothing enforced it for text
*generated at runtime*, and doctor's details are rendered verbatim into the dashboard's System card —
so `python 3.13.0 @ C:\\Users\\<name>\\AppData\\...` and three `<module>.config` lines put the
username on screen, next to sibling checks (`.path`, `.paper_db`) that were already portable.

This is written as a blanket rule over every check rather than four assertions about the ones that
were wrong, because the failure mode is a *new* check being added with a raw path in it.
"""

from __future__ import annotations

import re

import pytest

from cherrypick.orchestrator import doctor

pytestmark = pytest.mark.unit

#: A drive-letter path, a POSIX home path, or a bare Windows UNC — the shapes that carry a username.
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/])|(?:/home/[^\s]+)|(?:/Users/[^\s]+)|(?:\\\\[^\s]+)")


def _details(monkeypatch, tmp_path) -> list[tuple[str, str]]:
    """Run the local, cheap half of doctor (fast=True skips the broker round-trip) against a config
    that exercises the module checks — the ones that print config and DB paths."""
    cfg = {
        "timezone": "America/New_York",
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {"paper_db": str(tmp_path / "data" / "paper.db"), "kind": "self_healing"},
            }
        },
    }
    (tmp_path / "meic").mkdir(parents=True, exist_ok=True)
    return [(c.name, c.detail) for c in doctor.run(cfg, fast=True)]


def test_no_check_detail_contains_an_absolute_path(monkeypatch, tmp_path):
    offenders = [(n, d) for n, d in _details(monkeypatch, tmp_path) if _ABSOLUTE.search(d)]
    assert not offenders, "doctor checks must render paths portably: " + "; ".join(
        f"{n} -> {d}" for n, d in offenders
    )


def test_the_interpreter_check_still_reports_a_useful_location():
    """Portable, but not stripped to uselessness — the point of the line is telling one interpreter
    from another, which is what caught the suite running against an environment with no packages."""
    detail = next(c.detail for c in doctor.run({"modules": {}}, fast=True) if c.name == "python")
    assert not _ABSOLUTE.search(detail)
    assert "python" in detail.lower(), "the interpreter path should still be identifiable"
    assert "@" in detail, "version and location should both survive"
