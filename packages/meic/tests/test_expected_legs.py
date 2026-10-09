"""`live_loop --expected-legs`: the contracts the MEIC live ledger says the broker holds, for the
orchestrator's positions-vs-ledger check (2026-10-08)."""

from __future__ import annotations

import sqlite3

import pytest

from cherrypick.meic import db, live_loop

DAY = "2026-10-08"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = str(tmp_path / "meic_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    return path


def _add(path, oid, status, put_stop=None, call_stop=None, qty=1):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO ic_trades (ic_order_id, trade_date, symbol, expiration, status, put_strike, call_strike,"
        " wing_width, quantity, put_stop_cost, call_stop_cost, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            oid,
            DAY,
            "XSP",
            DAY,
            status,
            650.0,
            670.0,
            5.0,
            qty,
            put_stop,
            call_stop,
            f"{DAY}T10:00:00",
            f"{DAY}T10:00:00",
        ),
    )
    con.commit()
    con.close()


def _net(out):
    net = {}
    for leg in out["legs"]:
        key = (leg["expiry"], leg["right"], leg["strike"])
        net[key] = net.get(key, 0) + leg["qty"]
    return net


def test_an_open_ic_is_four_contracts_and_a_stopped_side_is_gone(ledger):
    _add(ledger, "LIVE-XSP-1", "open", qty=2)
    _add(ledger, "LIVE-XSP-2", "partial", put_stop=1.25)
    out = live_loop.expected_legs({"live": {"symbol": "XSP"}}, ledger)
    assert _net(out) == {
        (DAY, "P", 650.0): -2,
        (DAY, "P", 645.0): 2,
        (DAY, "C", 670.0): -3,
        (DAY, "C", 675.0): 3,
    }
    assert out["underlyings"] == ["XSP"]


def test_a_pending_entry_holds_nothing_and_counts_as_pending(ledger):
    _add(ledger, "LIVE-XSP-3", "pending")
    _add(ledger, "LIVE-XSP-4", "cancelled")
    out = live_loop.expected_legs({}, ledger)
    assert out["legs"] == [] and out["pending"] == 1


def test_no_live_ledger_is_nothing_held(tmp_path):
    out = live_loop.expected_legs({}, str(tmp_path / "absent.db"))
    assert out["ok"] and out["legs"] == []
