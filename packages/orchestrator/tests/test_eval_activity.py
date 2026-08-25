"""Eval-activity health: the four WARN triggers and the per-schema readers.

The load-bearing property: rejecting every candidate is HEALTHY (a legit gate, e.g. MEIC on
regime_gex_negative), so assess() must NOT warn on a quiet gate-blocked day — only when the loop stopped
evaluating, evaluated nothing, is error-dominated, or won't enter for a reason that isn't a known gate.
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from cherrypick.orchestrator import eval_activity as ea

pytestmark = pytest.mark.unit

# packages/orchestrator/tests -> packages/meic/src/cherrypick/meic/paper.py
_MEIC_PAPER_PY = Path(__file__).resolve().parents[2] / "meic" / "src" / "cherrypick" / "meic" / "paper.py"


def _assess(**over):
    act = {
        "iterations": 10,
        "evaluated": 50,
        "errors": 2,
        "entries": 1,
        "last_age_min": 3.0,
        "top_reason": "regime_gex_negative",
    }
    act.update(over)
    return ea.assess(act, window_min=30, eval_stale_min=10, error_frac_warn=0.5)


# --------------------------------------------------------------------------- assess() triggers
def test_gate_rejection_is_healthy():
    assert _assess(entries=0, top_reason="regime_gex_negative")[0] == ea.OK  # rejecting all on a gate
    assert _assess(entries=2)[0] == ea.OK  # actually entering


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
    con.execute(
        "CREATE TABLE loop_log(id INTEGER PRIMARY KEY, loop_time TEXT, loop_date TEXT, "
        "reasoning TEXT, mcp_errors TEXT)"
    )
    con.execute("CREATE TABLE ic_trades(id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT)")
    reasoning = (
        "SPX(ivr 0.5): all regime_gex_negative  RUT: ERROR no price  SPY(ivr 0.5): all regime_gex_negative"
    )
    con.execute(
        "INSERT INTO loop_log(loop_time, loop_date, reasoning, mcp_errors) VALUES (?,?,?,?)",
        (_now(), "2026-07-23", reasoning, "[]"),
    )
    con.commit()
    act = ea._meic_activity(con, "2026-07-23", 30)
    assert act["evaluated"] == 2  # SPX + SPY each print "(ivr"
    assert act["errors"] == 1  # RUT ERROR (mcp_errors "[]" is not an error)
    assert act["entries"] == 0
    assert act["top_reason"] == "regime_gex_negative"
    con.close()


def test_flies_reader_counts_ok_vs_refused(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE fly_snapshots(id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, status TEXT)"
    )
    con.execute("CREATE TABLE fly_positions(id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT)")
    for st in ("ok", "ok", "no_fresh_quotes"):
        con.execute(
            "INSERT INTO fly_snapshots(iteration_ts, trade_date, status) VALUES (?,?,?)",
            (_now(), "2026-07-23", st),
        )
    con.commit()
    act = ea._flies_activity(con, "2026-07-23", 30)
    assert act["evaluated"] == 2 and act["errors"] == 1 and act["top_reason"] == "no_fresh_quotes"
    con.close()


# --------------------------------------------------------------------------- _BENIGN_REASON coverage
def test_every_meic_reject_reason_is_a_known_gate():
    """The regression this locks in (found 2026-08-05): MEIC's regime gates grew from one
    (regime_gex_*) to four (regime_gex_*, regime_vix_elevated, regime_vix1d_ratio_elevated,
    regime_atr_elevated) and _BENIGN_REASON's old prefix-guessing list was never updated, so three of
    the four deliberately-designed regime pauses false-WARNed as "not a known gate" on any day they
    were the dominant reject reason. Scanning paper.py directly means a fifth gate added later fails
    this test instead of silently reintroducing the same drift."""
    if not _MEIC_PAPER_PY.exists():  # a checkout without the meic package (e.g. a partial clone)
        pytest.skip(f"meic source not found at {_MEIC_PAPER_PY}")
    source = _MEIC_PAPER_PY.read_text(encoding="utf-8")
    reasons = set(re.findall(r'return False, "([a-z0-9_]+)"', source))
    assert reasons, "regex found nothing — paper.py's reject-reason shape changed, update the pattern"
    missing = reasons - ea._BENIGN_REASON
    assert not missing, (
        f"paper.py can return reject reason(s) {sorted(missing)} that eval_activity doesn't "
        "recognize as a known gate — add them to _BENIGN_REASON or eval_activity will false-WARN "
        "on a day one of them is the dominant reason"
    )


# --------------------------------------------------------------- bwb: one entry, then trigger ticks
def _bwb_db(tmp_path, *, snapshot_ts, tick_epochs):
    """A bwb paper DB shaped like a real post-entry session: one snapshot, then trigger ticks."""
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE bwb_snapshots (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, status TEXT);"
        "CREATE TABLE bwb_trigger_ticks (id INTEGER PRIMARY KEY, session_date TEXT, ticked_at REAL);"
        "CREATE TABLE bwb_positions (entry_session TEXT, entry_time TEXT);"
    )
    conn.execute(
        "INSERT INTO bwb_snapshots (ts, trade_date, status) VALUES (?,?,?)",
        (snapshot_ts, "2026-08-25", "ok"),
    )
    for e in tick_epochs:
        # one tick writes a row per open cohort -- duplicated on purpose, they must collapse
        conn.executemany(
            "INSERT INTO bwb_trigger_ticks (session_date, ticked_at) VALUES (?,?)",
            [("2026-08-25", e)] * 4,
        )
    conn.commit()
    return conn


def test_bwb_stays_alive_on_its_trigger_ticks_after_the_one_entry(tmp_path):
    """bwb does NOT ladder, which this reader assumed until 2026-08-25.

    It takes one entry per session (all four books at once), so its snapshot ledger holds a single
    row and goes quiet while the module keeps marking every leg and evaluating its reversal triggers
    each tick. Reading snapshots alone reported "stopped evaluating" from the moment the entry
    filled -- and reported exactly that whether the module was healthy or had died five minutes
    later, making the one failure the check exists to catch indistinguishable from normal operation.
    """
    import time

    now = time.time()
    conn = _bwb_db(
        tmp_path,
        snapshot_ts="2026-08-25T10:00:45-04:00",  # hours stale, as it is every real session
        tick_epochs=[now - 120, now - 60, now - 5],
    )

    act = ea._bwb_activity(conn, "2026-08-25", 30)

    assert act["last_age_min"] < 1.0, "liveness comes from the trigger ticks once the entry is done"
    assert act["iterations"] == 3, (
        "3 distinct ticks collapsed from 12 rows; the morning snapshot is hours stale and correctly "
        "outside the window"
    )
    assert ea.assess(act, window_min=30, eval_stale_min=10, error_frac_warn=0.5)[0] == ea.OK


def test_a_bwb_that_actually_died_after_entering_is_still_caught(tmp_path):
    """The point of the fix is not to silence the check -- it is to make it discriminate."""
    import time

    now = time.time()
    conn = _bwb_db(
        tmp_path,
        snapshot_ts="2026-08-25T10:00:45-04:00",
        tick_epochs=[now - 3600],  # ticked once, an hour ago, then stopped
    )

    act = ea._bwb_activity(conn, "2026-08-25", 30)

    assert act["last_age_min"] > 30
    assert ea.assess(act, window_min=30, eval_stale_min=10, error_frac_warn=0.5)[0] != ea.OK


def test_age_min_accepts_an_epoch(tmp_path):
    """The per-tick ledgers store an epoch; the ISO-only parse returned None, which reads as
    "no data" rather than "could not parse"."""
    import time

    assert ea._age_min(time.time() - 600) == pytest.approx(10.0, abs=0.5)
    assert ea._age_min("not-a-time") is None
    assert ea._age_min(None) is None
