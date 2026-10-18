"""Sizing an XSP vertical that brings the book's beta-weighted delta back toward its band."""

import copy
from datetime import date

import pytest

from cherrypick.manage import hedge, portfolio, pricing
from cherrypick.manage import rules as _rules

RULES = _rules.DEFAULT_RULES
SESSION = date(2026, 10, 19)
SPOT = 500.0
DTE = 45
IV = 0.18


def _chain(spot=SPOT, iv=IV, strikes=range(440, 561), spread=0.10):
    """A flat-IV chain priced by the model itself, so every leg's IV solves back to `iv`."""
    years, options = DTE / 365.0, []
    for k in strikes:
        for right in ("C", "P"):
            mid = pricing.price(spot, float(k), years, iv, right, 0.04)
            if mid < 0.05:
                continue
            options.append(
                {
                    "strike": float(k),
                    "right": right,
                    "mid": round(mid, 4),
                    "bid": round(mid - spread / 2, 4),
                    "ask": round(mid + spread / 2, 4),
                }
            )
    return {
        "root": "XSP",
        "expiration": "2026-12-03",
        "dte": DTE,
        "spot": spot,
        "options": options,
        "error": None,
    }


def _book(shares, theta=40.0):
    """A reading whose BWD is `shares` SPY shares, with the band set by `theta`."""
    r = {
        "bwd": float(shares),
        "theta": theta,
        "net_liq": 15000.0,
        "benchmark_spot": SPOT,
        "gamma_bench": -0.4,
        "buying_power_used": 1000.0,  # room under the cap, so sizing is the need alone
    }
    lo, hi = portfolio.delta_band(r, RULES["portfolio"]["delta"])
    fired = not lo <= shares <= hi
    r["guidance"] = [{"rule": "portfolio.delta"}] if fired else []
    return r


METRICS = {"XSP": {"beta": "1.0"}}


def test_inside_the_band_no_hedge_is_offered():
    assert hedge.suggest(_book(5.0), _chain(), METRICS, SESSION, RULES) is None


def test_too_long_sells_calls_and_buys_puts_and_lands_in_band():
    out = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES)
    assert out["error"] is None and out["need"] < 0
    credit, debit = out["suggestions"]
    assert credit["structure"] == "call credit spread" and debit["structure"] == "put debit spread"
    for s in (credit, debit):
        assert s["bwd_change"] < 0
        assert s["after"]["in_band"], s
        short, long_ = sorted(s["legs"], key=lambda leg: leg["quantity"])
        assert abs(short["strike"] - long_["strike"]) == RULES["portfolio"]["hedge"]["width"]
    # The credit spread is short near 30 delta, its wing further out; it takes in cash and adds theta.
    short = next(leg for leg in credit["legs"] if leg["quantity"] < 0)
    assert short["delta"] == pytest.approx(0.30, abs=0.03)
    assert credit["cash"] > 0 and credit["theta_change"] > 0
    # The price is shown to the cent and max loss uses the exact mid: half a cent a share a spread.
    tolerance = credit["units"] * 0.5 + 0.01
    assert credit["max_loss"] == pytest.approx(credit["units"] * (5.0 - credit["price"]) * 100, abs=tolerance)
    assert debit["cash"] < 0 and debit["max_loss"] == pytest.approx(-debit["cash"])


def test_a_hedge_never_takes_buying_power_past_its_cap():
    book = _book(60.0)
    book["buying_power_used"] = 7000.0  # 46.7% of 15,000: $500 left under the 50% cap
    out = hedge.suggest(book, _chain(), METRICS, SESSION, RULES)
    for s in out["suggestions"]:
        if s.get("error"):
            assert "buying-power cap" in s["error"]
            continue
        assert s["after"]["buying_power_used_pct"] <= 0.50
    book["buying_power_used"] = 7600.0  # past the cap already: nothing fits
    assert all(
        "buying-power cap" in s["error"]
        for s in hedge.suggest(book, _chain(), METRICS, SESSION, RULES)["suggestions"]
    )


def test_too_short_sells_puts_and_buys_calls():
    out = hedge.suggest(_book(-60.0), _chain(), METRICS, SESSION, RULES)
    credit, debit = out["suggestions"]
    assert credit["structure"] == "put credit spread" and debit["structure"] == "call debit spread"
    assert credit["bwd_change"] > 0 and debit["bwd_change"] > 0


def test_the_size_is_the_need_over_one_spreads_delta():
    out = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES)
    credit = out["suggestions"][0]
    unit = credit["bwd_change"] / credit["units"]
    assert credit["units"] == max(1, round(abs(out["need"]) / abs(unit)))
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["hedge"]["max_units"] = 1
    assert hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, rules)["suggestions"][0]["units"] == 1


def test_the_hedge_roots_beta_and_price_scale_its_delta():
    # XSP a little above SPY moves a little more than SPY per contract.
    base = hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, RULES)["suggestions"][0]
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["hedge"]["max_units"] = base["units"]
    half = hedge.suggest(_book(60.0), _chain(), {"XSP": {"beta": "0.5"}}, SESSION, rules)["suggestions"][0]
    assert half["bwd_change"] / half["units"] == pytest.approx(
        0.5 * base["bwd_change"] / base["units"], abs=0.01
    )


def test_the_natural_price_sells_at_the_bid_and_buys_at_the_ask():
    credit = hedge.suggest(_book(60.0), _chain(spread=0.20), METRICS, SESSION, RULES)["suggestions"][0]
    assert credit["natural"] == pytest.approx(credit["price"] - 0.20, abs=1e-3)


def test_no_chain_or_no_beta_is_said_never_guessed():
    missing = hedge.suggest(
        _book(60.0), {"root": "XSP", "options": [], "error": "timed out"}, METRICS, SESSION, RULES
    )
    assert missing["error"] == "timed out" and missing["suggestions"] == []
    no_beta = hedge.suggest(_book(60.0), _chain(), {}, SESSION, RULES)
    assert "no beta" in no_beta["error"]


def test_the_switch_and_the_delta_rule_both_turn_it_off():
    rules = copy.deepcopy(RULES)
    rules["portfolio"]["hedge"]["enabled"] = False
    assert hedge.suggest(_book(60.0), _chain(), METRICS, SESSION, rules) is None
    quiet = _book(60.0)
    quiet["guidance"] = []  # portfolio.delta did not fire (disabled)
    assert hedge.suggest(quiet, _chain(), METRICS, SESSION, RULES) is None
