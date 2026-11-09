"""Tests for tools/check_changelog_touched.py, the warning for shipped code changed without a changelog line.

It must warn on shipped code alone, stay quiet when the changelog is edited too or when only docs and
tests changed, and never fail the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_changelog_touched as cct  # noqa: E402


def run(capsys, paths: list[str]) -> tuple[int, str]:
    code = cct.main("\n".join(paths))
    return code, capsys.readouterr().out


def test_a_package_source_change_without_the_changelog_warns(capsys):
    code, out = run(capsys, ["packages/manage/src/cherrypick/manage/evaluate.py"])
    assert code == 0
    assert out.startswith("::warning")
    assert "packages/manage/src/cherrypick/manage/evaluate.py" in out


def test_scripts_and_installers_count(capsys):
    assert "::warning" in run(capsys, ["scripts/fetch_dollar_volume.py"])[1]
    assert "::warning" in run(capsys, ["install.ps1"])[1]
    assert "::warning" in run(capsys, ["install.sh"])[1]


def test_editing_the_changelog_silences_it(capsys):
    _, out = run(capsys, ["packages/core/src/cherrypick/core/fees.py", "CHANGELOG.md"])
    assert out == ""


def test_docs_tests_and_console_only_changes_are_quiet(capsys):
    paths = [
        "docs/guide/faq.md",
        "packages/core/tests/test_fees.py",
        "packages/console/server/src/app.ts",
        "tools/check_docs.py",
        "README.md",
    ]
    assert run(capsys, paths)[1] == ""


def test_a_look_alike_path_is_not_shipped(capsys):
    assert run(capsys, ["scripts_old/x.py", "installer-notes.md"])[1] == ""


def test_many_files_make_one_warning(capsys):
    _, out = run(capsys, ["scripts/a.py", "scripts/b.py", "scripts/c.py"])
    assert out.count("::warning") == 1
    assert "and 2 more" in out
