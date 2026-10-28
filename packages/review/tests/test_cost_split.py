"""Fact set v10: costs as separate columns -- fees, settlement, slippage with its basis -- beside the
old total, so the review states money the way every other surface does (docs/conventions/money-layout.md)."""

import sqlite3
from datetime import datetime

import pytest

from cherrypick.review import facts, render


def _earnings_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL,"
        " exit_cost REAL, closed_at REAL, opened_at REAL, capital_at_risk REAL,"
        " entry_slippage REAL, exit_slippage REAL, settlement_fees REAL)"
    )
    at = datetime.fromisoformat("2026-08-12 15:00").timestamp()
    conn.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # gross 100; costs 12 = fees 6.50 + settlement 2.00 + slippage 3.50
            ("CSCO", "p", "iron_fly", 100.0, 5.0, 7.0, at, 0, 1000.0, 1.5, 2.0, 2.0),
            # a row with no recorded settlement or slippage: its 4.00 stays in fees
            ("AMAT", "p", "iron_fly", 50.0, 2.0, 2.0, at, 0, 1000.0, None, None, None),
        ],
    )
    conn.commit()
    conn.close()


def test_results_split_cost_into_fees_settlement_and_slippage(tmp_path):
    db = tmp_path / "paper_trades.db"
    _earnings_db(db)
    got = facts.build_module_facts("earnings", "2026-08-12", db_path=db)
    r = got["results"]
    assert r["cost"] == pytest.approx(16.0), "the total stays for v9 readers"
    assert r["fees"] == pytest.approx(10.5)
    assert r["settlement"] == pytest.approx(2.0) and r["settlement_unrecorded"] == 1
    assert r["slippage"] == pytest.approx(3.5) and r["slippage_unrecorded"] == 1
    assert r["slippage_basis"] == "cost" and got["slippage_basis"] == "cost"
    # Rows add up: gross - fees - settlement - charged slippage = net.
    assert r["gross"] - r["fees"] - r["settlement"] - r["slippage"] == pytest.approx(r["net"])
    arm = got["by_arm"]["p"]
    assert arm["fees"] == r["fees"] and arm["settlement"] == r["settlement"]


def test_unrecorded_settlement_is_none_never_zero():
    parts = facts._cost_parts(
        [{"fees": 3.0, "settlement": None, "slippage": None, "slippage_basis": "in_gross"}]
    )
    assert parts["settlement"] is None and parts["settlement_unrecorded"] == 1
    assert parts["slippage"] is None and parts["slippage_basis"] == "in_gross"


def test_every_module_states_its_slippage_basis():
    assert {s: facts._ledgers.SLIPPAGE_BASIS[spec["schema"]] for s, spec in facts.MODULES.items()} == {
        "meic": "in_gross",
        "flies": "in_gross",
        "earnings": "cost",
        "calendars": "cost",
        "pmcc": "cost",
        "bwb": "cost",
        "curve": "cost",
        "contango": "cost",
    }


def test_the_render_shows_the_split_and_says_so_for_an_old_set(monkeypatch):
    new = {
        "modules": {
            "meic": {
                "ok": True,
                "results": {"gross": 10.0, "fees": 2.0, "settlement": None, "slippage": 1.0,
                            "slippage_basis": "in_gross", "net": 8.0},
            }
        }
    }  # fmt: skip
    text = "\n".join(render._money_table(new))
    assert "| meic | 10.00 | 2.00 | n/r | 1.00 (in gross) | 8.00 |" in text
    old = {"modules": {"meic": {"ok": True, "results": {"gross": 10.0, "cost": 2.0, "net": 8.0}}}}
    assert "the split arrived with v10" in "\n".join(render._money_table(old))
