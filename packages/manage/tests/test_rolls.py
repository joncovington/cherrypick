from datetime import date

from test_evaluate import fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

# SOFI-shaped: a naked short 16 put sold for 1.01, now in the money (stock 15.225) and worth 1.445.
NAKED = [leg(-1, "P", 16, "2026-11-20", 1.01, 1.445)]


def test_a_naked_short_put_told_to_close_rolls_out_for_a_credit_instead():
    t = run(NAKED, spot=15.225)
    rec = t["recommendation"]
    assert rec["action"] == ev.ROLL_FOR_CREDIT
    assert rec["replaces"] == ["credit.breached"]
    assert rec["conflicts"] == [] and rec["confidence"] == 60  # its trigger is not counted against it
    roll = t["roll"]
    assert roll["expiration"] == "2026-12-18" and roll["strike"] == 16
    assert roll["credit"] > 0.10
    # The plain close stays on the trade as the fallback when the roll will not fill for a credit.
    assert t["order"]["net_side"] == "db"
    assert "Only send it for a net credit" in t["explanation"]


def test_no_roll_when_the_modelled_credit_is_below_the_minimum():
    rules, _ = _rules.effective({"roll": {"naked_put": {"min_credit": 5.0}}})
    t = run(NAKED, spot=15.225, rules=rules)
    assert t["recommendation"]["action"] == ev.CLOSE
    assert t["roll"] is None


def test_a_spread_is_not_a_naked_put_and_is_never_rolled_by_this_rule():
    spread = [leg(-1, "P", 16, "2026-11-20", 1.01, 1.445), leg(1, "P", 14, "2026-11-20", 0.40, 0.45)]
    t = run(spread, spot=15.225)
    assert "roll.naked_put" not in fired(t)


def test_a_winning_naked_put_takes_its_profit_rather_than_rolling():
    t = run([leg(-1, "P", 14, "2026-11-20", 1.00, 0.40)], spot=16)
    assert t["recommendation"]["action"] == ev.TAKE_PROFIT
    assert "roll.naked_put" not in fired(t)


def test_the_roll_can_be_switched_off():
    rules, _ = _rules.effective({"roll": {"naked_put": {"enabled": False}}})
    assert run(NAKED, spot=15.225, rules=rules)["recommendation"]["action"] == ev.CLOSE


def test_a_winners_time_exit_rolls_for_a_credit_only():
    t = run([leg(-1, "P", 90, "2026-10-30", 1.00, 0.70), leg(1, "P", 85, "2026-10-30", 0.40, 0.30)])
    (f,) = [f for f in t["fired"] if f["rule"] == "credit.time_exit"]
    assert "for a net credit only" in f["reason"]
    assert "only for a net credit" in t["explanation"]


def test_monthly_expiry_moves_off_a_friday_holiday():
    assert ev._monthly_on_or_after(date(2026, 12, 1)) == date(2026, 12, 18)
    # 2027-06-18 is the third Friday and Juneteenth: the monthly is the Thursday before.
    assert ev._monthly_on_or_after(date(2027, 6, 1)) == date(2027, 6, 17)
