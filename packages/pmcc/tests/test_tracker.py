"""The tracker's one valuation rule, checked by hand on a held-long position three weeks long.

Every number below is worked out in the comments, so a change to `value_at` that moves any of them is
a change to what a position is worth -- the header, every weekly row, the arms' weekly A/B and the
open mark-to-market all read it.
"""

from datetime import datetime

import pytest

from cherrypick.pmcc import analytics, db, tracker
from cherrypick.pmcc.clock import ET


def _t(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _iso(dt):
    return dt.isoformat()


ENTRY = _t(2026, 8, 24, 11, 0)
ROLL = _t(2026, 9, 4, 15, 0)
NOW = _t(2026, 9, 9, 12, 0).timestamp()
PID = "TQQQ:shield_hold:2026-08-24"


def _leg(role, *, expiration, strike, action, mid, cost, slip, opened, spot, **close):
    return {
        "position_id": PID,
        "leg_role": role,
        "occ_symbol": f"o-{role}",
        "streamer_symbol": f".s-{role}",
        "expiration": expiration,
        "strike": strike,
        "option_type": "call",
        "action": action,
        "quantity": 1,
        "entry_mid": mid,
        "entry_bid": mid - 0.05,
        "entry_ask": mid + 0.05,
        "status": "closed" if close else "open",
        "opened_at": _iso(opened),
        "opened_session": opened.date().isoformat(),
        "entry_spot": spot,
        "entry_cost": cost,
        "entry_slippage": slip,
        **close,
    }


def _mark(conn, role, when, mid, spot, delta):
    conn.execute(
        "INSERT INTO pmcc_marks (position_id, leg_role, marked_at, session_date, mid, delta, spot, usable) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (PID, role, when.timestamp(), when.date().isoformat(), mid, delta, spot),
    )


@pytest.fixture
def ledger(tmp_path):
    """Entry Monday 08-24: long 35 @ 37.80 (fee 1.12, slip 10.00), short 68 @ 3.65 (1.12, 0.63).
    Rolled Friday 09-04 15:00: the 68 bought back at 3.05 (0.14, 0.63) and the 70 sold at 2.45
    (1.12, 0.63). Fees to date: 11.12 + 1.75 + 0.77 + 1.75 = 15.39."""
    conn = db.connect(str(tmp_path / "paper.db"))
    db.save_position(
        conn,
        {
            "position_id": PID,
            "symbol": "TQQQ",
            "arm": "shield_hold",
            "entry_session": "2026-08-24",
            "entry_time": _iso(ENTRY),
            "quantity": 1,
            "long_expiration": "2027-09-17",
            "long_strike": 35.0,
            "short_expiration": "2026-09-11",
            "short_strike": 70.0,
            "entry_spot": 70.60,
            "long_entry_mid": 37.80,
            "entry_long_dte": 389,
            "net_debit": 34.15,
            "status": "open",
            "fees": 15.39,
            "roll_count": 1,
            "era": "shield",
        },
    )
    for leg in (
        _leg(
            "long_call",
            expiration="2027-09-17",
            strike=35.0,
            action="Buy to Open",
            mid=37.80,
            cost=1.12,
            slip=10.00,
            opened=ENTRY,
            spot=70.60,
        ),
        _leg(
            "short_call_1",
            expiration="2026-09-04",
            strike=68.0,
            action="Sell to Open",
            mid=3.65,
            cost=1.12,
            slip=0.63,
            opened=ENTRY,
            spot=70.60,
            closed_at=_iso(ROLL),
            close_kind="rolled",
            close_value=3.05,
            close_spot=71.0,
            close_cost=0.14,
            close_slippage=0.63,
            close_reason="roll:expiry",
        ),
        _leg(
            "short_call_2",
            expiration="2026-09-11",
            strike=70.0,
            action="Sell to Open",
            mid=2.45,
            cost=1.12,
            slip=0.63,
            opened=ROLL,
            spot=71.0,
        ),
    ):
        db.save_leg(conn, leg)
    # Week 35 closes Friday 08-28: long 38.00 (+20), short 68 at 3.40 (+25).
    _mark(conn, "long_call", _t(2026, 8, 28, 15, 59), 38.00, 70.90, 0.94)
    _mark(conn, "short_call_1", _t(2026, 8, 28, 15, 59), 3.40, 70.90, 0.80)
    # Week 36 closes Friday 09-04: long 38.20 (+40), the 68 realised (+60), the 70 at 2.30 (+15).
    _mark(conn, "long_call", _t(2026, 9, 4, 15, 59), 38.20, 71.00, 0.94)
    _mark(conn, "short_call_2", _t(2026, 9, 4, 15, 59), 2.30, 71.00, 0.70)
    # Now, Wednesday 09-09: long 38.50 (+70), the 70 at 2.00 (+45).
    _mark(conn, "long_call", _t(2026, 9, 9, 11, 0), 38.50, 71.40, 0.95)
    _mark(conn, "short_call_2", _t(2026, 9, 9, 11, 0), 2.00, 71.40, 0.74)
    conn.commit()
    return conn


def test_the_header_is_every_leg_at_its_close_or_mark_less_every_cost(ledger):
    t = tracker.tracker(ledger, PID, {}, now=NOW)
    h = t["header"]
    assert (h["long_gain"], h["short_realised"], h["short_open"], h["shares"]) == (70.0, 60.0, 45.0, 0.0)
    assert h["gross"] == 175.0
    # The legs' own cost shares sum to the position's fees: 15.39, split the suite's way.
    # Fees 1.12 + 1.12 + 0.14 + 1.12 = 3.50; slippage 10.00 + 0.63 x 3 = 11.89.
    assert h["costs"] == {"basis": "legs", "fees": 3.50, "slippage": 11.89, "settlement": 0.0, "total": 15.39}
    assert h["net"] == pytest.approx(175.0 - 15.39)
    assert h["return_on_long_cost"] == pytest.approx(round(159.61 / 3780.0, 4))
    assert h["net_delta"] == pytest.approx(95.0 - 74.0)


def test_a_held_long_header_states_its_two_exits_from_the_positions_own_rules(ledger):
    """The stop (net to date at -stop_loss_frac x the long's cost) and the long roll (the long at
    long_close_dte) close a shield position; the page could show neither. Both are read from the
    position's effective params, so an arm that never stops says so with a null."""
    exits = tracker.tracker(ledger, PID, {}, now=NOW)["header"]["exits"]
    assert exits["stop_net_at"] == pytest.approx(-0.30 * 3780.0)
    assert exits["stop_room"] == pytest.approx(175.0 - 15.39 + 0.30 * 3780.0)
    assert exits["long_close_on"] == "2027-08-03"  # 2027-09-17 less 45 days
    assert exits["long_dte"] == 373  # from Wednesday 2026-09-09


def test_every_week_is_valued_at_its_own_close_and_the_last_is_the_header(ledger):
    t = tracker.tracker(ledger, PID, {}, now=NOW)
    weeks = [(w["week"], w["net"], w["change"]) for w in t["weeks"]]
    # W35: +45 gross, entry costs 12.87 -> 32.13. W36: +115 gross, every cost (15.39) -> 99.61.
    assert weeks == [("2026-W35", 32.13, 32.13), ("2026-W36", 99.61, 67.48), ("2026-W37", 159.61, 60.0)]
    assert t["weeks"][-1]["net"] == t["header"]["net"]
    assert t["integrity"]["weeks_without_a_short"] == []


def test_the_short_log_states_each_short_in_the_suites_money_layout(ledger):
    shorts = tracker.tracker(ledger, PID, {}, now=NOW)["shorts"]
    first, second = shorts
    # Signed cash: +365 sold, -305 bought back; entry + exit = gross; gross - fees - slip = net.
    assert (first["entry"], first["exit"], first["gross"]) == (365.0, -305.0, 60.0)
    assert (first["fees"], first["slippage"], first["net"]) == (1.26, 1.26, 57.48)
    assert (first["how"], first["why"]) == ("rolled", "roll:expiry")
    # Extrinsic sold 3.65 - 2.60 = 1.05 -> 105; left at the close 3.05 - 3.00 = 0.05 -> 5.
    assert (first["extrinsic_sold"], first["extrinsic_left"], first["extrinsic_captured"]) == (
        105.0,
        5.0,
        100.0,
    )
    assert second["how"] == "open" and second["exit"] == -200.0  # marked, not traded


def test_the_arms_weekly_ab_and_the_open_mark_read_the_same_valuation(ledger):
    rows = analytics.weekly_by_arm(ledger, era="shield", now=NOW)["rows"]
    assert [(r["week"], r["change"], r["cumulative"]) for r in rows] == [
        ("2026-W35", 32.13, 32.13),
        ("2026-W36", 67.48, 99.61),
        ("2026-W37", 60.0, 159.61),
    ]
    assert analytics.open_mtm(ledger, now=NOW) == {
        "shield_hold": {"TQQQ": {"positions": 1, "net": 159.61, "unpriced": 0}}
    }


def test_an_unmarked_leg_is_unpriced_never_a_partial_sum(ledger):
    ledger.execute("DELETE FROM pmcc_marks WHERE leg_role = 'short_call_2'")
    t = tracker.tracker(ledger, PID, {}, now=NOW)
    assert t["header"]["net"] is None
    assert analytics.open_mtm(ledger, now=NOW)["shield_hold"]["TQQQ"]["net"] is None
    assert t["weeks"][0]["net"] == 32.13  # week 35 never held the 70, so it still prices


def test_a_closed_position_reads_exactly_the_ledgers_own_result(ledger):
    close = _t(2026, 9, 11, 15, 30)
    db.save_leg(
        ledger,
        {
            "position_id": PID,
            "leg_role": "short_call_2",
            "status": "closed",
            "close_kind": "traded",
            "closed_at": _iso(close),
            "close_value": 1.40,
            "close_spot": 71.6,
            "close_cost": 0.14,
            "close_slippage": 0.63,
            "close_reason": "long_roll_due",
        },
    )
    db.save_leg(
        ledger,
        {
            "position_id": PID,
            "leg_role": "long_call",
            "status": "closed",
            "close_kind": "traded",
            "closed_at": _iso(close),
            "close_value": 38.90,
            "close_spot": 71.6,
            "close_cost": 0.14,
            "close_slippage": 10.00,
            "close_reason": "long_roll_due",
        },
    )
    # gross: long +110, the 68 +60, the 70 +105 = 275; fees 15.39 + 10.91 = 26.30.
    db.save_position(
        ledger,
        {
            "position_id": PID,
            "status": "closed",
            "closed_at": _iso(close),
            "closed_session": "2026-09-11",
            "gross_pnl": 275.0,
            "fees": 26.30,
        },
    )
    t = tracker.tracker(ledger, PID, {}, now=NOW + 10 * 86400)
    assert t["header"]["net"] == pytest.approx(275.0 - 26.30)
    assert t["header"]["costs"]["total"] == 26.30
    assert t["weeks"][-1]["net"] == t["header"]["net"]


def test_held_long_excursions_are_sampled_at_each_close(ledger):
    db.save_position(
        ledger,
        {
            "position_id": PID,
            "status": "closed",
            "closed_session": "2026-09-04",
            "gross_pnl": 0.0,
            "fees": 15.39,
        },
    )
    out = analytics.excursions(ledger, era="shield")
    (p,) = out["positions"]
    assert p["sampled"] == "session_close" and p["n"] >= 2


def test_an_unknown_position_is_none(ledger):
    assert tracker.tracker(ledger, "NOPE:control:2026-01-01", {}) is None
