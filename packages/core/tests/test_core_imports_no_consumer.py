"""The core imports nothing from a consumer, and `cherrypick/` stays a namespace package.

The consumers are not listed here: they are every directory under `packages/*/src/cherrypick/` other
than core's own, so a package added later is covered the day it lands. (The list this replaces lived
in a prose grep and had already missed weeklyput and fundamentals.)

Verified to fail: `test_the_scan_catches_an_import_of_a_consumer` plants an import of a consumer in a
throwaway tree and asserts the scanner names it; `test_the_namespace_check_catches_an_init` does the
same for the PEP 420 rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"
CORE_ROOT = PACKAGES / "core" / "cherrypick"


def consumer_names() -> set[str]:
    """`cherrypick.<name>` for every package other than core: read off the directory tree."""
    names: set[str] = set()
    for pkg in PACKAGES.iterdir():
        ns = pkg / "src" / "cherrypick"
        if pkg.name == "core" or not ns.is_dir():
            continue
        names.update(d.name for d in ns.iterdir() if d.is_dir() and not d.name.startswith("__"))
    return names


def consumer_imports(root: Path, consumers: set[str]) -> list[str]:
    """Every `file:line import` under `root` that reaches a consumer, lazy imports included."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dotted = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                dotted = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                continue
            for name in dotted:
                parts = name.split(".")
                if len(parts) >= 2 and parts[0] == "cherrypick" and parts[1] in consumers:
                    found.append(f"{path}:{node.lineno} imports {name}")
                    break
    return found


def test_the_consumer_list_is_derived_from_the_package_directories():
    names = consumer_names()
    assert {"meic", "weeklyput", "fundamentals", "orchestrator", "streamer"} <= names
    assert "core" not in names


def test_core_imports_nothing_from_a_consumer():
    assert consumer_imports(CORE_ROOT, consumer_names()) == []


def is_namespace_package(root: Path) -> bool:
    return not (root / "__init__.py").exists()


def test_cherrypick_stays_a_namespace_package():
    assert is_namespace_package(CORE_ROOT)


def test_the_scan_catches_an_import_of_a_consumer(tmp_path):
    (tmp_path / "ok.py").write_text("import json\nfrom cherrypick.core import home\n", encoding="utf-8")
    assert consumer_imports(tmp_path, {"weeklyput"}) == []
    (tmp_path / "bad.py").write_text(
        "def f():\n    from cherrypick.weeklyput import engine\n    return engine\n", encoding="utf-8"
    )
    hits = consumer_imports(tmp_path, {"weeklyput"})
    assert len(hits) == 1 and "bad.py:2" in hits[0]


def test_the_namespace_check_catches_an_init(tmp_path):
    ns = tmp_path / "cherrypick"
    ns.mkdir()
    assert is_namespace_package(ns)
    (ns / "__init__.py").write_text("", encoding="utf-8")
    assert not is_namespace_package(ns)
