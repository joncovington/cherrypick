"""scripts/ci_changed_packages.py: which CI jobs a change needs.

A rule here that runs too little is the dangerous kind -- it reads as a green check over code
nobody tested -- so every case that must run MORE is pinned, and the package list is checked
against the CI matrix it stands in for.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ci_changed_packages", ROOT / "scripts" / "ci_changed_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ccp = _load()
ALL = ccp.python_packages()


def test_the_package_list_is_the_ci_matrix():
    """The script's 'everything' must be exactly what CI's matrix tests, or a package added to one
    and not the other is either never run or never skipped."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"package:\s*\[([^\]]*)\]", ci).group(1)
    assert sorted(p.strip() for p in matrix.split(",")) == ALL


def test_one_package_runs_alone():
    assert ccp.plan(["packages/pmcc/src/cherrypick/pmcc/engine.py"]) == {
        "packages": ["pmcc"],
        "console": False,
        "guards": False,
    }


def test_a_package_with_a_guard_mutant_runs_the_guards():
    out = ccp.plan(["packages/flies/src/cherrypick/flies/live_loop.py"])
    assert out["packages"] == ["flies"] and out["guards"] is True


def test_core_runs_everything():
    assert ccp.plan(["packages/core/cherrypick/core/fees.py"]) == {
        "packages": ALL,
        "console": True,
        "guards": True,
    }


def test_shared_paths_run_everything():
    for path in (
        "scripts/guard_mutants.py",
        ".github/workflows/ci.yml",
        "ruff.toml",
        "tools/check_docs.py",
        "install.sh",
    ):
        assert ccp.plan([path])["packages"] == ALL, path


def test_an_unknown_package_directory_runs_everything():
    assert ccp.plan(["packages/newthing/src/x.py"])["packages"] == ALL


def test_docs_outside_packages_run_no_package_job():
    assert ccp.plan(["README.md", "docs/releasing.md", "CHANGELOG.md"]) == {
        "packages": [],
        "console": False,
        "guards": False,
    }


def test_the_console_and_the_orchestrator_it_bridges():
    assert ccp.plan(["packages/console/web/src/App.tsx"]) == {
        "packages": [],
        "console": True,
        "guards": False,
    }
    out = ccp.plan(["packages/orchestrator/src/cherrypick/orchestrator/config_editor.py"])
    assert out["packages"] == ["orchestrator"] and out["console"] is True


def test_a_package_doc_still_runs_its_package():
    assert ccp.plan(["packages/bwb/CLAUDE.md"])["packages"] == ["bwb"]


def test_all_flag_and_windows_separators():
    assert ccp.plan([], all_jobs=True)["packages"] == ALL
    assert ccp.plan(["packages\\gex\\src\\cherrypick\\gex\\service.py"])["packages"] == ["gex"]


def test_the_output_is_github_step_output_lines(capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("packages/curve/src/cherrypick/curve/engine.py\n"))
    assert ccp.main([]) == 0
    assert capsys.readouterr().out.splitlines() == ['packages=["curve"]', "console=false", "guards=false"]
