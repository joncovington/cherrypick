"""#1082: a stamp records the files a study rests on, and a change to one marks its result stale."""

from __future__ import annotations

import json

import pytest

from cherrypick.orchestrator import studyversion as sv


@pytest.fixture
def repo(tmp_path):
    core = tmp_path / "packages" / "core" / "cherrypick" / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "bs.py").write_text("def price(): ...\n", encoding="utf-8")
    (core / "home.py").write_text("X = 1\n", encoding="utf-8")
    kit = tmp_path / "packages" / "backtest" / "src" / "cherrypick" / "backtest" / "study"
    kit.mkdir(parents=True)
    (kit.parent / "__init__.py").write_text("", encoding="utf-8")
    (kit / "__init__.py").write_text("", encoding="utf-8")
    (kit / "checks.py").write_text("T = 2\n", encoding="utf-8")
    scripts = tmp_path / "packages" / "backtest" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "chain_item59.py").write_text("from cherrypick.core import home\n", encoding="utf-8")
    (scripts / "study_item90.py").write_text(
        # A long docstring with a newline: not a script name, and never asked of the filesystem (on
        # Linux that fails outright, "File name too long").
        '"""' + "A study. " * 60 + '\nMore."""\n'
        "from cherrypick.core import bs\n"
        "from cherrypick.backtest.study import checks\n"
        "import json\n"
        "i59 = _load('chain_item59')\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_script_its_loaded_scripts_and_its_direct_imports_are_its_dependencies(repo):
    deps = sv.dependencies(repo / "packages/backtest/scripts/study_item90.py", repo)
    assert deps == [
        "packages/backtest/scripts/chain_item59.py",
        "packages/backtest/scripts/study_item90.py",
        "packages/backtest/src/cherrypick/backtest/study/checks.py",
        "packages/core/cherrypick/core/bs.py",
        "packages/core/cherrypick/core/home.py",  # through the loaded script, followed
    ]


def _stamped(repo, out):
    return sv.stamp(
        out, repo / "packages/backtest/scripts/study_item90.py", repo, now="2026-11-14", sha="abc"
    )


def test_a_change_to_a_dependency_marks_the_result_stale_and_names_it(repo, tmp_path):
    out = tmp_path / "results.json"
    out.write_text("{}", encoding="utf-8")
    doc = _stamped(repo, out)
    assert sv.state(doc, [], repo) == {**sv.state(doc, [], repo), "stale": False, "changed_files": []}
    (repo / "packages/backtest/scripts/chain_item59.py").write_text(
        "from cherrypick.core import home\nY = 2\n"
    )
    st = sv.state(sv.read_stamp(out), [], repo)
    assert st["stale"] is True and st["changed_files"] == ["packages/backtest/scripts/chain_item59.py"]


def test_a_package_init_is_not_a_dependency_of_a_submodule_import(repo, tmp_path):
    out = tmp_path / "results.json"
    out.write_text("{}", encoding="utf-8")
    doc = _stamped(repo, out)
    (repo / "packages/core/cherrypick/core/__init__.py").write_text("# unrelated\n")
    assert sv.state(doc, [], repo)["changed_files"] == []


def test_a_stamp_without_depends_reads_as_before(repo):
    old = {"version": 0, "script": "s.py", "commit": None}
    st = sv.state(old, [], repo)
    assert st["stale"] is False and st["changed_files"] == []


def test_the_stamp_file_carries_the_hashes(repo, tmp_path):
    out = tmp_path / "results.json"
    out.write_text("{}", encoding="utf-8")
    _stamped(repo, out)
    doc = json.loads(sv.stamp_path(out).read_text(encoding="utf-8"))
    assert len(doc["depends"]) == 5 and all(len(h) == 64 for h in doc["depends"].values())
