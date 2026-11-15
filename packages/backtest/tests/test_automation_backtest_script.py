"""The automation backtest script (packages/backtest/scripts/automation_backtest.py): which store a run reads,
that the vendor's is refused once its licence ends, and where a result goes. The replay itself is
`cherrypick.ensemble.driver`, held to the paper loop by ensemble's own tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from cherrypick.core import sources as src

from cherrypick.backtest import paths

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("automation_backtest", SCRIPTS / "automation_backtest.py")
ab = importlib.util.module_from_spec(_spec)
sys.modules["automation_backtest"] = ab
_spec.loader.exec_module(ab)

SPAN = {"first": "2022-05-16", "last": "2026-11-13", "sessions": 1131}
INV = {
    "sources": {
        "recorded-0dte": {"symbols": {"SPX": {"first": "2026-10-13", "last": "2026-11-13", "sessions": 24}}},
        "vendor-0dte-spx-60s": {"store": SPAN},
    }
}


def test_the_finest_covering_store_is_chosen_and_the_vendors_only_while_licensed():
    recent = ab.choose_source("SPX", "2026-10-13", "2026-11-13", None, licensed_ok=True, inv=INV)
    assert recent.name == "recorded-0dte", "our recording, the finer cadence, covers it"
    older = ab.choose_source("SPX", "2023-01-03", "2023-12-29", None, licensed_ok=True, inv=INV)
    assert older.name == "vendor-0dte-spx-60s"
    with pytest.raises(ab.Refused, match="no store covers SPX 0DTE chains 2023-01-03..2023-12-29"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", None, licensed_ok=False, inv=INV)
    with pytest.raises(ab.Refused, match="licence does not allow new work"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", "vendor-0dte-spx-60s", licensed_ok=False, inv=INV)
    with pytest.raises(ab.Refused, match="not a same-day chain store"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", "dolt-chains", licensed_ok=True, inv=INV)


def test_a_store_path_follows_its_source_and_a_result_follows_its_licence(managed_home):
    from cherrypick.backtest import settings

    s = settings.resolve({"enabled": True})
    assert ab.store_path(src.by_name("recorded-0dte"), s).as_posix().endswith("data/gex/gex_history.db")
    assert ab.store_path(src.by_name("vendor-0dte-spx-60s"), s) == paths.vendor_dir(s) / "SPX-60s.db"
    assert paths.automations_dir(True) == paths.results_dir() / "automations"
    assert paths.automations_dir(False) == paths.data_dir() / "automations"
