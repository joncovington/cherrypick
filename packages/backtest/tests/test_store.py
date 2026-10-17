"""The store's tables are the gex recorder's, column for column, so a vendor session replays through
the readers built for recorded ones. gex cannot be imported from here (no package imports another but
core), so its DDL is read out of its source and executed; packages/gex carries the mirror check."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from cherrypick.backtest import settings, store

GEX = Path(__file__).resolve().parents[2] / "gex" / "src" / "cherrypick" / "gex"
TABLES = ("gex_spot_history", "gex_profile_history", "gex_quote_samples", "gex_quotes", "daily_closes")


def _literal(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def _ddl_from_source(path: Path, function: str, profile_columns=None) -> list[str]:
    """Every CREATE TABLE statement `function` executes, rebuilt from its string literals. The one
    computed piece (the profile's column list) is rebuilt from the module's own PROFILE_COLUMNS."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    out = []
    for call in ast.walk(fn):
        if not (isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "execute" and call.args):
            continue
        parts = []
        for piece in _flatten(call.args[0]):
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            elif isinstance(piece, ast.Call) and getattr(piece.func, "attr", None) == "join":
                parts.append(", ".join(f"{c} REAL" for c in profile_columns))
            else:
                parts = None
                break
        if parts and "".join(parts).startswith("CREATE TABLE"):
            out.append("".join(parts))
    return out


def _flatten(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        yield from _flatten(node.left)
        yield from _flatten(node.right)
    else:
        yield node


def _columns(conn, table):
    return [(r[1], r[2].upper(), r[3], r[5]) for r in conn.execute(f"PRAGMA table_info({table})")]


def test_the_tables_match_the_recorder_column_for_column():
    service = GEX / "service.py"
    profile_columns = _literal(ast.parse(service.read_text(encoding="utf-8")), "PROFILE_COLUMNS")
    theirs = sqlite3.connect(":memory:")
    for stmt in _ddl_from_source(service, "_ensure_history_table", profile_columns):
        theirs.execute(stmt)
    for stmt in _ddl_from_source(GEX / "regime.py", "ensure_tables"):
        theirs.execute(stmt)
    ours = store.connect(settings.resolve({}), "SPX")
    for table in TABLES:
        assert _columns(ours, table) == _columns(theirs, table), table
    ours.close()


def test_the_store_lives_in_a_marked_directory():
    from cherrypick.backtest import paths

    s = settings.resolve({})
    store.connect(s, "SPX").close()
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()
    assert store.db_path(s, "spx").name == "SPX.db"
