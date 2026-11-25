"""`intent_ask`: what a trade is asked about its intent, so the console shows the question and never works
out the shape rules. A naked short put is asked wheel or not (with each outcome's verdict), a call diagonal
pmcc or not (with the PMCC view's own reading as the suggestion), and anything else is not asked.
Synthetic histories, as in test_wheel.py and test_pmcc.py."""

import test_pmcc as pm
import test_wheel_rules as wr
from test_wheel_put import TESTED
from test_wheel_rules import INCOME, OPENED, PUT

from cherrypick.manage import evaluate as ev
from cherrypick.manage import intentask

TRADE = "XYZ-261001-1"  # the put OPENED sells


def put_ask(history=OPENED, overrides=None, legs=TESTED):
    acct = wr.evaluate(history, legs, 47.0, income=INCOME, overrides=overrides)
    t = wr.trade_of(acct, PUT)
    return t, t["intent_ask"]


def brief(t):
    rec = t["recommendation"]
    return {"action": rec["action"], "rule": rec["rule"], "reason": rec["reason"]}


# --------------------------------------------------------------------------- a naked short put
def test_an_unanswered_put_is_asked_with_each_outcome_judged():
    _, ask = put_ask()
    assert ask["kind"] == "wheel" and ask["asked"] is True and ask["current"] is None
    assert ask["options"] == ["wheel", "no_wheel"]
    assert ask["key"] == f"trades.{TRADE}.intent" and ask["scope"] == "trade"
    assert ask["verdicts"]["wheel"]["rule"] in ("wheel.tested", "roll.naked_put")
    assert ask["verdicts"]["no_wheel"]["action"] == ev.CLOSE
    assert ask["suggested"] == "wheel" and ask["evidence"] and ask["note"] is None


def test_an_answered_put_keeps_its_block_so_the_answer_can_change():
    t, ask = put_ask(overrides={"trades": {TRADE: {"intent": "wheel"}}})
    assert ask["asked"] is False and ask["current"] == "wheel"
    assert ask["verdicts"]["wheel"] == brief(t)  # its own verdict is what it stands at
    assert ask["verdicts"]["no_wheel"]["action"] == ev.CLOSE
    assert ask["suggested"] is None and ask["evidence"] == [] and ask["note"] is None


def test_the_other_outcome_is_judged_even_when_the_trade_has_its_own_intent():
    # A trade's own intent beats a name's, so the outcome it does not hold must be set on the trade itself.
    t, ask = put_ask(overrides={"trades": {TRADE: {"intent": "no_wheel"}}})
    assert t["wheel_put"] is False and ask["current"] == "no_wheel"
    assert ask["verdicts"]["no_wheel"] == brief(t)
    assert ask["verdicts"]["wheel"]["rule"] == "wheel.tested"


def test_a_provisional_trade_is_keyed_to_its_name():
    t, ask = put_ask(history=[])
    assert t["trade_id_provisional"] is True
    assert ask["key"] == "positions.XYZ.intent" and ask["scope"] == "name"


def test_a_names_intent_for_another_trade_is_said_not_read_as_the_answer():
    _, ask = put_ask(overrides={"positions": {"XYZ": {"intent": "pmcc"}}})
    assert ask["asked"] is True and ask["current"] is None
    assert "pmcc" in ask["note"] and "not this put" in ask["note"]


def test_a_put_answered_beside_a_names_pmcc_carries_no_note():
    # The note explains why a put is asked; once its own entry answers, there is nothing to explain.
    overrides = {"positions": {"XYZ": {"intent": "pmcc"}}, "trades": {TRADE: {"intent": "wheel"}}}
    _, ask = put_ask(overrides=overrides)
    assert ask["asked"] is False and ask["current"] == "wheel" and ask["note"] is None


def test_a_free_label_on_a_put_is_not_asked():
    _, ask = put_ask(overrides={"trades": {TRADE: {"intent": "income"}}})
    assert ask["asked"] is False and ask["current"] == "income"


def test_a_put_with_no_cycle_suggests_nothing():
    _, ask = put_ask(history=[])
    assert ask["suggested"] is None and ask["evidence"] == []


def test_a_put_spread_and_other_shapes_are_not_asked():
    long_put = {
        **TESTED[0],
        "symbol": PUT.replace("00050000", "00045000"),
        "strike": 45.0,
        "quantity": 1,
        "mid": 0.90,
    }
    acct = wr.evaluate(OPENED, [TESTED[0], long_put], 47.0, income=INCOME)
    assert all(t["intent_ask"] is None for t in acct["trades"] if len(t["legs"]) == 2)


# --------------------------------------------------------------------------- a call diagonal
NEAR = pm.occ(pm.LONG_EXP, "C", 50)
NEAR_PRICE = pm.fill(50, 0.30)


def near_snap(**kw):
    return pm.snapshot(
        pm.history(long=NEAR, long_price=NEAR_PRICE),
        pm.legs(long=NEAR, long_price=NEAR_PRICE, long_mid=9.0),
        **kw,
    )


