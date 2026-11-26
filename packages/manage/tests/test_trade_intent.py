"""Intent is a fact about one trade. A name's intent (`positions.<SYM>.intent`) applies only to the trades
it can describe, a trade's own (`trades.<ID>.intent`) wins, the wheel tab's candidates are keyed per
trade with a suggestion, and any other value is a free label. Synthetic histories, as in test_wheel.py."""

from test_wheel_put import TESTED
from test_wheel_rules import INCOME, OPENED, PUT, evaluate, trade_of

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

TRADE = "XYZ-261001-1"  # the put OPENED sells: XYZ, opened 2026-10-01


def short_put():
    return {"right": "P", "quantity": -1.0}


def call(qty):
    return {"right": "C", "quantity": qty}


# --------------------------------------------------------------------------- what a name's intent describes
def test_a_names_pmcc_does_not_describe_a_naked_put():
    rules = _rules.effective({"positions": {"XYZ": {"intent": "pmcc"}}})[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [short_put()]) is None


def test_a_names_pmcc_still_describes_a_call_diagonal():
    rules = _rules.effective({"positions": {"XYZ": {"intent": "pmcc"}}})[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [call(1.0), call(-1.0)]) == "pmcc"


def test_a_names_wheel_does_not_describe_a_call_diagonal():
    rules = _rules.effective({"positions": {"XYZ": {"intent": "wheel"}}})[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [call(1.0), call(-1.0)]) is None
    assert _rules.trade_intent(rules, "XYZ", TRADE, [short_put()]) == "wheel"


def test_a_wheel_is_never_a_put_with_a_long_leg():
    rules = _rules.effective({"positions": {"XYZ": {"intent": "no_wheel"}}})[0]
    spread = [short_put(), {"right": "P", "quantity": 1.0}]
    assert _rules.trade_intent(rules, "XYZ", TRADE, spread) is None


def test_a_trades_own_intent_wins_over_the_names_and_needs_no_fit():
    rules = _rules.effective(
        {"positions": {"XYZ": {"intent": "pmcc"}}, "trades": {TRADE: {"intent": "wheel"}}}
    )[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [short_put()]) == "wheel"


def test_a_label_intent_describes_any_trade():
    rules = _rules.effective({"positions": {"XYZ": {"intent": "hedge"}}})[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [short_put()]) == "hedge"
    assert _rules.trade_intent(rules, "XYZ", TRADE, [call(1.0), call(-1.0)]) == "hedge"


def test_an_entry_that_is_not_an_object_names_no_intent_and_does_not_raise():
    rules = _rules.effective({"positions": {"XYZ": "oops"}, "trades": {TRADE: ["oops"]}})[0]
    assert _rules.trade_intent(rules, "XYZ", TRADE, [short_put()]) is None
    assert _rules.name_intent(rules, "XYZ") is None


# --------------------------------------------------------------------------- in the evaluation
def test_a_names_pmcc_no_longer_hides_a_put_from_the_wheel_tab():
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides={"positions": {"XYZ": {"intent": "pmcc"}}})
    t = trade_of(acct, PUT)
    assert t["intent"] is None and t["wheel_put"] is False
    (c,) = acct["wheel"]["intent_candidates"]
    assert c["trade_id"] == TRADE


def test_a_put_marked_a_wheel_by_its_trade_id_takes_the_wheel_path_beside_a_names_pmcc():
    overrides = {"positions": {"XYZ": {"intent": "pmcc"}}, "trades": {TRADE: {"intent": "wheel"}}}
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides=overrides)
    t = trade_of(acct, PUT)
    assert t["intent"] == "wheel" and t["wheel_put"] is True
    assert t["recommendation"]["rule"] == "wheel.tested"
    assert acct["wheel"]["intent_candidates"] == []


def test_a_trade_marked_not_a_wheel_keeps_its_stop_and_leaves_the_list():
    acct = evaluate(
        OPENED, TESTED, 47.0, income=INCOME, overrides={"trades": {TRADE: {"intent": ev.NO_WHEEL}}}
    )
    t = trade_of(acct, PUT)
    assert t["wheel_put"] is False and t["recommendation"]["action"] == ev.CLOSE
    assert acct["wheel"]["intent_candidates"] == []


# --------------------------------------------------------------------------- the candidate row
def test_a_candidate_is_keyed_to_its_trade_and_judged_as_marking_it_would():
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME)
    (c,) = acct["wheel"]["intent_candidates"]
    assert c["key"] == f"trades.{TRADE}.intent"
    marked = evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides={"trades": {TRADE: {"intent": "wheel"}}})
    assert c["as_wheel"]["rule"] == trade_of(marked, PUT)["recommendation"]["rule"]


def test_a_trade_whose_id_is_still_provisional_is_keyed_to_its_name():
    # With no order history the ID is provisional: an entry under it would not survive the next run.
    acct = evaluate([], TESTED, 47.0, income=INCOME)
    assert trade_of(acct, PUT)["trade_id_provisional"] is True
    (c,) = acct["wheel"]["intent_candidates"]
    assert c["key"] == "positions.XYZ.intent"


def test_a_candidate_on_a_reconciled_cycle_suggests_a_wheel_and_says_why():
    (c,) = evaluate(OPENED, TESTED, 47.0, income=INCOME)["wheel"]["intent_candidates"]
    assert c["suggested"] == "wheel"
    assert any("wheel cycle" in why for why in c["evidence"])


def test_a_candidate_with_no_cycle_suggests_nothing():
    (c,) = evaluate([], TESTED, 47.0, income=INCOME)["wheel"]["intent_candidates"]
    assert c["suggested"] is None and c["evidence"] == [] and c["note"] is None


def test_a_candidate_says_when_the_names_intent_is_for_another_trade():
    acct = evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides={"positions": {"XYZ": {"intent": "pmcc"}}})
    (c,) = acct["wheel"]["intent_candidates"]
    assert "pmcc" in c["note"] and "not this put" in c["note"]
    # The notice is not a reason to suggest a wheel: the suggestion's evidence is the cycles alone.
    assert not any("pmcc" in why for why in c["evidence"])


# --------------------------------------------------------------------------- free labels
def test_a_free_label_is_shown_on_the_trade_and_takes_it_off_the_list():
    # An intent the suite has no behaviour for ("income", "hedge") is a label: no warning, shown on the card.
    for overrides in (
        {"positions": {"XYZ": {"intent": "income"}}},
        {"trades": {TRADE: {"intent": "income"}}},
    ):
        rules, warnings = _rules.effective(overrides)
        assert [w for w in warnings if "intent" in w] == []
        acct = evaluate(OPENED, TESTED, 47.0, income=INCOME, overrides=overrides)
        assert trade_of(acct, PUT)["intent"] == "income"
        assert acct["wheel"]["intent_candidates"] == []
