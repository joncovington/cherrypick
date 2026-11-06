"""A calendar's stop is on the underlying's price; its same-strike roll is no longer blocked (2026-11-06)."""

import math
from datetime import date

from test_evaluate import SESSION, fired, leg, run

from cherrypick.manage import evaluate as ev
from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules

# Held: the short 38 DTE on Oct 13, so no time exit; the long 56 days after it.
FRONT, BACK = "2026-11-20", "2027-01-15"
# Due: the short 7 DTE, the long far enough out for a roll to the next monthly.
DUE_FRONT = "2026-10-20"


def calendar(front=FRONT, front_mid=4.10, back_mid=7.40, opened=(4.30, 7.00)):
    """A 100 put calendar opened for 2.70."""
    return [leg(1, "P", 100, BACK, opened[1], back_mid), leg(-1, "P", 100, front, opened[0], front_mid)]


def _bs_put(s, k, t, v, r):
    d1 = (math.log(s / k) + (r + 0.5 * v * v) * t) / (v * math.sqrt(t))
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return k * math.exp(-r * t) * n(-(d1 - v * math.sqrt(t))) - s * n(-d1)


def test_a_calendar_carries_its_price_stop_band_around_the_strike():
    t = run(calendar(), spot=100.0)
    assert t["shape"] == "calendar"
    assert fired(t) == {}
    ps = t["exit_levels"]["price_stop"]
    assert ps["modelled"] and ps["horizon"] == FRONT and ps["loss_share"] == 0.25
    assert ps["low"] < 100 < ps["high"]
    # Independent check at the band's high edge, where the short put expires worthless: the long put
    # alone is worth three quarters of the debit there, to within the grid's step.
    back = next(x for x in t["legs"] if x["quantity"] > 0)
    years = (date.fromisoformat(BACK) - date.fromisoformat(FRONT)).days / 365
    rate = float(_rules.effective(None)[0]["pricing"]["rate"])
    assert abs(_bs_put(ps["high"], 100, years, back["iv"], rate) * 100 - 0.75 * t["cost"]) < 0.02 * t["cost"]
    assert "closes below $" in t["explanation"] and "above $" in t["explanation"]


def test_outside_the_band_the_calendar_is_reviewed():
    low = run(calendar(), spot=100.0)["exit_levels"]["price_stop"]["low"]
    # The stock well under the low edge, the marks moved with it.
    t = run(calendar(front_mid=12.6, back_mid=14.2), spot=low - 4)
    assert fired(t)["debit.price_stop"] == ev.REVIEW
    reason = next(f["reason"] for f in t["fired"] if f["rule"] == "debit.price_stop")
    assert "below the price stop" in reason and "close if it closes there" in reason
    assert "re-centres it" in t["explanation"]


def test_the_price_stop_is_for_calendars_only_and_can_be_turned_off():
    vertical = run([leg(1, "P", 100, FRONT, 3.0, 3.1), leg(-1, "P", 95, FRONT, 1.0, 1.0)], spot=100.0)
    assert "price_stop" not in vertical["exit_levels"]
    rules, _ = _rules.effective({"positions": {"XYZ": {"disable": ["debit.price_stop"]}}})
    assert "price_stop" not in run(calendar(), spot=100.0, rules=rules)["exit_levels"]


def test_price_band_is_none_when_the_trade_is_already_worth_less_than_the_floor():
    legs = run(calendar(), spot=100.0)["legs"]
    assert pricing.price_band(legs, 100.0, 38 / 365, 0.04, 1e6) is None


def test_a_calendars_same_strike_roll_is_no_longer_blocked_by_the_breakeven():
    # 7 DTE short, no order chain: the short's 21-day clock fires, and the roll is due.
    t = run(calendar(front=DUE_FRONT, front_mid=1.20, back_mid=5.40, opened=(2.30, 5.00)), spot=100.0)
    assert t["roll_blocked"] is None
    assert t["roll"] is not None and t["roll"]["credit"] > 0.10
    assert t["recommendation"]["action"] == ev.ROLL_FOR_CREDIT


def test_a_diagonal_still_keeps_its_breakeven_guard():
    # Paid 40 for the 350 long against a 370 short: the new short would sit inside the breakeven.
    legs = [leg(1, "C", 350, "2027-03-19", 40.0, 44.0), leg(-1, "C", 370, "2026-10-30", 6.0, 2.0)]
    t = ev.evaluate_trade("XYZ", legs, {"last": 360.0}, SESSION, 100_000.0, _rules.effective(None)[0])
    assert t["roll_blocked"]["reason"] == "breakeven"
