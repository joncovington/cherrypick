"""No package in this monorepo may import an AI client. The model is invoked by scripts outside
every package (`scripts/advisor_checkpoint.py`, `scripts/eod_narrative.py`,
`scripts/morning_narrative.py`), so a failed call can never reach a loop, a ledger or a report.

`packages/advisor/tests/test_guardrails.py` enforces this for the advisor alone, and the advisor
is the package least likely to need it: the trading loops are where an SDK import would do damage.
This scans every package's source tree and every package's declared dependencies (2026-09-12).

Verified to fail: `test_the_scan_catches_an_injected_import` plants each forbidden import in a
throwaway tree and asserts the scanner names it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"

# Module names whose import means "a model can be called from here". Prefix-matched on the dotted
# name, so `langchain_openai` and `google.generativeai` are caught by their roots.
AI_CLIENTS = (
    "anthropic",
    "openai",
    "claude_agent_sdk",
    "claude_code_sdk",
    "litellm",
    "langchain",
    "google.generativeai",
    "google.genai",
    "ollama",
    "mistralai",
    "cohere",
    "groq",
)


def source_roots() -> list[Path]:
    """Every package's importable source tree: `src/` where a package uses src-layout, and the
    flat `cherrypick/` tree for core. Tests and scripts are not packages and are not scanned."""
    roots = []
    for pkg in sorted(p for p in PACKAGES.iterdir() if p.is_dir()):
        if (pkg / "src").is_dir():
            roots.append(pkg / "src")
        elif (pkg / "cherrypick").is_dir():
            roots.append(pkg / "cherrypick")
    return roots


def _matches(dotted: str) -> bool:
    return any(dotted == c or dotted.startswith(c + ".") for c in AI_CLIENTS)


def imports_in(path: Path) -> list[str]:
    """Every imported dotted name in one file, including `from x import y` (as `x.y`) and inside
    functions -- a lazy import is still an import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def scan(roots: list[Path]) -> list[str]:
    """`"<file>: <import>"` for every AI-client import under `roots`."""
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for name in imports_in(path):
                if _matches(name):
                    shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
                    hits.append(f"{shown}: {name}")
    return hits


def declared_dependencies(pyproject: Path) -> list[str]:
    """Dependency names from a pyproject's `dependencies = [...]` and optional-dependency lists,
    lower-cased, extras and version pins stripped."""
    text = pyproject.read_text(encoding="utf-8")
    names = []
    for block in re.findall(r"=\s*\[(.*?)\]", text, flags=re.S):
        for item in re.findall(r"[\"']([^\"']+)[\"']", block):
            names.append(re.split(r"[\[<>=!~; ]", item.strip(), maxsplit=1)[0].lower())
    return names


def test_no_package_source_imports_an_ai_client():
    hits = scan(source_roots())
    assert hits == [], "an AI client is reachable from inside a package:\n" + "\n".join(hits)


def test_no_package_declares_an_ai_client_dependency():
    offenders = []
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        for name in declared_dependencies(pyproject):
            if _matches(name.replace("-", "_")):
                offenders.append(f"{pyproject.relative_to(REPO)}: {name}")
    assert offenders == [], "\n".join(offenders)


@pytest.mark.parametrize("client", AI_CLIENTS)
def test_the_scan_catches_an_injected_import(tmp_path, client):
    """The guard shown to fail: each forbidden import, planted top-level and lazily, is named."""
    pkg = tmp_path / "src" / "cherrypick" / "planted"
    pkg.mkdir(parents=True)
    (pkg / "top.py").write_text(f"import {client}\n", encoding="utf-8")
    (pkg / "lazy.py").write_text(
        f"def call():\n    from {client} import Client\n    return Client\n", encoding="utf-8"
    )
    hits = scan([tmp_path / "src"])
    assert len(hits) == 3, hits  # `import x`, `from x import Client` yields x and x.Client
    assert all(client in h for h in hits)


def test_the_scan_covers_every_package():
    """A package with neither layout would be silently unscanned; say so instead."""
    scanned = {r.parent.name if r.name == "src" else r.parent.name for r in source_roots()}
    every = {p.name for p in PACKAGES.iterdir() if p.is_dir() and (p / "pyproject.toml").exists()}
    assert every <= scanned, f"unscanned packages: {sorted(every - scanned)}"
