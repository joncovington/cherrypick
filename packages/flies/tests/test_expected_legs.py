"""`live_loop --expected-legs`: the contracts the flies live ledger says the broker holds, for the
orchestrator's positions-vs-ledger check (2026-10-08)."""

from __future__ import annotations

from cherrypick.flies import db as dbmod
from cherrypick.flies import live_loop as ll

DAY = "2026-10-08"


def _save(conn, pid, *, kind="fly", status="open", entry="filled", completion=None, qty=1):
    dbmod.save_position(
        conn,
        {
            "position_id": pid,
            "book_id": f"{DAY}:control:SPX",
            "trade_date": DAY,
            "arm": "control",
            "symbol": "SPX",
            "kind": kind,
            "side": "put",
            "center": 5800.0,
            "wing_width": 5.0,
            "quantity": qty,
            "status": status,
            "entry_fill_status": entry,
            "completion_fill_status": completion,
        },
    )


def _net(out):
    net = {}
    for leg in out["legs"]:
        key = (leg["expiry"], leg["right"], leg["strike"])
        net[key] = net.get(key, 0) + leg["qty"]
    return net


def test_a_completed_fly_is_its_four_contracts_times_quantity(tmp_path):
    conn = dbmod.connect(str(tmp_path / "live.db"))
    _save(conn, "a", qty=2)
    out = ll.expected_legs({"live": {"symbol": "SPX"}}, conn)
    assert _net(out) == {(DAY, "P", 5795.0): 2, (DAY, "P", 5800.0): -4, (DAY, "P", 5805.0): 2}
    assert out["underlyings"] == ["SPX"] and out["pending"] == 0


def test_a_pending_completion_counts_only_the_filled_vertical(tmp_path):
    conn = dbmod.connect(str(tmp_path / "live.db"))
    _save(conn, "a", kind="short_vertical", completion="pending")
    out = ll.expected_legs({}, conn)
    assert _net(out) == {(DAY, "P", 5800.0): -1, (DAY, "P", 5795.0): 1}
    assert out["pending"] == 1


def test_pending_entries_cancelled_and_settled_rows_hold_nothing(tmp_path):
    conn = dbmod.connect(str(tmp_path / "live.db"))
    _save(conn, "p", entry="pending")
    _save(conn, "c", status="cancelled")
    _save(conn, "s", status="settled")
    out = ll.expected_legs({}, conn)
    assert out["legs"] == [] and out["pending"] == 1
