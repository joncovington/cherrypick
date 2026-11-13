"""A short call the name's shares cover is a covered call, never a naked one.

Under order-chain grouping the shares and the call are two chains, and the call alone read as a naked
call: the credit stop and the breached close said buy it back at a loss the shares had already gained,
and its loss was called unbounded. Synthetic histories, as in test_wheel.py.
"""

from test_wheel import assigned, occ, opt_leg, share_leg, tx
from test_wheel_rules import ASSIGNED, evaluate, fired, trade_of

from cherrypick.manage import evaluate as ev

CALL = occ("2026-11-20", "C", 55)
BOUGHT = [
    tx(1, "2026-10-01T15:00:00Z", "Buy to Open", "XYZ", 100, 50.0, kind="Equity"),
    tx(2, "2026-10-01T15:01:00Z", "Sell to Open", CALL, 1, 0.80),
]


def test_a_call_the_shares_cover_is_a_covered_call_and_its_call_away_is_reviewed_not_stopped():
    # Shares at 50, the 55 call sold for 0.80; the stock at 58 is through it.
    acct = evaluate(BOUGHT, [share_leg(100, 58.0, 50.0), opt_leg(CALL, -1, 3.40, 0.80)], 58.0)
    t = trade_of(acct, CALL)
    assert t["shape"] == "covered call"
    names = fired(t)
    assert not {"credit.stop", "credit.breached", "risk.oversize"} & set(names)
    f = names["covered_call.assigned"]
    assert f["action"] == ev.REVIEW and f["confidence"] == 75
    # Shares with a call written against them are a wheel cycle too: its basis counts the premium.
    assert "49.22 wheel basis" in f["reason"] and "$578 over it" in f["reason"]
    assert t["recommendation"]["rule"] == "covered_call.assigned"
    assert t["covered"]["basis_source"] == "wheel basis" and t["covered"]["share_cost"] == 50.0


def test_without_shares_the_same_call_is_still_naked():
    # The guard shown failing: the same call with no shares behind it keeps the credit rules.
    acct = evaluate(BOUGHT[1:], [opt_leg(CALL, -1, 3.40, 0.80)], 58.0)
    t = trade_of(acct, CALL)
    assert t["shape"] == "naked call" and t["covered"] is None
    assert "credit.stop" in fired(t)


def test_partial_cover_covers_nothing():
    history = [
        tx(1, "2026-10-01T15:00:00Z", "Buy to Open", "XYZ", 50, 50.0, kind="Equity"),
        BOUGHT[1],
    ]
    t = trade_of(evaluate(history, [share_leg(50, 58.0, 50.0), opt_leg(CALL, -1, 3.40, 0.80)], 58.0), CALL)
    assert t["covered"] is None and "credit.stop" in fired(t)


def test_a_call_under_the_wheel_basis_is_rolled_or_closed_and_the_wheels_roll_stands():
    # Assigned at 50 for a 1.00 put, then a 46 call for 1.50: the wheel basis is about 47.5. The 46
    # call is under it.
    low = occ("2026-11-20", "C", 46)
    history = [*ASSIGNED, tx(5, "2026-10-10T15:00:00Z", "Sell to Open", low, 1, 1.50)]
    t = trade_of(evaluate(history, [share_leg(100, 52.0, 50.0), opt_leg(low, -1, 6.20, 1.50)], 52.0), low)
    assert t["covered"]["basis_source"] == "wheel basis" and 47 < t["covered"]["basis"] < 48
    names = fired(t)
    assert names["covered_call.assigned"]["action"] == ev.ROLL_OR_CLOSE
    assert "under it" in names["covered_call.assigned"]["reason"]
    assert "wheel.tested" in names
    assert not {"credit.stop", "credit.breached"} & set(names)


def test_a_call_over_the_wheel_basis_is_the_cycle_working_and_the_wheels_repair_roll_stands_aside():
    high = occ("2026-11-20", "C", 50)
    history = [*ASSIGNED, tx(5, "2026-10-10T15:00:00Z", "Sell to Open", high, 1, 1.20)]
    t = trade_of(evaluate(history, [share_leg(100, 53.0, 50.0), opt_leg(high, -1, 3.30, 1.20)], 53.0), high)
    names = fired(t)
    assert names["covered_call.assigned"]["action"] == ev.REVIEW
    assert "wheel.tested" not in names


def test_with_no_known_basis_the_review_says_so():
    # Shares the history never bought: no chain, no wheel cycle, no basis (never the open price).
    t = trade_of(
        evaluate(BOUGHT[1:], [share_leg(100, 58.0, 50.0), opt_leg(CALL, -1, 3.40, 0.80)], 58.0), CALL
    )
    f = fired(t)["covered_call.assigned"]
    assert f["action"] == ev.REVIEW and "basis is unknown" in f["reason"]
    assert t["covered"]["basis"] is None


def test_a_covered_call_is_held_into_expiry():
    near = occ("2026-10-15", "C", 55)  # 2 days out on the 2026-10-13 session
    history = [BOUGHT[0], tx(2, "2026-09-20T15:01:00Z", "Sell to Open", near, 1, 0.80)]
    t = trade_of(evaluate(history, [share_leg(100, 54.0, 50.0), opt_leg(near, -1, 0.10, 0.80)], 54.0), near)
    assert "expiry" not in fired(t)
    assert t["covered"]["held_into_expiry"] is True


def test_a_losing_covered_call_is_not_bought_back_at_its_time_exit_and_a_winner_still_rolls():
    mid = occ("2026-10-30", "C", 55)  # 17 days out, sold 35 days before its expiry
    sold = tx(2, "2026-09-25T15:01:00Z", "Sell to Open", mid, 1, 0.80)
    loser = trade_of(
        evaluate([BOUGHT[0], sold], [share_leg(100, 54.0, 50.0), opt_leg(mid, -1, 1.40, 0.80)], 54.0), mid
    )
    assert "credit.time_exit" not in fired(loser)
    winner = trade_of(
        evaluate([BOUGHT[0], sold], [share_leg(100, 52.0, 50.0), opt_leg(mid, -1, 0.30, 0.80)], 52.0), mid
    )
    assert fired(winner)["credit.time_exit"]["action"] == ev.ROLL_OR_CLOSE


def test_the_switch_turns_the_call_away_review_off_but_coverage_stands():
    acct = evaluate(
        BOUGHT,
        [share_leg(100, 58.0, 50.0), opt_leg(CALL, -1, 3.40, 0.80)],
        58.0,
        overrides={"covered_call": {"assigned": {"enabled": False}}},
    )
    t = trade_of(acct, CALL)
    assert "covered_call.assigned" not in fired(t) and "credit.stop" not in fired(t)


def test_an_assigned_put_delivers_shares_the_call_is_then_covered_by():
    # The fixture the next-leg tests use, with a call written: covered, so no naked-call stop.
    history = [*ASSIGNED, tx(5, "2026-10-10T15:00:00Z", "Sell to Open", CALL, 1, 0.50)]
    t = trade_of(evaluate(history, [share_leg(100, 49.0, 50.0), opt_leg(CALL, -1, 0.40, 0.50)], 49.0), CALL)
    assert t["covered"] is not None and t["recommendation"]["action"] == ev.HOLD
    assert assigned  # imported for the fixture's provenance
