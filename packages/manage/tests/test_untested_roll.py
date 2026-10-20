import pytest
from test_evaluate import SESSION, fired, leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules

NOV = "2026-11-20"
YEARS = 38 / 365.0  # SESSION to NOV
IV, RATE = 0.30, 0.04


def mid(spot, strike, right):
    return round(pricing.price(spot, strike, YEARS, IV, right, RATE), 2)


def strangle(spot, put=90, call=110, opened=(1.0, 1.0), qty=1):
    return [
        leg(-qty, "P", put, NOV, opened[0], mid(spot, put, "P")),
        leg(-qty, "C", call, NOV, opened[1], mid(spot, call, "C")),
    ]


def chain(spot, strikes, right="P", underlying="XYZ"):
    return {
        f"{underlying}|{NOV}": {
            "underlying": underlying,
            "expiration": NOV,
            "spot": spot,
            "options": [{"strike": float(k), "right": right, "mid": mid(spot, k, right)} for k in strikes],
            "error": None,
        }
    }


ON = {"enabled": True}  # the rule is off by default (2026-10-20); these tests turn it on


def rules_on(**untested):
    return _rules.effective({"roll": {"untested_side": {**ON, **untested}}})[0]


def run(legs, spot, chains, rules=None):
    rules = rules or rules_on()
    return ev.evaluate_trade("XYZ", legs, {"last": spot}, SESSION, 100_000.0, rules, None, None, chains)


def test_the_untested_roll_is_off_by_default():
    spot = 107.0
    t = run(strangle(spot), spot, chain(spot, range(85, 112)), _rules.effective(None)[0])
    assert "roll.untested_side" not in fired(t) and t["untested_roll"] is None
    assert _rules.DEFAULT_RULES["roll"]["untested_side"]["enabled"] is False


def test_a_tested_strangle_rolls_its_untested_put_up_to_the_listed_strike_nearest_30_delta():
    spot = 107.0  # toward the 110 call
    t = run(strangle(spot), spot, chain(spot, range(85, 112)))
    assert t["shape"] == "short strangle"
    assert "roll.untested_side" in fired(t)
    r = t["untested_roll"]
    assert (r["tested_strike"], r["from_strike"], r["right"]) == (110, 90, "P")
    assert abs(r["tested_delta"]) >= 0.40
    # The listed put nearest 30 delta, never past the tested 110.
    best = min(range(91, 111), key=lambda k: abs(abs(pricing.delta(spot, k, YEARS, IV, "P", RATE)) - 0.30))
    assert r["to_strike"] == best and 90 < best <= 110
    assert r["credit"] == pytest.approx(mid(spot, best, "P") - mid(spot, 90, "P"))
    assert r["legs"][0] == f"BTC 1 {NOV} 90P @ {mid(spot, 90, 'P'):.2f}"
    assert r["legs"][1].startswith(f"STO 1 {NOV} {best:g}P @ ")
    # The 90 put has decayed past the buy-back rule's capture: the roll is offered in its place, the
    # buy-back alone its fallback order.
    rec = t["recommendation"]
    assert fired(t)["legs.short_take_profit"] == ev.CLOSE_LEGS
    assert rec["rule"] == "roll.untested_side" and rec["action"] == ev.ROLL_FOR_CREDIT
    assert rec["replaces"] == ["legs.short_take_profit"] and rec["conflicts"] == []
    assert [row["leg"] for row in t["order"]["legs"]] == [f"-1 {NOV} 90P"]
    assert "untested put up to the" in t["explanation"] and "repair, not a cure" in t["explanation"]
    assert "rather than only buying it back" in t["explanation"]


def test_a_tested_strangle_whose_untested_side_has_premium_left_gets_a_review():
    spot = 107.0
    # The put has kept two thirds of its premium (not the buy-back's 80%), and the trade is not stopped.
    legs = strangle(spot, opened=(mid(spot, 90, "P") * 1.5, 3.0))
    t = run(legs, spot, chain(spot, range(85, 112)))
    assert "legs.short_take_profit" not in fired(t)
    assert fired(t)["roll.untested_side"] == ev.REVIEW
    # A naked strangle's unbounded loss is also a review, at a higher confidence: it heads the
    # recommendation, and the roll is named beside it.
    rec = t["recommendation"]
    assert rec["action"] == ev.REVIEW and rec["rule"] == "risk.oversize"
    assert "roll.untested_side" in rec["agreeing"] and t["order"] is None
    assert "untested-side roll points the same way" in t["explanation"]


