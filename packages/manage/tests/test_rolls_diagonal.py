from test_evaluate import fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

SPOT = 360.0


def diagonal(long_open=30.0, long_mid=34.0, long_exp="2027-03-19"):
    """A long call sold against a 2x short call spread that expires Oct 30 (17 DTE on Oct 13)."""
    return [
        leg(1, "C", 350, long_exp, long_open, long_mid),
        leg(-2, "C", 370, "2026-10-30", 6.00, 2.00),
        leg(1, "C", 390, "2026-10-30", 2.00, 0.40),
    ]


def test_a_healthy_diagonal_rolls_its_short_structure_for_a_credit():
    t = run(diagonal(), spot=SPOT)
    rec = t["recommendation"]
    assert rec["action"] == ev.ROLL_FOR_CREDIT and rec["rule"] == "roll.diagonal_short"
    assert "debit.time_exit" in rec["replaces"] and rec["conflicts"] == []
    roll = t["roll"]
    assert roll["from"] == "2026-10-30" and roll["expiration"] == "2026-12-18"
    assert roll["credit"] > 0.10
    # Three legs closed, the same three strikes opened further out; the long is untouched.
    assert len(roll["legs"]) == 4  # BTC 2x370, STC 390, STO 2x370, BTO 390
    assert not any("350C" in row for row in roll["legs"])
    assert "Rather than close the trade, roll the short structure out" in t["explanation"]


def test_a_stopped_diagonal_is_closed_not_rolled():
    t = run(diagonal(long_mid=12.0), spot=SPOT)
    assert t["recommendation"]["action"] == ev.CLOSE
    assert t["roll_blocked"]["reason"] == "stopped"
    assert "repairing a loser" in t["explanation"]


def test_no_roll_when_the_long_would_not_outlast_the_new_short():
    t = run(diagonal(long_exp="2027-01-15"), spot=SPOT)
    assert "roll.diagonal_short" not in fired(t)
    assert t["roll_blocked"]["reason"] == "long_too_short"
    assert "should outlast the new short by 45 days" in t["explanation"]


def test_no_roll_when_the_short_would_sit_inside_the_longs_breakeven():
    # Paid 40 for the 350 long: breakeven 350 + 30 net debit = 380, above the 370 short.
    t = run(diagonal(long_open=40.0, long_mid=44.0), spot=SPOT)
    assert t["roll_blocked"]["reason"] == "breakeven"
    assert t["roll_blocked"]["breakeven"] == 380.0


def test_earnings_are_flagged_by_default_and_block_when_asked():
    flagged = run(diagonal(), spot=SPOT, metrics={"earnings_date": "2026-11-05"})
    assert flagged["recommendation"]["action"] == ev.ROLL_FOR_CREDIT
    assert flagged["roll"]["earnings_date"] == "2026-11-05"
    assert "the long covers it" in flagged["explanation"]
    rules, _ = _rules.effective({"roll": {"diagonal_short": {"skip_through_earnings": True}}})
    blocked = run(diagonal(), spot=SPOT, rules=rules, metrics={"earnings_date": "2026-11-05"})
    assert blocked["roll_blocked"]["reason"] == "earnings"
    assert blocked["recommendation"]["action"] == ev.ROLL_OR_CLOSE


def test_a_spread_with_one_expiry_is_not_a_diagonal():
    t = run([leg(-1, "P", 90, "2026-10-30", 1.00, 0.70), leg(1, "P", 85, "2026-10-30", 0.40, 0.30)])
    assert "roll.diagonal_short" not in fired(t)
    assert t.get("roll_blocked") is None
