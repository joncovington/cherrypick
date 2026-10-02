"""The separation that gives the desk its reason to exist, asserted rather than documented.

"The desk is armed" and "the automated loops are armed" must stay independent facts. Ways that
could quietly stop being true:

  1. the desk starts reading a module's `enable_live_trading` (so enabling a loop enables the desk);
  2. another package imports the desk, or launches it — `subprocess` on the `cherrypick-desk`
     console script, `python -m cherrypick.desk`, `importlib.import_module("cherrypick.desk...")` —
     so the submit path becomes reachable from scheduled, unattended code.

The package list is derived from `packages/*/` (every package but this one) plus `scripts/`, never a
hand-kept list: a new package is covered the day it lands. Only files named `test_*.py` are skipped
(the old rule skipped any name containing "test", which let `backtest.py` through).

The launch check reads string literals inside the arguments of launch-shaped calls. It cannot follow
a name built at runtime from pieces; that is a limit of a source scan, stated here so nobody reads
more into a green run than it checks. The orchestrator's `desk_notifier.py` reads the desk journal as
a file (`state/desk/journal.jsonl`) and names neither the package nor the script in a call, so it
needs no allow-list entry — it is held to the import rule like everything else.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DESK_SRC = Path(__file__).resolve().parents[1] / "src" / "cherrypick" / "desk"
REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"
SCRIPTS = REPO / "scripts"

SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".git",
    ".tox",
    "build",
    "dist",
    "site-packages",
    ".pytest_cache",
    ".ruff_cache",
}

# "cherrypick-desk" (the console script) or "cherrypick.desk[...]" (the package), but not a longer
# name that merely starts that way, such as the orchestrator's "cherrypick-desk-notify" task.
DESK_NAME = re.compile(r"cherrypick[.-]desk(?![-\w])")

# Call names that start a process, import a module by name, or look up a console script.
LAUNCH_CALLS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
    "system",
    "popen",
    "startfile",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "import_module",
    "__import__",
    "find_spec",
    "run_module",
    "run_path",
    "resolve_name",
    "which",
    *(f"exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
    *(f"spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
}


# --------------------------------------------------------------------------- the scanner
def automated_roots(packages: Path = PACKAGES, scripts: Path = SCRIPTS) -> list[Path]:
    roots = [
        p
        for p in sorted(packages.iterdir())
        if p.is_dir() and p.name != "desk" and not p.name.startswith(".")
    ]
    if scripts.is_dir():
        roots.append(scripts)
    return roots


def scanned_files(roots: list[Path]):
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if path.name.startswith("test_"):
                continue
            yield root, path


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def offenders_in(path: Path, label: str) -> list[str]:
    tree = _parse(path)
    if tree is None:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            is_desk = module == "cherrypick.desk" or module.startswith("cherrypick.desk.")
            if is_desk or (module == "cherrypick" and "desk" in names):
                out.append(f"{label}:{node.lineno}: imports cherrypick.desk")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cherrypick.desk" or alias.name.startswith("cherrypick.desk."):
                    out.append(f"{label}:{node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.Call) and _call_name(node.func) in LAUNCH_CALLS:
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                for sub in ast.walk(arg):
                    if (
                        isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and DESK_NAME.search(sub.value)
                    ):
                        out.append(f"{label}:{node.lineno}: {_call_name(node.func)}({sub.value!r})")
    return out


def scan(roots: list[Path]) -> list[str]:
    out = []
    for root, path in scanned_files(roots):
        out += offenders_in(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return out


# --------------------------------------------------------------------------- the real assertions
def _desk_sources():
    return sorted(DESK_SRC.rglob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id()s of the string constants that are docstrings, so prose ABOUT a flag is not mistaken for
    a read OF it — these modules deliberately explain what they refuse to touch."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    out.add(id(body[0].value))
    return out


def test_the_desk_never_reads_a_module_live_trading_flag():
    needles = {"enable_live_trading", "account_deploy_limit_pct", "gate0_confirmed"}
    offenders = []
    for path in _desk_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if node.value in needles:
                    offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
            elif isinstance(node, ast.Attribute) and node.attr in needles:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
    assert not offenders, f"the desk must not read any module's live-trading flag: {offenders}"


def test_the_desk_never_writes_a_module_config():
    offenders = []
    for path in _desk_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in ("write_text", "replace") and "config" in path.name:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"desk wrote to a config path: {offenders}"


def test_the_roots_are_every_other_package_plus_scripts():
    names = {r.name for r in automated_roots()}
    on_disk = {p.name for p in PACKAGES.iterdir() if p.is_dir() and not p.name.startswith(".")}
    assert names == (on_disk - {"desk"}) | {"scripts"}
    assert {"orchestrator", "core", "meic", "flies", "bwb"} <= names  # sanity: this is the real tree


def test_the_scan_actually_reads_files():
    """A scanner that walked nothing would pass forever."""
    files = list(scanned_files(automated_roots()))
    assert len(files) > 100
    assert any(p.name == "desk_notifier.py" for _, p in files)


def test_no_other_package_imports_or_launches_the_desk():
    offenders = scan(automated_roots())
    assert not offenders, (
        "another package imports or launches cherrypick.desk — the manual submit path must stay "
        f"unreachable from unattended code: {offenders}"
    )


def test_the_submit_path_lives_only_in_the_cli():
    """`live=True` must appear in exactly one place: a single auditable line where real money moves."""
    hits = []
    for path in _desk_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "live=True" in line and not line.strip().startswith("#"):
                hits.append(f"{path.name}:{lineno}")
    assert len(hits) == 1, f"expected exactly one live submit site, found {hits}"
    assert hits[0].startswith("cli.py"), f"the live submit site moved out of cli.py: {hits}"


# --------------------------------------------------------------------------- the scanner can fire
# Planted in a temp tree rather than in a real package, so these prove each rule reports what it
# should without editing code other people are working on.
PLANTS = {
    "import": "import cherrypick.desk\n",
    "import_sub": "import cherrypick.desk.cli as c\n",
    "from_import": "from cherrypick.desk import cli\n",
    "from_parent": "from cherrypick import desk\n",
    "subprocess_script": "import subprocess\nsubprocess.run(['cherrypick-desk', 'confirm'])\n",
    "subprocess_module": "import subprocess as sp\nsp.Popen(['python', '-m', 'cherrypick.desk'])\n",
    "shell_string": "import os\nos.system('cherrypick-desk confirm --ticket x')\n",
    "importlib": "import importlib\nimportlib.import_module('cherrypick.desk.cli')\n",
    "dunder_import": "__import__('cherrypick.desk')\n",
    "which": "import shutil\nshutil.which(cmd='cherrypick-desk')\n",
    "asyncio_exec": "import asyncio\nasyncio.create_subprocess_exec('cherrypick-desk', 'propose')\n",
}


def _plant(tmp_path: Path, filename: str, source: str) -> list[Path]:
    packages = tmp_path / "packages"
    (packages / "desk").mkdir(parents=True, exist_ok=True)
    (packages / "desk" / "inside.py").write_text("import cherrypick.desk\n")  # the desk itself is exempt
    pkg = packages / "newpkg" / "src"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / filename).write_text(source)
    return automated_roots(packages, tmp_path / "scripts")


@pytest.mark.parametrize("kind", list(PLANTS))
def test_each_rule_fires_on_a_planted_file(tmp_path, kind):
    roots = _plant(tmp_path, "loop.py", PLANTS[kind])
    offenders = scan(roots)
    assert len(offenders) == 1, offenders
    assert offenders[0].startswith("newpkg/src/loop.py")


def test_a_new_package_is_covered_without_editing_a_list(tmp_path):
    roots = _plant(tmp_path, "loop.py", PLANTS["import"])
    assert [r.name for r in roots] == ["newpkg"]


def test_scripts_are_covered(tmp_path):
    roots = _plant(tmp_path, "clean.py", "x = 1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "arm.py").write_text(PLANTS["subprocess_script"])
    roots = automated_roots(tmp_path / "packages", tmp_path / "scripts")
    assert [o.split(":")[0] for o in scan(roots)] == ["scripts/arm.py"]


@pytest.mark.parametrize(
    "name", ["backtest.py", "contest.py", "testing.py", "conftest.py", "tests_helper.py"]
)
def test_only_test_star_files_are_skipped(tmp_path, name):
    """The old rule skipped any file with "test" in its name, so `backtest.py` was never read."""
    roots = _plant(tmp_path, name, PLANTS["import"])
    assert len(scan(roots)) == 1


def test_test_files_are_skipped(tmp_path):
    roots = _plant(tmp_path, "test_loop.py", PLANTS["import"])
    assert scan(roots) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['cherrypick-desk-notify'])\n",  # a longer, different name
        "x = 'cherrypick-desk'\n",  # a plain string outside a launch call
        '"""This module never imports cherrypick.desk."""\n',  # prose
        "from cherrypick import core\n",
        "import cherrypick.desktop\n",
    ],
)
def test_lookalikes_do_not_fire(tmp_path, source):
    assert scan(_plant(tmp_path, "loop.py", source)) == []