def test_a_stopped_strangle_is_closed_not_rolled():
    # Opened for $1.20 in all, now $5 to close: the credit stop outranks any roll.
    spot = 107.0
    t = run(strangle(spot, opened=(0.6, 0.6)), spot, chain(spot, range(85, 112)))
    assert fired(t)["credit.stop"] == ev.CLOSE and fired(t)["roll.untested_side"] == ev.REVIEW
    assert t["recommendation"]["action"] == ev.CLOSE


def test_an_untested_strangle_is_left_alone():
    spot = 100.0
    t = run(strangle(spot), spot, chain(spot, range(85, 112)))
    assert "roll.untested_side" not in fired(t) and t["untested_roll"] is None


def test_a_tested_strangle_without_a_quoted_chain_says_so_and_proposes_nothing():
    spot = 107.0
    t = run(strangle(spot), spot, {})
    assert "roll.untested_side" not in fired(t)
    assert t["untested_roll"]["blocked"] == "no_chain"


def test_a_strangles_untested_side_never_passes_the_tested_strike():
    # Only strikes beyond the tested call are listed above the old put: each would invert it, so
    # nothing is proposed, and the inversion is kept as the alternative ranked last.
    spot = 107.0
    t = run(strangle(spot), spot, chain(spot, [85, 88, 112, 115]))
    assert "roll.untested_side" not in fired(t)
    assert t["untested_roll"]["blocked"] == "inverts"
    # With nothing toward the money at all, there is no strike.
    t = run(strangle(spot), spot, chain(spot, [85, 88]))
    assert t["untested_roll"]["blocked"] == "no_strike"


def test_an_inversion_is_never_proposed_and_ranks_below_closing_or_rolling_out():
    # Short 100 straddle, the stock at 108: the call is tested, and any in-expiry roll of the put
    # passes 100 -- an inversion, which always ranks below closing or rolling out in time.
    spot = 108.0
    legs = strangle(spot, put=100, call=100, opened=(4.0, 4.0))
    t = run(legs, spot, chain(spot, range(95, 115)))
    assert t["shape"] == "short straddle"
    assert "roll.untested_side" not in fired(t)
    r = t["untested_roll"]
    assert r["blocked"] == "inverts"
    assert r["inversion"]["to_strike"] > 100
    assert r["inversion"]["ranked_below"] == ["close", "roll out in time without inverting"]
    assert t["recommendation"]["action"] != ev.ROLL_FOR_CREDIT


def test_a_strangles_roll_may_reach_the_tested_strike_but_not_pass_it():
    # Listed strikes at and past the tested 110 call only: 110 is a straddle, not an inversion.
    spot = 109.0
    t = run(strangle(spot), spot, chain(spot, [85, 110, 111, 112]))
    r = t["untested_roll"]
    assert r.get("blocked") != "inverts" and r["to_strike"] == 110


def test_a_roll_under_the_minimum_credit_is_not_proposed():
    spot = 107.0
    rules = rules_on(min_credit=5.0)
    t = run(strangle(spot), spot, chain(spot, range(85, 112)), rules)
    assert "roll.untested_side" not in fired(t) and t["untested_roll"]["blocked"] == "credit"


def test_the_untested_roll_reads_the_snapshots_chains():
    spot = 107.0
    snapshot = {
        "session": SESSION.isoformat(),
        "positions": {
            "ok": True,
            "accounts": [
                {
                    "account": "****1234",
                    "balances": {"net_liquidating_value": 100000},
                    "underlyings": [{"underlying": "XYZ", "legs": strangle(spot)}],
                }
            ],
        },
        "metrics": {"XYZ": {"last": spot}},
        "roll_chains": chain(spot, range(85, 112)),
    }
    result = ev.evaluate(snapshot, rules_on())
    (trade,) = result["accounts"][0]["trades"]
    assert "roll.untested_side" in fired(trade)


def test_the_untested_roll_can_be_switched_off():
    spot = 107.0
    rules = _rules.effective(
        {"roll": {"untested_side": ON}, "positions": {"XYZ": {"disable": ["roll.untested_side"]}}}
    )[0]
    t = run(strangle(spot), spot, chain(spot, range(85, 112)), rules)
    assert "roll.untested_side" not in fired(t) and t["untested_roll"] is None
