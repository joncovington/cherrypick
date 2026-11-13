"""The wheel tab's "no intent yet": short puts the wheel could hold on a name with no intent, each with
today's verdict beside the verdict it would get marked as a wheel. Synthetic histories, as in
test_wheel.py."""

from test_wheel_put import TESTED
from test_wheel_rules import INCOME, OPENED, PUT, evaluate

from cherrypick.manage import evaluate as ev


def candidates(acct):
    return acct["wheel"]["intent_candidates"]


def test_a_tested_put_with_no_intent_is_listed_with_both_verdicts():
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME)
    (c,) = candidates(acct)
    assert c["underlying"] == "XYZ" and c["legs"] == ["-1 2026-11-20 50P"]
    assert c["cash_secured"] == 5000.0 and c["contracts"] == 1
    assert abs(c["net_if_assigned"] - 49.0) < 0.05  # the 50 strike less the 1.00 kept
    assert c["now"]["action"] == ev.CLOSE and c["now"]["rule"] in ("credit.stop", "credit.breached")
    assert c["as_wheel"]["rule"] in ("wheel.tested", "roll.naked_put") and c["changes"] is True
    assert c["wheel_path"] is True and c["key"] == "positions.XYZ.intent"


def test_the_listed_trade_itself_is_judged_without_the_intent():
    # The re-judging is for the candidate row only: the trade the cards show keeps today's verdict.
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME)
    (t,) = [t for t in acct["trades"] if any(leg["symbol"] == PUT for leg in t["legs"])]
    assert t["wheel_put"] is False and t["recommendation"]["action"] == ev.CLOSE


def test_any_intent_takes_the_name_off_the_list():
    for intent in ("wheel", ev.NO_WHEEL):
        acct = evaluate(
            OPENED, TESTED, 47.0, income=INCOME, overrides={"positions": {"XYZ": {"intent": intent}}}
        )
        assert candidates(acct) == [], intent


def test_an_untested_put_is_listed_and_changes_nothing_today():
    calm = [{**TESTED[0], "mid": 0.60, "value": -60.0, "open_pl": 40.0}]
    (c,) = candidates(evaluate(OPENED, calm, 52.0, income=INCOME))
    assert c["now"] == c["as_wheel"] and c["changes"] is False


def test_with_no_reconciled_cycle_marking_it_would_not_yet_change_the_verdict():
    (c,) = candidates(evaluate([], TESTED, 47.0, income=INCOME))
    assert c["wheel_path"] is False and c["changes"] is False


def test_a_spread_is_never_a_candidate():
    long_put = {
        **TESTED[0],
        "symbol": PUT.replace("00050000", "00045000"),
        "strike": 45.0,
        "quantity": 1,
        "mid": 0.90,
    }
    acct = evaluate(OPENED, [TESTED[0], long_put], 47.0, income=INCOME)
    spread = [t for t in acct["trades"] if len(t["legs"]) == 2]
    listed = {leg for c in candidates(acct) for leg in c["legs"]}
    # Whichever way the legs group, a trade holding the long put is never listed.
    assert not any(set(leg["label"] for leg in t["legs"]) <= listed for t in spread)
    assert not any("45P" in leg for leg in listed)
