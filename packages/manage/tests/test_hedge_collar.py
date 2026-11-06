"""The hedge collar: a bought spread funded by a sold spread on the other side, at the net nearest zero.

Research item 55 declares the test of whether it earns anything; until then it is offered on request
and says so.
"""

import copy

import pytest
from test_hedge import METRICS, RULES, SESSION, _book, _chain

from cherrypick.manage import hedge


def _collar(out):
    return next(s for s in out["suggestions"] if s["kind"] == "collar")


def _rules(**collar):
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["hedge"]["collar"].update(collar)
    return rules


def _unit_legs(s):
    return [(leg["strike"], leg["right"], leg["quantity"] // s["units"], leg["mid"]) for leg in s["legs"]]


def test_a_book_too_long_buys_a_put_spread_and_sells_a_call_spread_and_lands_in_band():
    out = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES)
    assert [s["kind"] for s in out["suggestions"]] == ["credit", "debit", "collar"]
    s = _collar(out)
    assert s.get("error") is None
    assert (
        s["structure"] == "collar (put debit spread + call credit spread), no tested edge, research item 55"
    )
    assert s["bwd_change"] < 0 and s["after"]["in_band"]
    rights = sorted((leg["right"], leg["quantity"] > 0) for leg in s["legs"])
    assert rights == [("C", False), ("C", True), ("P", False), ("P", True)]
    # The bought put is the debit spread's long (near 40 delta); the sold call is out of the money.
    bought = next(leg for leg in s["legs"] if leg["right"] == "P" and leg["quantity"] > 0)
    sold = next(leg for leg in s["legs"] if leg["right"] == "C" and leg["quantity"] < 0)
    assert bought["delta"] == pytest.approx(-0.40, abs=0.03) and 0 < sold["delta"] < 0.5
    # Sized as the other kinds are: the need over one unit's delta, held to max_units.
    unit = s["bwd_change"] / s["units"]
    assert s["units"] == max(
        1, min(RULES["portfolio"]["hedge"]["max_units"], round(abs(out["need"]) / abs(unit)))
    )


def test_the_sold_strike_is_the_one_that_makes_the_net_nearest_zero():
    chain = _chain()
    s = _collar(hedge.suggest(_book(60.0), chain, METRICS, SESSION, RULES))
    mids = {(o["strike"], o["right"]): o["mid"] for o in chain["options"]}
    put_spread_credit = sum(-m * q for k, r, q, m in _unit_legs(s) if r == "P")  # negative: it is bought
    sold = next(k for k, r, q, _ in _unit_legs(s) if r == "C" and q < 0)
    width = RULES["portfolio"]["hedge"]["width"]

    def net(call_strike):
        return put_spread_credit + mids[(call_strike, "C")] - mids[(call_strike + width, "C")]

    chosen = abs(net(sold))
    floor = RULES["portfolio"]["hedge"]["collar"]["min_short_delta"]
    # Every out-of-the-money strike the chain could sell against does no better.
    for k in range(501, 540):
        assert chosen <= abs(net(float(k))) + 1e-9, k
    assert chosen < 0.05  # the 1-point grid gets it nearly to zero
    assert s["price"] == pytest.approx(net(sold), abs=0.006)
    assert s["natural"] is not None and s["natural"] < s["price"]
    assert floor < 0.5


def test_the_skew_sentence_is_made_only_where_the_chain_shows_it():
    def deltas(s):
        bought = next(leg for leg in s["legs"] if leg["right"] == "P" and leg["quantity"] > 0)
        sold = next(leg for leg in s["legs"] if leg["right"] == "C" and leg["quantity"] < 0)
        return sold["delta"], abs(bought["delta"])

    # Flat chain: the call is the nearer, and the note says so with both deltas.
    s = _collar(hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES))
    call, put = deltas(s)
    assert call > put
    assert f"nearer the money than its put: the sold call is {call:.2f} delta" in s["note"]
    # A steep skew cheapens the bought spread's lower strike and the sold call spread's wing, so the
    # spreads' wings put the call further out than the put; the note then says it does not hold here.
    s = _collar(hedge.suggest(_book(60.0), _chain(skew=0.4), METRICS, SESSION, RULES))
    call, put = deltas(s)
    assert call < put
    assert "it does not here" in s["note"]


