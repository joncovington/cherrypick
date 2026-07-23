"""Eval-activity health: the four WARN triggers and the per-schema readers.

The load-bearing property: rejecting every candidate is HEALTHY (a legit gate, e.g. MEIC on
regime_gex_negative), so assess() must NOT warn on a quiet gate-blocked day — only when the loop stopped
evaluating, evaluated nothing, is error-dominated, or won't enter for a reason that isn't a known gate.
"""

import sqlite3
from datetime import datetime

import pytest

from cherrypick.orchestrator import eval_activity as ea

pytestmark = pytest.mark.unit


def _assess(**over):
    act = {"iterations": 10, "evaluated": 50, "errors": 2, "entries": 1,
           "last_age_min": 3.0, "top_reason": "regime_gex_negative"}
    act.update(over)
    return ea.assess(act, window_min=30, eval_stale_min=10, error_frac_warn=0.5)


# --------------------------------------------------------------------------- assess() triggers
def test_gate_rejection_is_healthy():
    assert _assess(entries=0, top_reason="regime_gex_negative")[0] == ea.OK  # rejecting all on a gate
    assert _assess(entries=2)[0] == ea.OK                                    # actually entering


def test_no_iterations_defers_to_freshness():
    assert _assess(last_age_min=None)[0] == ea.OK  # 'not running at all' is the freshness check's job


def test_stopped_evaluating_warns():
    status, detail = _assess(last_age_min=15)
    assert status == ea.WARN and "stopped evaluating" in detail


def test_iterating_but_evaluating_nothing_warns():
    status, detail = _assess(evaluated=0)
    assert status == ea.WARN and "evaluated nothing" in detail


def test_error_dominated_warns():
    status, detail = _assess(evaluated=4, errors=6)  # 6/10 = 60% >= 50%
    assert status == ea.WARN and "erroring" in detail


def test_zero_entries_for_a_non_gate_reason_warns():
    status, detail = _assess(entries=0, top_reason="broker_disconnected")
    assert status == ea.WARN and "not a known gate" in detail


# --------------------------------------------------------------------------- schema readers
def _now():
    return datetime.now().astimezone().isoformat()


def test_meic_reader_counts_evals_errors_and_reason(tmp_path):
    con = sqlite3.connect(tmp_path / "p.db")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE loop_log(id INTEGER PRIMARY KEY, loop_time TEXT, loop_date TEXT, "
                "reasoning TEXT, mcp_errors TEXT)")
    con.execute("CREATE TABLE ic_trades(id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT)")
    reasoning = ("SPX(ivr 0.5): all regime_gex_negative  RUT: ERROR no price  "
                 "SPY(ivr 0.5): all regime_gex_negative")
    con.execute("INSERT INTO loop_log(loop_time, loop_date, reasoning, mcp_errors) VALUES (?,?,?,?)",
                (_now(), "2026-07-23", reasoning, "[]"))
    con.commit()
    act = ea._meic_activity(con, "2026-07-23", 30)
    assert act["evaluated"] == 2                    # SPX + SPY each print "(ivr"
    assert act["errors"] == 1                       # RUT ERROR (mcp_errors "[]" is not an error)
    assert act["entries"] == 0
    assert act["top_reason"] == "regime_gex_negative"
    con.close()


def test_flies_reader_counts_ok_vs_refused(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE fly_snapshots(id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, "
                "status TEXT)")
    con.execute("CREATE TABLE fly_positions(id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT)")
    for st in ("ok", "ok", "no_fresh_quotes"):
        con.execute("INSERT INTO fly_snapshots(iteration_ts, trade_date, status) VALUES (?,?,?)",
                    (_now(), "2026-07-23", st))
    con.commit()
    act = ea._flies_activity(con, "2026-07-23", 30)
    assert act["evaluated"] == 2 and act["errors"] == 1 and act["top_reason"] == "no_fresh_quotes"
    con.close()
