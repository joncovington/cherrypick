"""The MEIC stop-overshoot comparison (scripts/meic_stop_overshoot.py): a stopped side's cost to close
over its trigger, read from a ledger, and its distribution. Invented ledgers only."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_stop_overshoot", SCRIPTS / "meic_stop_overshoot.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


def ledger(rows, legs, current=True):
    conn = sqlite3.connect(":memory:")
    trig = ", stop_trigger_current REAL" if current else ""
    conn.execute(
        "CREATE TABLE ic_trades (ic_order_id TEXT, arm TEXT, entry_time TEXT, net_credit REAL, "
        f"stop_trigger_original REAL{trig}, put_stop_cost REAL, call_stop_cost REAL)"
    )
    conn.execute("CREATE TABLE ic_spread_legs (ic_order_id TEXT, side TEXT, exit_reason TEXT)")
    n = 8 if current else 7
    conn.executemany(f"INSERT INTO ic_trades VALUES ({', '.join('?' * n)})", rows)
    conn.executemany("INSERT INTO ic_spread_legs VALUES (?,?,?)", legs)
    return conn


def test_a_stopped_sides_overshoot_reads_the_trigger_in_force():
    conn = ledger(
        [
            # credit 2.00, ratio 1.0 moved to 1.2: the trigger in force is 2.40; the call cost 3.00.
            ("a", "pm-200", "2026-10-29 14:00", 2.0, 1.0, 1.2, None, 3.0),
            ("b", "pm-200", "2026-10-30 14:00", 2.0, 1.0, None, 2.2, None),
        ],
        [("a", "call", "per_side_stop"), ("b", "put", "per_side_stop"), ("b", "call", "expired_settlement")],
    )
    rows = st.overshoots(conn)
    assert sorted((r["side"], round(r["overshoot"], 3)) for r in rows) == [("call", 1.25), ("put", 1.1)]
    assert rows[0]["day"] in {"2026-10-29", "2026-10-30"}


def test_an_older_ledger_without_a_current_trigger_reads_the_original():
    conn = ledger(
        [("a", "x", "2026-10-29 14:00", 2.0, 1.0, None, 2.5)],
        [("a", "call", "per_side_stop")],
        current=False,
    )
    assert [round(r["overshoot"], 3) for r in st.overshoots(conn)] == [1.25]


def test_one_arm_is_kept_when_asked_and_a_side_without_a_cost_is_left_out():
    conn = ledger(
        [("a", "keep", None, 2.0, 1.0, None, 2.4, None), ("b", "drop", None, 2.0, 1.0, None, 2.6, None)],
        [("a", "put", "per_side_stop"), ("b", "put", "per_side_stop"), ("a", "call", "per_side_stop")],
    )
    assert [r["arm"] for r in st.overshoots(conn, "keep")] == ["keep"]


def test_a_ledger_without_stop_columns_reads_empty():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ic_trades (ic_order_id TEXT, net_credit REAL)")
    assert st.overshoots(conn) == []


def test_the_summary_counts_quantiles_and_fills_under_the_trigger():
    rows = [
        {"overshoot": x, "day": d}
        for x, d in ((1.0, "2026-10-01"), (1.1, "2026-10-02"), (0.98, "2026-10-03"))
    ]
    s = st.summary(rows)
    assert s["n"] == 3 and s["p50"] == 1.0 and s["under_trigger"] == 1
    assert s["span"] == ["2026-10-01", "2026-10-03"]
    assert st.summary([]) == {"n": 0}
