"""`scripts/pmcc_earnings_refresh.py`: the pure plan. The end-to-end runs need the orchestrator's config
editor, which this package does not install, so they live in packages/orchestrator/tests."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "pmcc_earnings_refresh.py"


def _load():
    spec = importlib.util.spec_from_file_location("pmcc_earnings_refresh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_plan_refreshes_stocks_and_leaves_etfs_as_declared():
    mod = _load()
    block = {
        "_note": "doc",
        "XSP": {"kind": "etf", "declared_through": "2099-12-31", "dates": []},
        "AMZN": {"declared_through": "2026-10-01", "dates": ["2026-07-30"], "_note": "kept"},
    }
    new = mod.plan(block, "2026-11-13", {"AMZN": ["2026-10-29"]})
    assert set(new) == {"AMZN"}  # the ETF and the note are untouched
    assert new["AMZN"]["declared_through"] == "2026-11-06"  # a week short of the horizon
    assert new["AMZN"]["dates"] == ["2026-10-29"]
    assert new["AMZN"]["_note"] == "kept"
