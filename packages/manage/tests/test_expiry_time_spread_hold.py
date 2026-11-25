"""A calendar's far out-of-the-money short is left to expire; one near the money is still closed."""

import copy
from datetime import date

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
FRONT = "2026-11-27"
BACK = "2026-12-18"


def leg(qty, strike, exp, mid, delta):
    return {
        "symbol": f"XYZ   {exp.replace('-', '')[2:]}P{int(strike * 1000):08d}",
        "underlying": "XYZ",
        "instrument_type": "Equity Option",
        "quantity": qty,
        "expiration": exp,
        "right": "P",
        "strike": strike,
        "open_price": mid,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": 0.0,
        "delta": delta,
    }


def run(short_delta, long_strike=600.0, spot=625.0):
    legs = [leg(-1, 600.0, FRONT, 0.66, short_delta), leg(1, long_strike, BACK, 8.13, -0.40)]
    metrics = {"last": spot}
    return ev.evaluate_trade("XYZ", copy.deepcopy(legs), metrics, date(2026, 11, 24), 100_000.0, RULES, None)


def expiry_actions(t):
    return [f["action"] for f in t["fired"] if f["rule"] == "expiry"]


def test_far_otm_short_is_left_to_expire():
    t = run(-0.06)
    assert t["shape"] == "calendar"
    assert expiry_actions(t) == []
    assert t["expiry_held"]["legs"] == ["-1 2026-11-27 600P"]


def test_short_near_the_money_is_still_closed():
    t = run(-0.30, spot=601.0)
    assert expiry_actions(t) == [ev.CLOSE_LEGS]
    assert "expiry_held" not in t


def sold_for(price, short_delta, spot):
    legs = [leg(-1, 600.0, FRONT, 0.66, short_delta), leg(1, 600.0, BACK, 8.13, -0.40)]
    legs[0]["open_price"] = price
    metrics = {"last": spot}
    return ev.evaluate_trade("XYZ", copy.deepcopy(legs), metrics, date(2026, 11, 24), 100_000.0, RULES, None)


def test_held_short_is_not_bought_back_or_rolled_for_being_captured():
    # Sold at 6.15, worth 0.66: 89% captured, past the 80% take-profit.
    t = sold_for(6.15, -0.06, 625.0)
    names = {f["rule"] for f in t["fired"]}
    assert "legs.short_take_profit" not in names
    assert not t.get("roll")
    assert not any(f["action"] == ev.ROLL_OR_CLOSE for f in t["fired"])


def test_captured_short_near_the_money_is_still_bought_back():
    t = sold_for(6.15, -0.30, 601.0)
    assert "legs.short_take_profit" in {f["rule"] for f in t["fired"]}


def test_uncovered_short_is_still_closed():
    # The long's strike sits below the short's: it is no guard, so the close stands.
    t = run(-0.06, long_strike=590.0)
    assert expiry_actions(t) == [ev.CLOSE_LEGS]
