"""packages/backtest writes vendor sessions in this recorder's tables, so they replay through the same
readers (core.quotehistory, flies.replay). It may not import gex, so it spells the DDL itself; this
holds its copy equal to the recorder's, from this side (CI runs only changed packages' tests, and
packages/backtest carries the same check from its side)."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from cherrypick.gex import regime, service

BACKTEST_STORE = (
    Path(__file__).resolve().parents[2] / "backtest" / "src" / "cherrypick" / "backtest" / "store.py"
)
TABLES = (
    "gex_spot_history",
    "gex_profile_history",
    "gex_quote_samples",
    "gex_quotes",
    "daily_closes",
    "gex_regime_history",
    "market_regime_history",
)


def _recorder_ddl_of_backtest() -> tuple[str, ...]:
    tree = ast.parse(BACKTEST_STORE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "RECORDER_DDL" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("packages/backtest/store.py no longer declares RECORDER_DDL")


def _columns(conn, table):
    return [(r[1], r[2].upper(), r[3], r[5]) for r in conn.execute(f"PRAGMA table_info({table})")]


def test_the_backtest_store_spells_the_recorder_tables_exactly():
    ours = sqlite3.connect(":memory:")
    service._ensure_history_table(ours)
    regime.ensure_tables(ours)
    theirs = sqlite3.connect(":memory:")
    for stmt in _recorder_ddl_of_backtest():
        theirs.execute(stmt)
    for table in TABLES:
        assert _columns(theirs, table) == _columns(ours, table), table
