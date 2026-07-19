"""Tests for the decision journal — why an entry was made or refused, in queryable form.

This is the thing MEIC does not have: it computes equally rich reasons and then collapses them into a
free-text `loop_log.reasoning` blob that has to be regex-scraped and cannot be aggregated. These tests
exist to keep this side honest.
"""

import pytest
from test_engine import BASE_CONFIG, q, snapshot

import analytics
import book as bookmod
import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def config(**defaults):
    return {"defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"], **defaults},
            "arms": {"control": {}}}


def journal(conn, day="2026-07-20"):
    return analytics.decision_journal(conn, day)


# --------------------------------------------------------------------------- collapsing
def test_repeated_refusals_collapse_into_one_counted_run(conn):
    """A gate that blocks all morning is one row saying so, not forty identical rows. The whole point
    is that a barren day reads as a handful of rows that tell the story."""
    for i in range(5):
        dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                              reason="credit_below_floor", center=6000.0, when=f"2026-07-20T09:5{i}:00")
    rows = journal(conn)
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 5
    assert rows[0]["first_seen"].endswith("09:50:00")
    assert rows[0]["last_seen"].endswith("09:54:00")


def test_a_changed_reason_opens_a_new_run(conn):
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="outside_entry_window", when="2026-07-20T09:00:00")
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="credit_below_floor", when="2026-07-20T09:50:00")
    rows = journal(conn)
    assert [r["reason"] for r in rows] == ["credit_below_floor", "outside_entry_window"]
    assert all(r["occurrences"] == 1 for r in rows)


def test_a_reason_that_returns_opens_a_third_run_not_a_merge(conn):
    """Duration matters. A gate that blocked, cleared, then blocked again is three episodes, and
    merging the two blocks would erase the fact that something changed in between."""
    for ts, reason in [("09:00", "credit_below_floor"), ("10:00", "outside_entry_window"),
                       ("11:00", "credit_below_floor")]:
        dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                              reason=reason, when=f"2026-07-20T{ts}:00")
    assert len(journal(conn)) == 3


def test_runs_are_kept_separate_per_arm_and_mode(conn):
    for arm in ("gex", "control"):
        for mode in ("legged", "outright"):
            dbmod.record_decision(conn, trade_date="2026-07-20", arm=arm, symbol="SPX", mode=mode,
                                  reason="max_positions_reached", when="2026-07-20T12:00:00")
    assert len(journal(conn)) == 4


def test_center_drift_is_captured_across_a_run(conn):
    """Spot moves while a gate stays shut, so the run records where the arm wanted to be at each end."""
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="credit_below_floor", center=6000.0, when="2026-07-20T09:50:00")
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="credit_below_floor", center=6015.0, when="2026-07-20T11:20:00")
    row = journal(conn)[0]
    assert row["center_first"] == 6000.0 and row["center_last"] == 6015.0


# --------------------------------------------------------------------------- the accept path
def test_accepted_decisions_never_merge(conn):
    """Two entries back to back are two trades. Collapsing them would lose the count of what was
    actually done, which is the one thing the journal must never do."""
    for i in range(3):
        dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                              reason="entered", accepted=True, position_id=f"P{i}",
                              when="2026-07-20T12:00:00")
    rows = journal(conn)
    assert len(rows) == 3
    assert all(r["accepted"] == 1 for r in rows)


def test_an_acceptance_after_refusals_starts_a_new_row(conn):
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="credit_below_floor", when="2026-07-20T09:50:00")
    dbmod.record_decision(conn, trade_date="2026-07-20", arm="gex", symbol="SPX", mode="legged",
                          reason="entered", accepted=True, position_id="P1",
                          when="2026-07-20T11:25:00")
    rows = journal(conn)
    assert rows[0]["reason"] == "entered" and rows[0]["position_id"] == "P1"
    assert rows[1]["reason"] == "credit_below_floor"


# --------------------------------------------------------------------------- through the real loop
def test_a_live_iteration_writes_both_refusals_and_acceptances(conn):
    cfg = config()
    # First iteration enters; the second is refused because the centre is taken.
    snap = snapshot(underlying_price=5998.0)
    bookmod.process_snapshot(snap, cfg, conn, "control")
    bookmod.process_snapshot(snap, cfg, conn, "control")

    rows = journal(conn)
    reasons = {r["reason"] for r in rows}
    assert "entered" in reasons, "the accept path must be journalled, not just refusals"
    assert "center_already_occupied" in reasons

    entered = next(r for r in rows if r["reason"] == "entered")
    assert entered["position_id"] is not None
    assert "credit" in (entered["detail"] or "")


def test_a_barren_day_still_explains_itself(conn):
    """The failure this feature exists to prevent: a session with no trades that leaves no record of
    why. Every arm must have written a reason."""
    cfg = {"defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]},
           "arms": {"control": {}, "gex": {}}}
    # dte=1 fails the very first gate, so nothing can trade.
    bookmod.process_snapshot(snapshot(dte=1), cfg, conn, "control")
    bookmod.process_snapshot(snapshot(dte=1), cfg, conn, "gex")

    rows = journal(conn)
    assert {r["arm"] for r in rows} == {"control", "gex"}
    assert all(r["reason"] == "no_0dte_expiration" for r in rows)


def test_completion_refusals_record_how_far_off_the_debit_was(conn):
    """A refusal that only said 'too high' would leave you unable to tell a near miss from a mile off."""
    cfg = config()
    bookmod.process_snapshot(snapshot(underlying_price=5998.0), cfg, conn, "control")
    expensive = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(3.9, 4.1)})
    bookmod.process_snapshot(expensive, cfg, conn, "control")

    row = next(r for r in journal(conn) if r["reason"] == "completing_debit_too_high")
    assert "vs gate" in row["detail"]


# --------------------------------------------------------------------------- iterations table
def test_every_arm_records_what_it_wanted_even_when_it_cannot_trade(conn):
    """Divergence is measured over intentions, not fills — so the centre is recorded before any gate
    gets a chance to veto it."""
    cfg = config()
    bookmod.process_snapshot(snapshot(dte=1, underlying_price=5998.0), cfg, conn, "control")
    rows = conn.execute("SELECT * FROM fly_iterations").fetchall()
    assert len(rows) == 1
    assert rows[0]["arm"] == "control" and rows[0]["center"] == 6000.0
