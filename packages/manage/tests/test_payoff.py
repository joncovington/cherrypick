"""The payoff (payoff.py): each held name's P&L across its price. At expiry it is plain arithmetic
(intrinsic less cost); today's curve passes through today's P&L at today's price, because every vol
is solved from its leg's mid; an index is European and an equity American; a leg no vol prices nulls
the dated curves rather than drawing them on a guess. Synthetic names and prices."""

from datetime import date

import pytest
from cherrypick.core import american, bs

from cherrypick.manage import payoff
from cherrypick.manage import rules as _rules

SESSION = date(2026, 11, 4)
RULES = _rules.DEFAULT_RULES
RATE = float(RULES["pricing"]["rate"])


def leg(right, strike, qty, mid, cost_per_share, exp="2026-12-18", name="XYZ"):
    days = (date.fromisoformat(exp) - SESSION).days
    value = None if mid is None else qty * 100 * mid
    cost = qty * 100 * cost_per_share
    return {
        "underlying": name,
        "right": right,
        "strike": strike,
        "expiration": exp,
        "years": days / 365.0,
        "quantity": qty,
        "mid": mid,
        "value": value,
        "cost": cost,
        "open_pl": None if value is None else value - cost,
        "label": f"{qty:+g} {exp} {strike:g}{right}",
    }


def read(legs, spot=50.0, name="XYZ", hist=None):
    trades = [{"underlying": name, "legs": legs}]
    snap = {"underlying_history": {name: hist} if hist else {}}
    (n,) = payoff.reading(trades, {name: {"last": spot}}, snap, SESSION, RULES)["names"]
    return n


def at(n, curve, s):
    """The curve's P&L at the grid point nearest `s`."""
    i = min(range(len(n["grid"])), key=lambda j: abs(n["grid"][j] - s))
    return n["grid"][i], n["curves"][curve]["pnl"][i]


def test_a_short_put_at_expiry_is_intrinsic_less_cost_with_its_breakeven():
    n = read([leg("P", 48, -1, 0.90, 1.20)])
    expiry = n["curves"][-1]
    assert expiry["expiry"] and expiry["label"] == "at expiry 2026-12-18"
    for s, pnl in zip(n["grid"], expiry["pnl"], strict=True):
        assert pnl == pytest.approx(-100 * max(48 - s, 0) + 120, abs=0.01)
    (be,) = n["breakevens"]
    assert be == pytest.approx(46.80, abs=0.05)
    assert n["cost"] == -120 and n["pnl_now"] == pytest.approx(30)


def test_today_s_curve_reprices_every_mid_at_today_s_price():
    legs = [leg("P", 48, -1, 0.90, 1.20), leg("P", 45, 1, 0.35, 0.50)]
    n = read(legs, spot=50.0)
    # Evaluate the model at exactly today's price: the solved vols give back each mid.
    iv = {r["leg"]: r["iv"] for r in n["legs"]}
    years = legs[0]["years"]
    again = sum(
        lg["quantity"] * 100 * american.price(50.0, lg["strike"], years, iv[lg["label"]], "P", RATE, 0.0)
        for lg in legs
    )
    assert again - n["cost"] == pytest.approx(n["pnl_now"], abs=0.05)
    assert [c["label"] for c in n["curves"]] == ["today", "2026-11-11", "2026-11-18", "at expiry 2026-12-18"]
    assert n["model"] == "american" and n["yield_source"].startswith("no dividend record")


def test_an_index_is_european_and_an_equity_american():
    spx = read([leg("P", 5000, -1, 40.0, 45.0, name="SPX")], spot=5100.0, name="SPX")
    assert spx["model"] == "european"
    (row,) = spx["legs"]
    years = (date(2026, 12, 18) - SESSION).days / 365.0
    assert bs.price(5100.0, 5000, years, row["iv"], "P", RATE, 0.0) == pytest.approx(40.0, abs=0.01)


def test_the_yield_comes_from_the_dividends_on_record():
    hist = {
        "dividends": [["2026-02-01", 0.25], ["2026-05-01", 0.25], ["2026-08-01", 0.25], ["2026-10-30", 0.25]]
    }
    n = read([leg("C", 52, -1, 1.0, 1.1)], spot=50.0, hist=hist)
    assert n["div_yield"] == pytest.approx(1.0 / 50.0) and n["yield_source"] == "the last year's dividends"


def test_a_leg_no_vol_prices_nulls_the_dated_curves_but_not_expiry():
    n = read([leg("P", 48, -1, None, 1.20)])
    assert n["legs"][0]["iv"] is None
    assert all(c["pnl"] is None for c in n["curves"][:-1]) and n["curves"][-1]["pnl"] is not None


def test_a_name_with_no_price_or_no_cost_says_why():
    trades = [{"underlying": "XYZ", "legs": [leg("P", 48, -1, 0.9, 1.2)]}]
    (n,) = payoff.reading(trades, {}, {}, SESSION, RULES)["names"]
    assert n["why"] == "no price for the underlying"
    bad = leg("P", 48, -1, 0.9, 1.2)
    bad["cost"] = None
    assert read([bad])["why"] == "a leg has no cost"


def test_an_expiring_name_has_only_today_at_expiry():
    n = read([leg("P", 48, -1, 0.10, 0.50, exp="2026-11-06")])
    assert [c["label"] for c in n["curves"]] == ["today", "at expiry 2026-11-06"]
