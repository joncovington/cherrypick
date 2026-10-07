"""analytics.close_tag_tracker: the closer's tags, valued and classed by how each position ended.

Pinned on the 2026-10-07 shape: live's agent tagged two call verticals that stranded (a close would
have saved most of the loss) and, on 10-06, two that went on to complete (a close gave the fly up);
paper tagged a 7795 call that completed in paper but not on live, from a better modelled entry
credit. The tracker must say which is which, or the closer's record reads as one number.
"""

import sqlite3

from cherrypick.flies import analytics
from cherrypick.flies import db as dbmod

DAY = "2026-10-07"


def _ledger(path: str) -> sqlite3.Connection:
    dbmod.connect(path).close()  # migrate once; the tracker itself only ever reads
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _row(conn, pid, arm, center, *, kind, credit, gross, fees, payoff=None, completed=None, tag=None):
    conn.execute(
        "INSERT INTO fly_positions (position_id, trade_date, arm, symbol, kind, side, center, wing_width, quantity,"
        " credit, gross_pnl, fees, status, expiry_payoff, completed_at, entry_time, close_tag_at,"
        " close_tag_natural, close_tag_mid, close_tag_fees, close_tag_source)"
        " VALUES (?, ?, ?, 'SPX', ?, 'call', ?, 5, 1, ?, ?, ?, 'settled', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid, DAY, arm, kind, center, credit, gross, fees, payoff, completed, f"{DAY}T12:00:00-04:00",
            *(tag or (None, None, None, None, None)),
        ),
    )  # fmt: skip
    conn.commit()


TAG = (f"{DAY}T12:25:00-04:00", 2.40, 2.30, 4.88, "agent")


def test_tags_are_classed_by_how_the_position_ended():
    live = _ledger(dbmod.live_db_path())
    _row(live, "L1", "control", 7795.0, kind="short_vertical", credit=2.30, gross=-270.0, fees=13.44,
         payoff=-5.0, tag=TAG)  # fmt: skip
    _row(live, "L2", "control", 7830.0, kind="fly", credit=2.35, gross=25.0, fees=6.88,
         completed=f"{DAY}T13:00:00-04:00", tag=(TAG[0], 2.60, 2.50, 4.88, "agent"))  # fmt: skip
    _row(live, "L3", "control", 7900.0, kind="short_vertical", credit=2.00, gross=200.0, fees=3.44,
         payoff=0.0, tag=(TAG[0], 1.50, 1.40, 4.88, "agent"))  # fmt: skip
    _row(live, "L4", "control", 7700.0, kind="fly", credit=2.0, gross=25.0, fees=6.88)  # untagged

    (arm,) = analytics.close_tag_tracker({"live": live})["arms"]
    assert (arm["ledger"], arm["arm"], arm["tags"], arm["sessions"]) == ("live", "control", 3, 1)
    outcomes = {c["position_id"]: c["outcome"] for c in arm["closes"]}
    assert outcomes == {"L1": "stranded", "L2": "completed", "L3": "expired_otm"}
    # Closing the strand saves most of its loss; closing the completer and the OTM expiry gives up.
    assert arm["by_outcome"]["stranded"]["saved"] > 0
    assert arm["by_outcome"]["completed"]["saved"] < 0
    assert arm["by_outcome"]["expired_otm"]["saved"] < 0
    assert round(sum(b["saved"] for b in arm["by_outcome"].values()), 2) == arm["saved"]
    assert all("live_twin" not in c for c in arm["closes"])  # live is its own reference


def test_a_paper_completion_is_checked_against_live():
    live = _ledger(dbmod.live_db_path())
    paper = _ledger(dbmod.default_db_path())
    # Same vertical both sides; paper completed it, live did not.
    _row(
        live,
        "L1",
        "control",
        7795.0,
        kind="short_vertical",
        credit=2.30,
        gross=-270.0,
        fees=13.44,
        payoff=-5.0,
    )
    _row(paper, "P1", "intraday-agent", 7795.0, kind="fly", credit=2.375, gross=22.5, fees=21.89,
         completed=f"{DAY}T13:01:00-04:00", tag=TAG)  # fmt: skip
    # A paper completion live never held.
    _row(paper, "P2", "intraday-agent", 7805.0, kind="fly", credit=2.4, gross=212.0, fees=21.89,
         completed=f"{DAY}T15:15:00-04:00", tag=TAG)  # fmt: skip

    (arm,) = analytics.close_tag_tracker({"live": live, "paper": paper})["arms"]
    twins = {c["position_id"]: c["live_twin"] for c in arm["closes"]}
    assert twins == {"P1": "not_completed", "P2": None}


def test_an_arm_that_never_tagged_is_absent():
    paper = _ledger(dbmod.default_db_path())
    _row(paper, "P1", "control", 7795.0, kind="fly", credit=2.4, gross=25.0, fees=6.88)
    assert analytics.close_tag_tracker({"paper": paper, "live": None}) == {"arms": []}
