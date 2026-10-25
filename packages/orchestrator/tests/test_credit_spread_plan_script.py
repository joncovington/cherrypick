"""The credit-spread planner's item specs (`scripts/credit_spread_plan.py`): item 14 stays as declared, item
17 is its own.

Item 14's plan has already been fetched and run, so its spec must never drift; item 17's is what
docs/edge-hypotheses-plan.md declares. The universe rule is checked on a scratch store.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "credit_spread_plan.py"


def _module():
    spec = importlib.util.spec_from_file_location("credit_spread_plan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


csp = _module()


def test_item_14_is_the_declared_spec_unchanged():
    s = csp.ITEMS[14]
    assert s["seed"] == 20261020 and s["floor"] == 300e6
    assert s["selectors"] == {
        "A": (("reversion", "put"),),
        "C": (("pullback-short", "call"), ("reversion-short", "call")),
    }
    assert s["reference"] == ("SPY", "QQQ", "IWM") and s["exclude_leveraged"] is False


def test_item_17_is_every_setup_and_its_mirror_on_the_100m_universe():
    s = csp.ITEMS[17]
    assert s["floor"] == 100e6 and s["reference"] == () and s["exclude_leveraged"] is True
    assert s["seed"] != csp.ITEMS[14]["seed"]
    sides = {sel: specs for sel, specs in s["selectors"].items()}
    assert len(sides) == 8
    for setup in ("trend", "pullback", "reversion", "breakout"):
        assert sides[setup] == ((setup, "put"),)
        assert sides[f"{setup}-short"] == ((f"{setup}-short", "call"),)


def test_the_universe_is_the_floor_less_the_declared_leveraged_funds(tmp_path):
    db = tmp_path / "history.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE peaks (symbol TEXT PRIMARY KEY, max_dollar_volume REAL)")
    c.executemany(
        "INSERT INTO peaks VALUES (?, ?)",
        [("AAPL", 5e9), ("MID", 150e6), ("SMALL", 50e6), ("TQQQ", 3e9), ("SQQQ", 2e9)],
    )
    c.commit()
    c.close()
    lev = {"TQQQ", "SQQQ"}
    assert csp.universe_names(csp.ITEMS[17], db, lev) == ["AAPL", "MID"]
    # Item 14: $300M, funds kept, the reference added whether or not it is in the store.
    assert csp.universe_names(csp.ITEMS[14], db, lev) == ["AAPL", "IWM", "QQQ", "SPY", "SQQQ", "TQQQ"]
