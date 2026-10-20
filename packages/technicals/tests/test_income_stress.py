"""The income run's stress move reads the history store as skate does (scripts/fetch_screener_greeks.py).

Kept here rather than beside the script's other tests: it needs this package's store, which the
orchestrator's test job does not install.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from cherrypick.technicals import store

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_screener_greeks.py"


def _script():
    spec = importlib.util.spec_from_file_location("fetch_screener_greeks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stress_reads_split_adjusted_closes_and_keeps_a_dividend_drop(tmp_path):
    conn = store.connect(tmp_path / "history.db")
    rows = [("BRK.B", d, 100.0, 101.0, 99.0, 100.0, 1e6) for d in ("2026-10-12", "2026-10-13")]
    rows += [("BRK.B", d, 50.0, 50.5, 49.5, c, 2e6) for d, c in (("2026-10-14", 50.0), ("2026-10-15", 49.0))]
    store.upsert_bars(conn, rows)
    store.upsert_splits(conn, [("BRK.B", "2026-10-14", 2.0, 1.0)])
    store.upsert_dividends(conn, [("BRK.B", "2026-10-15", 1.0)])
    conn.commit()
    # The broker's spelling finds the store's; the split is applied and the ex-date drop is kept.
    assert _script().stress_closes(conn, "BRK/B") == [50.0, 50.0, 50.0, 49.0]
