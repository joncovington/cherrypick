"""The separation that gives the desk its reason to exist, asserted rather than documented.

The whole point of a separate package is that "the desk is armed" and "the automated loops are
armed" are independent facts. Two ways that could quietly stop being true:

  1. the desk starts reading a module's `enable_live_trading` (so enabling a loop enables the desk), or
  2. a module's loop starts importing the desk (so the desk's submit path becomes reachable from
     scheduled, unattended code).

Both are source-scanned here, in the same spirit as the orchestrator's `test_headless.py` — a rule
that only lives in prose is a rule that drifts.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DESK_SRC = Path(__file__).resolve().parents[1] / "src" / "cherrypick" / "desk"
PACKAGES = Path(__file__).resolve().parents[2]

# Every package whose code runs unattended (scheduled tasks, watchdog ticks, trading loops).
AUTOMATED_PACKAGES = ("orchestrator", "meic", "earnings", "flies", "gex", "streamer", "core")


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
    """Authorization for a desk order comes from desk.json + the PIN + the ticket. If the desk ever
    consulted a module's flag, enabling that module's automated loop would silently widen what the
    desk may do — the exact coupling this package was built to remove.

    Checked against real string literals in the AST (a config key is always a literal at the point
    it is read), so the modules can keep documenting the separation in prose without tripping it.
    """
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
    assert not offenders, (
        f"the desk must not read any module's live-trading flag — its authorization is its own: {offenders}"
    )


def test_the_desk_never_writes_a_module_config():
    """It reads its own desk.json and nothing else. A desk that could edit meic.json would be a
    strictly worse version of the flag-flipping it replaces."""
    offenders = []
    for path in _desk_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in ("write_text", "replace") and "config" in path.name:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"desk wrote to a config path: {offenders}"


def test_no_automated_package_imports_the_desk():
    """The submit path must never be reachable from scheduled, unattended code. A loop that imported
    the desk could call `_submit` on its own schedule, with no human and no ticket."""
    offenders = []
    for pkg in AUTOMATED_PACKAGES:
        root = PACKAGES / pkg
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or "test" in path.name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cherrypick.desk"):
                    offenders.append(f"{pkg}/{path.name}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("cherrypick.desk"):
                            offenders.append(f"{pkg}/{path.name}:{node.lineno}")
    assert not offenders, (
        "an automated package imports cherrypick.desk — the manual submit path must stay "
        f"unreachable from unattended code: {offenders}"
    )


def test_the_submit_path_lives_only_in_the_cli():
    """`live=True` must appear in exactly one place, so there is a single auditable line where real
    money can move — mirroring core.broker's own 'a live order is placed on exactly one path'."""
    hits = []
    for path in _desk_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "live=True" in line and not line.strip().startswith("#"):
                hits.append(f"{path.name}:{lineno}")
    assert hits == ["cli.py:" + hits[0].split(":")[1]] if hits else True
    assert len(hits) == 1, f"expected exactly one live submit site, found {hits}"
    assert hits[0].startswith("cli.py"), f"the live submit site moved out of cli.py: {hits}"
