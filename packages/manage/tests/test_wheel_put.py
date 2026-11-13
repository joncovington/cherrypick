"""A short put on a name the operator wheels (intent "wheel") is never stopped or closed as breached:
assignment is the wheel's next step, so wheel.tested offers it beside the roll and the close.

Opt in per name (user, 2026-11-12): every lone short put is a wheel cycle to the wheel view, so without
the intent a put keeps the credit stop and the breached close. Synthetic histories, as in test_wheel.py.
"""

from test_wheel_rules import INCOME, OPENED, PUT, evaluate, fired, trade_of

from cherrypick.manage import evaluate as ev

WHEEL = {"positions": {"XYZ": {"intent": "wheel"}}}
TESTED = [  # the 50 put, sold for 1.00, at 2.60 with the stock at 47
    {
        "symbol": PUT,
        "underlying": "XYZ",
        "instrument_type": "Equity Option",
        "quantity": -1,
        "expiration": "2026-11-20",
        "right": "P",
        "strike": 50.0,
        "open_price": 1.00,
        "mid": 2.60,
        "priced": True,
        "value": -260.0,
        "open_pl": -160.0,
    }
]


def test_without_the_intent_a_tested_put_keeps_its_stop_and_breached_close():
    # The guard shown failing: the same put on a name not marked as wheeled.
    t = trade_of(evaluate(OPENED, TESTED, 47.0, income=INCOME), PUT)
    assert {"credit.stop", "credit.breached"} <= set(fired(t))
    assert t["wheel_put"] is False


def test_a_wheeled_names_tested_put_is_rolled_closed_or_assigned_never_stopped():
    t = trade_of(evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides=WHEEL), PUT)
    names = fired(t)
    assert t["wheel_put"] is True
    assert not {"credit.stop", "credit.breached"} & set(names)
    reason = names["wheel.tested"]["reason"]
    assert "take the assignment" in reason and "100 shares at 50" in reason and "wheel breakeven" in reason
    assert t["recommendation"]["action"] in (ev.ROLL_OR_CLOSE, ev.ROLL_FOR_CREDIT)


def test_a_wheeled_put_past_its_stop_but_not_tested_is_held():
    near = [{**TESTED[0], "mid": 2.10, "value": -210.0, "open_pl": -110.0}]
    t = trade_of(evaluate(OPENED, near, 50.5, income=INCOME, overrides=WHEEL), PUT)
    assert "credit.stop" not in fired(t) and t["recommendation"]["action"] == ev.HOLD


def test_the_intent_on_one_trade_alone_works_too():
    trade_id = trade_of(evaluate(OPENED, TESTED, 47.0, income=INCOME), PUT)["trade_id"]
    t = trade_of(
        evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides={"trades": {trade_id: {"intent": "wheel"}}}),
        PUT,
    )
    assert t["wheel_put"] is True and "credit.stop" not in fired(t)


def test_with_no_reconciled_cycle_the_stop_stands_whatever_the_intent():
    # No history: no wheel cycle holds the put, so nothing says it can be met by an assignment.
    t = trade_of(evaluate([], TESTED, 47.0, income=INCOME, overrides=WHEEL), PUT)
    assert t["wheel_put"] is False and "credit.stop" in fired(t)
