from test_evaluate import fired, run
from test_rolls import NAKED

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules


def test_a_naked_put_is_never_rolled_through_earnings_it_closes():
    t = run(NAKED, spot=15.225, metrics={"earnings_date": "2026-10-27"})
    assert t["recommendation"]["action"] == ev.CLOSE
    assert "roll.naked_put" not in fired(t)
    assert t["roll"] is None
    assert t["roll_blocked"] == {
        "reason": "earnings",
        "earnings_date": "2026-10-27",
        "expiration": "2026-12-18",
    }
    assert "carry the put through earnings on Oct 27" in t["explanation"]
    assert "re-opening it after the report" in t["explanation"]


def test_earnings_after_the_new_expiry_do_not_block_the_roll():
    t = run(NAKED, spot=15.225, metrics={"earnings_date": "2027-01-28"})
    assert t["recommendation"]["action"] == ev.ROLL_FOR_CREDIT
    assert t["roll_blocked"] is None


def test_the_earnings_block_can_be_switched_off():
    rules, warnings = _rules.effective({"roll": {"naked_put": {"skip_through_earnings": False}}})
    assert warnings == []
    t = run(NAKED, spot=15.225, rules=rules, metrics={"earnings_date": "2026-10-27"})
    assert t["recommendation"]["action"] == ev.ROLL_FOR_CREDIT
