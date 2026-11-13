"""The expiry close for legs opened inside its own window: due on the expiry day, not the first one."""

import copy
from datetime import date

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
EXPIRY = "2026-10-23"  # a Friday


def leg(qty, right, strike, opened, mid, exp=EXPIRY):
    return {
        "symbol": f"XYZ   {exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}",
        "underlying": "XYZ",
        "instrument_type": "Equity Option",
        "quantity": qty,
        "expiration": exp,
        "right": right,
        "strike": strike,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": qty * (mid - opened) * 100,
    }


# A put credit spread a little under water: nothing but the expiry rule has a reason to fire.
SPREAD = [leg(-1, "P", 95, 1.00, 1.05), leg(1, "P", 90, 0.40, 0.42)]


def chain(opened_on: str, legs=SPREAD) -> dict:
    rows = [
        {
            "symbol": x["symbol"],
            "action": "Sell to Open" if x["quantity"] < 0 else "Buy to Open",
            "quantity": abs(x["quantity"]),
            "price": x["open_price"],
        }
        for x in legs
    ]
    return {
        "cash": 60.0,
        "fees": -2.26,
        "orders": [
            {"executed_at": f"{opened_on}T15:00:00", "role": "open", "net": 60.0, "fees": -2.26, "legs": rows}
        ],
    }


def run(session, opened_on="2026-10-21", legs=SPREAD, rules=RULES, with_chain=True):
    metrics = {"last": 100.0}
    c = chain(opened_on, legs) if with_chain else None
    return ev.evaluate_trade("XYZ", copy.deepcopy(legs), metrics, session, 100_000.0, rules, c)


def rules_named(t):
    return {f["rule"] for f in t["fired"]}


def test_legs_opened_inside_the_window_close_on_expiry_day_not_the_day_they_open():
    # Opened Wednesday at 2 DTE: the old rule said close at once, guarding nothing.
    t = run(date(2026, 10, 21))
    assert "expiry" not in rules_named(t)
    assert t["expiry_close_due"] == {"date": EXPIRY, "entry_dte": 2, "limit": 3}
    assert "close before that day's close" in t["explanation"]
    # Thursday: still waiting.
    assert "expiry" not in rules_named(run(date(2026, 10, 22)))
    # Friday, expiry day: due.
    friday = run(date(2026, 10, 23))
    assert friday["expiry_close_due"] is None
    assert friday["recommendation"]["action"] == ev.CLOSE and friday["recommendation"]["rule"] == "expiry"


def test_legs_opened_before_the_window_still_close_at_three_days():
    t = run(date(2026, 10, 20), opened_on="2026-10-09")
    assert t["expiry_close_due"] is None
    assert t["recommendation"]["rule"] == "expiry"


def test_one_leg_opened_before_the_window_keeps_the_close_now():
    # The long was bought a week earlier, the short sold inside the window: not all of it was put on
    # to be held into expiry.
    c = chain("2026-10-21")
    c["orders"].insert(
        0,
        {
            "executed_at": "2026-10-09T15:00:00",
            "role": "open",
            "net": -40.0,
            "fees": -1.13,
            "legs": [{"symbol": SPREAD[1]["symbol"], "action": "Buy to Open", "quantity": 1, "price": 0.40}],
        },
    )
    c["orders"][1]["legs"] = c["orders"][1]["legs"][:1]
    t = ev.evaluate_trade(
        "XYZ", copy.deepcopy(SPREAD), {"last": 100.0}, date(2026, 10, 21), 100_000.0, RULES, c
    )
    assert t["expiry_close_due"] is None and t["recommendation"]["rule"] == "expiry"


def test_without_an_order_chain_the_close_stands():
    t = run(date(2026, 10, 21), with_chain=False)
    assert t["expiry_close_due"] is None and "expiry" in rules_named(t)


def test_the_switch_restores_the_close_at_three_days():
    off, _ = _rules.effective({"expiry": {"wait_if_entered_inside": False}})
    t = run(date(2026, 10, 21), rules=off)
    assert t["expiry_close_due"] is None and t["recommendation"]["rule"] == "expiry"


def test_a_stop_still_fires_while_the_expiry_close_waits():
    # Well through the short strike: the credit stop closes it; only the expiry close waited.
    deep = [leg(-1, "P", 95, 1.00, 4.50), leg(1, "P", 90, 0.40, 0.60)]
    t = run(date(2026, 10, 21), legs=deep)
    assert t["expiry_close_due"] is not None
    assert t["recommendation"]["action"] == ev.CLOSE and t["recommendation"]["rule"] != "expiry"
