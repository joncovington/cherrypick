"""Model a trade (model.py): proposed legs drawn with what the name holds. A leg with a price is taken
at it, one without at the name's IV index; the net credit, greeks and worst case are the proposed legs'
alone; a naked call's worst case is unbounded, never a number; the caps are the ruleset's, by the
account's kind. Synthetic names and prices, as in test_pmcc.py."""

from datetime import date

import pytest
from test_pmcc import SESSION, history, legs, snapshot

from cherrypick.manage import evaluate as ev
from cherrypick.manage import model
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES


def run(proposed, metrics=None, spot=50.0):
    snap = snapshot(
        history(), legs(), spot=spot, metrics={"iv_index": "0.30", "hv_30_day_pct": "24.0", **(metrics or {})}
    )
    snap["positions"]["accounts"][0]["balances"].update({"used_derivative_buying_power": "20000"})
    snap["positions"]["accounts"][0]["account_type"] = {
        "margin_or_cash": "Cash",
        "type_name": "Traditional IRA",
    }
    result = ev.evaluate(snap, RULES)
    return model.model(result, snap, RULES, "XYZ", proposed)


def test_a_cash_secured_put_at_a_given_price():
    out = run(["-1 2026-12-18 45P @ 1.05"])
    assert out["ok"] and out["net"] == pytest.approx(105.0)
    (leg,) = out["legs"]
    assert leg["price"] == 1.05 and leg["price_source"] == "the price given"
    assert -0.5 < leg["delta"] < 0 and leg["theta_day"] > 0  # a short put gains with time
    # Worst case at expiry, the stock at zero: the strike less the credit.
    assert out["worst_case"] == pytest.approx(-(4500 - 105), abs=0.5) and out["worst_case_at"] == 0.0
    assert out["unbounded"] is False
    # The payoff after includes what XYZ already holds; before is the holding alone.
    assert out["before"]["underlying"] == "XYZ" and out["after"]["cost"] == pytest.approx(
        out["before"]["cost"] - 105
    )
    assert out["iv_index"] == 0.30 and out["hv_30_day"] == pytest.approx(0.24)


def test_a_leg_without_a_price_is_priced_at_the_iv_index_and_says_so():
    out = run(["-1 2026-12-18 45P"])
    (leg,) = out["legs"]
    assert leg["iv"] == pytest.approx(0.30, abs=1e-3) and "IV index (30.0%)" in leg["price_source"]


def test_a_naked_call_is_unbounded():
    out = run(["-1 2026-12-18 60C @ 0.50"])
    assert out["unbounded"] is True and out["worst_case"] is None
    assert all(c["value"] is None for c in out["caps"])


def test_the_caps_are_the_ruleset_s_by_the_account_s_kind():
    out = run(["-1 2026-12-18 45P @ 1.05", "+1 2026-12-18 40P @ 0.35"])
    # A 5-wide put spread for 0.70: 430 at risk on 100,000.
    assert out["worst_case"] == pytest.approx(-430.0, abs=0.5)
    by = {c["label"]: c for c in out["caps"]}
    trade = by["this trade's worst case / net liq"]
    assert (
        trade["value"] == pytest.approx(0.0043)
        and trade["cap"] == RULES["portfolio"]["candidates"]["per_trade_risk_pct"]
    )
    assert trade["over"] is False
    assert (
        by["XYZ's worst case with it / net liq"]["cap"]
        == RULES["portfolio"]["candidates"]["notional_per_name_pct"]["cash"]
    )
    assert by["buying power in use with it / net liq"]["value"] == pytest.approx(
        (20000 + 430) / 100000, abs=1e-4
    )
    assert out["account_basis"] == "cash"


def test_what_cannot_be_modelled_says_why():
    assert run(["nonsense"])["error"].startswith("cannot read leg")
    assert run([])["error"] == "no leg to model"
    assert run(["-1 2026-01-16 45P @ 1"])["error"] == "2026-01-16 has already expired"
    assert run(["-1 2026-12-18 45P"], metrics={"iv_index": None})["error"].startswith("no price given")
    assert date.fromisoformat(SESSION)  # the session the fixtures share