def ask_of(snap, overrides=None):
    a = pm.evaluate(snap, overrides)
    return pm.view_of(a), pm.trade_of(a, pm.SHORT)["intent_ask"]


def test_a_diagonal_the_delta_decided_is_asked_with_its_reading_as_the_suggestion():
    v, ask = ask_of(near_snap())
    assert ask["kind"] == "pmcc" and ask["asked"] is True and ask["current"] is None
    assert ask["options"] == ["pmcc", "diagonal"] and ask["verdicts"] == {}
    assert ask["suggested"] == "diagonal" and "under 0.70" in ask["evidence"][0]
    assert ask["key"] == f"trades.{v['intent_key']}.intent" and ask["scope"] == "contract"


def test_a_pmcc_the_delta_recognised_is_asked_too():
    v, ask = ask_of(pm.snapshot(pm.history(), pm.legs()))
    assert v["recognition"] == "pmcc" and v["intent"] is None
    assert ask["asked"] is True and ask["suggested"] == "pmcc" and ask["evidence"][0].startswith("entered at")


def test_a_diagonal_whose_delta_cannot_be_solved_is_asked_with_no_suggestion():
    _, ask = ask_of(pm.snapshot(pm.history(), pm.legs(), hist=None))
    assert ask["asked"] is True and ask["suggested"] is None
    assert "unknown" in ask["evidence"][0]


def test_an_intent_on_the_long_contract_answers_it():
    snap = near_snap()
    v, _ = ask_of(snap)
    for intent in ("pmcc", "diagonal"):
        _, ask = ask_of(snap, {"trades": {v["intent_key"]: {"intent": intent}}})
        assert ask["asked"] is False and ask["current"] == intent
        assert ask["suggested"] is None and ask["evidence"] == []


def test_a_names_pmcc_or_diagonal_answers_it_but_its_wheel_does_not():
    snap = near_snap()
    _, ask = ask_of(snap, {"positions": {"XYZ": {"intent": "pmcc"}}})
    assert ask["asked"] is False and ask["current"] == "pmcc"
    _, ask = ask_of(snap, {"positions": {"XYZ": {"intent": "wheel"}}})
    assert ask["asked"] is True and ask["current"] is None


def test_the_question_stands_when_the_pmcc_rules_fail(monkeypatch):
    # The question is a fact of the view, not of its rules: a failing rule costs the verdict, not the ask.
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ev, "_pmcc_rules", boom)
    t = pm.trade_of(pm.evaluate(near_snap()), pm.SHORT)
    assert "error" in t["pmcc"]
    assert t["intent_ask"]["asked"] is True and t["intent_ask"]["kind"] == "pmcc"


def test_a_trade_holding_two_diagonals_carries_the_one_still_open():
    def view(key, intent):
        return {
            "structure": "diagonal",
            "intent": intent,
            "recognition": "pmcc",
            "recognized_by": "x",
            "intent_key": key,
        }

    answered, open_ = view("XYZ 280121C00040000", "pmcc"), view("XYZ 280121C00045000", None)
    assert intentask.diagonal_asks([answered, open_])["key"] == "trades.XYZ 280121C00045000.intent"
    assert intentask.diagonal_asks([open_, answered])["key"] == "trades.XYZ 280121C00045000.intent"
    # None open: the first there is. None at all: no question.
    assert intentask.diagonal_asks([answered, view("XYZ 280121C00045000", "diagonal")])["current"] == "pmcc"
    assert intentask.diagonal_asks([{"structure": "calendar"}]) is None
    assert intentask.diagonal_asks([]) is None


def test_a_put_outcome_that_cannot_be_judged_costs_the_verdict_not_the_evaluation(monkeypatch):
    real = ev.evaluate_trade

    def flaky(*args, **kwargs):
        if kwargs["rules"].get("trades"):  # only the re-judging under an answer sets a trade's own intent
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(ev, "evaluate_trade", flaky)
    acct = wr.evaluate(OPENED, TESTED, 47.0, income=INCOME)
    t = wr.trade_of(acct, PUT)
    assert t["intent_ask"]["asked"] is True and t["intent_ask"]["verdicts"] == {}
    assert acct["wheel"]["intent_candidates"] == []  # no wheel verdict to list it with


def test_a_view_with_no_long_contract_to_write_under_is_not_asked():
    view = {
        "structure": "diagonal",
        "intent": None,
        "recognition": "pmcc",
        "recognized_by": "entered at 0.82",
    }
    assert intentask.diagonal_ask(view) is None
    assert intentask.diagonal_ask({**view, "intent_key": "XYZ 280121C00040000"})["asked"] is True


def test_a_calendar_is_not_asked():
    same = pm.occ(pm.LONG_EXP, "C", 55)
    price = pm.fill(55, 0.30)
    a = pm.evaluate(
        pm.snapshot(
            pm.history(long=same, long_price=price), pm.legs(long=same, long_price=price, long_mid=6.0)
        )
    )
    assert pm.view_of(a)["structure"] == "calendar"
    assert pm.trade_of(a, pm.SHORT)["intent_ask"] is None
