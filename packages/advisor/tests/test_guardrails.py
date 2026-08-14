"""The package's guardrails, enforced by reading the source rather than by trusting prose.

Three claims in CLAUDE.md are only worth as much as their enforcement:

1. **No AI, no network, no broker in this package.** The AI touchpoint is a script outside every
   package; a client library, an API key, or a socket appearing here would quietly move it back
   inside the fence that keeps loops importable-clean.
2. **Every foreign database is opened read-only.** Not "we remembered to pass mode=ro at each call
   site" -- there is exactly one opener, and no module calls `sqlite3.connect` directly.
3. **Live data is read in one file only.** The advisor reads live facts for context; the modules
   that decide and enact must be provably free of them, so a future edit that reaches for a live
   table from `enact.py` fails here instead of in production.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SRC = PACKAGE_ROOT / "src" / "cherrypick" / "advisor"

# The only file allowed to know live data exists. It reads; it never decides or enacts.
LIVE_FACTS_FILE = "factpack.py"

FORBIDDEN_IMPORTS = {
    # broker / credentials
    "tastytrade", "keyring", "cherrypick.core.auth", "cherrypick.core.broker",
    # network
    "requests", "httpx", "urllib.request", "urllib3", "http.client", "socket",
    "websocket", "websockets", "asyncio",
    # AI clients -- the model is invoked by scripts/advisor_checkpoint.py, never from a package
    "anthropic", "openai",
    # this package shells out to nothing: it IS what gets shelled out to
    "subprocess",
}

# Strings that only mean something if you are touching a live account.
LIVE_LITERALS = ("live_trades", "halt-live", "enable_live_trading", "live_db")


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_names(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def test_no_ai_no_network_no_broker_imports():
    offenders = []
    for py in _modules():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for name in _imported_names(tree):
            root = name.split(".")[0]
            if name in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                offenders.append(f"{py.name}: {name}")
    assert not offenders, (
        "this package must never invoke AI, open a socket, or touch broker credentials -- the AI "
        f"touchpoint is scripts/advisor_checkpoint.py, outside every package: {offenders}"
    )


def test_there_is_exactly_one_read_only_opener():
    """No module may call `sqlite3.connect` -- reads go through `store.ro` (which is core's
    `connect_ro`, i.e. `?mode=ro`), writes go through `store.connect`, which only ever opens the
    advisor's own database."""
    offenders = []
    for py in _modules():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3"
            ):
                offenders.append(f"{py.name}:{node.lineno}")
    assert not offenders, (
        "direct sqlite3.connect() opens a foreign database read-WRITE -- use store.ro(): "
        f"{offenders}"
    )


def test_store_ro_really_is_read_only():
    """The one opener is only a guardrail if it is actually `?mode=ro`. Prove it functionally, not
    by reading the source: a write through it must fail."""
    import sqlite3

    import pytest

    from cherrypick.advisor import store

    conn = store.connect()  # the advisor's own db, in the autouse fixture's tmp home
    conn.execute("CREATE TABLE IF NOT EXISTS probe (x INTEGER)")
    conn.commit()
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.close()

    reader = store.ro(db)
    with pytest.raises(sqlite3.OperationalError):
        reader.execute("INSERT INTO probe VALUES (1)")
    reader.close()


def test_live_literals_appear_in_the_fact_pack_only():
    offenders = []
    for py in _modules():
        if py.name == LIVE_FACTS_FILE:
            continue
        text = py.read_text(encoding="utf-8")
        for literal in LIVE_LITERALS:
            if literal in text:
                offenders.append(f"{py.name}: {literal!r}")
    assert not offenders, (
        "live data may be READ for context, in factpack.py only. Deciding and enacting modules "
        f"must be provably free of it: {offenders}"
    )


def test_the_deciding_and_enacting_modules_never_mention_live_anything():
    """A narrower scan than the one above, aimed at the three files that DECIDE: an edit that
    reaches for a live table from `enact.py` should fail here, not in production."""
    offenders = []
    for name in ("enact.py", "experiments.py", "bounds.py", "verdicts.py"):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for literal in (*LIVE_LITERALS, "meic_trades", "earnings_trades"):
                    if literal in node.value:
                        offenders.append(f"{name}:{node.lineno}: {literal!r}")
    assert not offenders, f"a deciding module referenced live data: {offenders}"


def test_every_enact_output_path_is_a_paper_advice_artifact():
    """Functional half of the same claim: run a real enact and check where the bytes landed."""
    import json

    import fakes

    from cherrypick.advisor import enact, experiments, paths, store

    home = paths.state_dir().parent
    fakes.seed_suite(home, "2026-08-13")
    fakes.write_config(home, "meic", fakes.advice_block({"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}))
    fakes.write_suite_config(home, {"enabled": True, "modules": {"meic": {"enabled": True}}})

    conn = store.connect()
    experiments.admit_spec(conn, session="2026-08-13", module="meic",
                           params={"stop_trigger_ratio": 0.9})
    result = enact.run(conn, "2026-08-13")
    conn.close()

    written = [m["path"] for m in result["enacted"] if m.get("written")]
    assert written, "nothing was written — the check would pass vacuously"
    for path in written:
        rel = Path(path).relative_to(paths.state_dir())
        assert rel.parts[0] == "advice"
        assert rel.name.split("-")[0] in ("meic", "flies", "earnings")
        assert json.loads(Path(path).read_text(encoding="utf-8"))["module"] in (
            "meic", "flies", "earnings")


def test_the_package_declares_only_core_as_a_dependency():
    """A dependency list is the other place an API key or an HTTP client sneaks in."""
    pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    body = pyproject.split("[project.optional-dependencies]")[0]
    declared = body.split("dependencies = ")[1].split("]")[0]
    assert "cherrypick-core" in declared
    assert declared.count('"') == 2, f"only cherrypick-core may be a runtime dependency: {declared}"
