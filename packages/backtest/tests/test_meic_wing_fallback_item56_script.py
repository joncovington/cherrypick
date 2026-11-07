"""Hypothesis 56's study (scripts/meic_wing_fallback_item56.py): the fallback rule composed tick by tick
from two once-a-day arms. Invented sessions only.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "meic_wing_fallback_item56", SCRIPTS / "meic_wing_fallback_item56.py"
)
s56 = importlib.util.module_from_spec(_spec)
sys.modules["meic_wing_fallback_item56"] = s56
_spec.loader.exec_module(s56)


def _row(net: float, trades: int = 1) -> dict:
    return {"net": net, "fees": 2.0 if trades else 0.0, "trades": trades}


WIDE = {"d1": _row(50), "d2": _row(-20), "d3": _row(0, 0), "d4": _row(30), "d5": _row(0, 0)}
BASE = {"d1": _row(10), "d2": _row(5), "d3": _row(8), "d4": _row(-40), "d5": _row(0, 0)}
WIDE_AT = {"d1": "14:00", "d2": "14:20", "d4": "14:05"}
BASE_AT = {"d1": "14:00", "d2": "14:01", "d3": "14:02", "d4": "14:10"}


def test_the_width_whose_gates_pass_first_is_taken_the_wide_one_on_a_tie():
    sessions, took = s56.compose(WIDE, BASE, WIDE_AT, BASE_AT)
    assert took == {"d1": "wide", "d2": "base", "d3": "base", "d4": "wide", "d5": "none"}
    # The session's net is the taken entry's, never the better of the two.
    assert sessions["d2"]["net"] == 5 and sessions["d4"]["net"] == 30 and sessions["d1"]["net"] == 50
    # A session neither entered stays a no-entry session at zero.
    assert sessions["d5"] == {"net": 0, "fees": 0.0, "trades": 0}


def test_the_choice_reads_only_entry_times_never_the_outcome():
    # Flip every outcome: the choice of width does not move.
    flipped = {d: _row(-r["net"], r["trades"]) for d, r in WIDE.items()}
    _, took = s56.compose(flipped, BASE, WIDE_AT, BASE_AT)
    _, took0 = s56.compose(WIDE, BASE, WIDE_AT, BASE_AT)
    assert took == took0


def test_counts_name_the_sessions_taken_at_ten_though_the_wide_width_entered_later():
    _, took = s56.compose(WIDE, BASE, WIDE_AT, BASE_AT)
    c = s56.counts(took, WIDE_AT, BASE_AT)
    assert c == {"sessions": 5, "took_wide": 2, "took_base": 2, "no_entry": 1, "base_while_wide_later": 1}


def test_buying_power_is_the_taken_entrys():
    _, took = s56.compose(WIDE, BASE, WIDE_AT, BASE_AT)
    risk = {
        "g15": {"d1": 1200.0, "d2": 1300.0, "d4": 1250.0},
        s56.BASE: {"d1": 850.0, "d2": 860.0, "d3": 870.0, "d4": 880.0},
    }
    assert s56.composed_risk(risk, took, "g15") == {"d1": 1200.0, "d2": 860.0, "d3": 870.0, "d4": 1250.0}


def _ledger(path: Path, rows: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ic_trades (arm TEXT, trade_date TEXT, entry_time TEXT)")
    conn.executemany("INSERT INTO ic_trades VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_entry_times_reads_the_ledgers_and_refuses_two_entries_in_a_session(tmp_path):
    _ledger(
        tmp_path / "a.db", [("g15", "d1", "14:03"), ("live-1400", "d1", "14:00"), ("control", "d1", "09:45")]
    )
    assert s56.entry_times(tmp_path, {"g15", "live-1400"}) == {
        "g15": {"d1": "14:03"},
        "live-1400": {"d1": "14:00"},
    }
    _ledger(tmp_path / "b.db", [("g15", "d1", "14:20")])
    with pytest.raises(SystemExit, match="entered twice"):
        s56.entry_times(tmp_path, {"g15"})