def test_it_is_labelled_untested_and_says_what_it_gives_up():
    s = _collar(hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES))
    assert s["edge"] == "no tested edge, research item 55"
    assert s["note"].startswith("No tested edge, research item 55.")
    assert "gives up the upside past the" in s["note"] and "short call strike" in s["note"]
    # The other kinds carry no such label.
    for other in hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES)["suggestions"][:2]:
        assert "edge" not in other


def test_a_book_too_short_mirrors_it_with_a_call_debit_spread_and_a_put_credit_spread():
    out = hedge.suggest(_book(-60.0), _chain(), METRICS, SESSION, RULES)
    s = _collar(out)
    assert (
        s["structure"] == "collar (call debit spread + put credit spread), no tested edge, research item 55"
    )
    assert s["bwd_change"] > 0 and s["after"]["in_band"]
    bought = next(leg for leg in s["legs"] if leg["right"] == "C" and leg["quantity"] > 0)
    sold = next(leg for leg in s["legs"] if leg["right"] == "P" and leg["quantity"] < 0)
    assert bought["strike"] > 500 - 5 and sold["strike"] < 500
    assert "gives up the downside gain past the" in s["note"] and "short put strike" in s["note"]


def test_no_call_is_ever_naked_and_every_short_has_its_wing_further_out():
    for shares in (60.0, -60.0):
        s = _collar(hedge.suggest(_book(shares), _chain(skew=0.2), METRICS, SESSION, RULES))
        legs = _unit_legs(s)
        for right in ("C", "P"):
            assert sum(q for _, r, q, _ in legs if r == right) == 0, (shares, right)
        # Each side is a pair: one short, one long, the same size.
        for right in ("C", "P"):
            assert sorted(q for _, r, q, _ in legs if r == right) == [-1, 1], (shares, right)


def test_the_whole_worst_case_is_stated_and_equals_the_expiry_low():
    s = _collar(hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES))
    legs = _unit_legs(s)
    credit = sum(-q * m for _, _, q, m in legs)

    def pay(spot):
        return credit + sum(q * (max(spot - k, 0) if r == "C" else max(k - spot, 0)) for k, r, q, _ in legs)

    worst = -min(pay(x) for x in [0, *[k for k, *_ in legs], 2000])
    assert s["max_loss"] == pytest.approx(s["units"] * worst * 100, abs=s["units"] * 0.5 + 0.01)
    assert s["max_loss"] > 0


def test_the_switch_turns_only_the_collar_off():
    out = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, _rules(enabled=False))
    assert [s["kind"] for s in out["suggestions"]] == ["credit", "debit"]


def test_a_collar_is_held_under_the_buying_power_cap_like_a_credit_spread():
    book = _book(60.0)
    book["buying_power_used"] = 7600.0  # past 50% of 15,000
    s = _collar(hedge.suggest(book, _chain(), METRICS, SESSION, RULES))
    assert "buying-power cap" in s["error"] and "units" not in s
    # With a little room, the size is cut to fit and the book stays under the cap.
    book["buying_power_used"] = 7000.0
    s = _collar(hedge.suggest(book, _chain(), METRICS, SESSION, RULES))
    if s.get("error"):
        assert "buying-power cap" in s["error"]
    else:
        assert s["after"]["buying_power_used_pct"] <= 0.50 and not s["past_cap"]
        wide = _book(60.0)
        assert s["units"] <= _collar(hedge.suggest(wide, _chain(), METRICS, SESSION, RULES))["units"]


def test_a_chain_that_cannot_reach_cost_neutral_says_so_instead_of_offering_a_cost():
    out = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, _rules(max_net=0.0001))
    s = _collar(out)
    assert "cost-neutral" in s["error"] and s["edge"] == "no tested edge, research item 55"
    # The vertical kinds are untouched.
    assert out["suggestions"][0].get("error") is None


def test_a_short_that_is_nearly_worthless_is_not_sold_for_a_zero_net():
    # Raising the floor above any out-of-the-money delta leaves nothing to sell against.
    s = _collar(hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, _rules(min_short_delta=0.49)))
    assert s.get("error") is None or "strike" in s["error"]
