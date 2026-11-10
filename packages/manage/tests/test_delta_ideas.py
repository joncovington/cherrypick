"""The direction card's ideas (deltaideas.py): drivers, the two rolls, the fallback spread, the flags."""

import copy
from datetime import date

from cherrypick.manage import deltaideas, pricing
from cherrypick.manage import rules as _rules

SESSION = date(2026, 11, 9)
RATE = 0.04


def _rules_with(**cfg):
    r = copy.deepcopy(_rules.DEFAULT_RULES)
    r["pricing"]["rate"] = RATE
    r["portfolio"]["delta_ideas"].update(cfg)
    r["portfolio"]["delta_ideas"]["new_spread"] = False
    return r


def _years(expiry):
    return (date.fromisoformat(expiry) - SESSION).days / 365.0


def _mid(spot, k, expiry, right, iv):
    return round(pricing.price(spot, k, _years(expiry), iv, right, RATE, 0.0), 2)


def _leg(spot, k, expiry, right, qty, iv=0.25):
    years = _years(expiry)
    return {
        "right": right,
        "strike": float(k),
        "expiration": expiry,
        "quantity": float(qty),
        "dte": (date.fromisoformat(expiry) - SESSION).days,
        "years": years,
        "iv": iv,
        "div_yield": 0.0,
        "mid": _mid(spot, k, expiry, right, iv),
        "delta": pricing.delta(spot, k, years, iv, right, RATE, 0.0),
    }


def _chain(spot, expiry, right, strikes, iv=0.25, spread=0.02):
    out = []
    for k in strikes:
        m = _mid(spot, k, expiry, right, iv)
        out.append(
            {
                "strike": float(k),
                "right": right,
                "mid": m,
                "bid": round(m - spread, 2),
                "ask": round(m + spread, 2),
            }
        )
    return out


def _book(name="XYZ", beta=1.0, bench=100.0):
    return {
        "bwd": -10.0,
        "benchmark": "SPY",
        "benchmark_spot": bench,
        "net_liq": 20000.0,
        "theta": 50.0,
        "theta_ex_front": 50.0,
        "underlyings": [{"underlying": name, "beta": beta}],
    }


CONDOR_EXP = "2026-12-18"


def _condor(spot=100.0):
    # The call side tested (short call 3% away), the put side untested (short put 12% away).
    return {
        "trade_id": "XYZ-261105-1",
        "underlying": "XYZ",
        "spot": spot,
        "shape": "iron condor",
        "legs": [
            _leg(spot, 88, CONDOR_EXP, "P", -1),
            _leg(spot, 83, CONDOR_EXP, "P", 1),
            _leg(spot, 103, CONDOR_EXP, "C", -1),
            _leg(spot, 108, CONDOR_EXP, "C", 1),
        ],
        "fired": [],
    }


def _condor_snapshot(spot=100.0, spread=0.02):
    opts = _chain(spot, CONDOR_EXP, "P", range(75, 104), spread=spread) + _chain(
        spot, CONDOR_EXP, "C", range(95, 115), spread=spread
    )
    return {"roll_chains": {f"XYZ|{CONDOR_EXP}": {"options": opts}}}


def test_the_untested_put_side_rolls_up_for_a_credit_keeping_its_width():
    rules = _rules_with()
    out = deltaideas.suggest(_book(), [_condor()], _condor_snapshot(), {}, SESSION, rules)
    (idea,) = out["add"]
    assert idea["kind"] == "untested_side" and idea["trade_id"] == "XYZ-261105-1"
    assert idea["bwd_change"] > 0 and idea["price"] >= 0.10 and idea["buying_power_change"] == 0.0
    new_short, new_wing = idea["legs"][2], idea["legs"][3]
    assert (new_short["action"], new_wing["action"]) == ("STO", "BTO")
    short_k = float(new_short["label"].split()[1][:-1])
    wing_k = float(new_wing["label"].split()[1][:-1])
    assert short_k - wing_k == 5 and 88 < short_k < 103
    # The ruleset's own untested-side roll is off by default, and the idea says so.
    assert any(f["key"] == "rule_off" for f in idea["flags"])
    assert out["together"]["add"]["bwd_change"] == idea["bwd_change"]


def test_the_tested_side_is_never_rolled_toward_the_money():
    # Cutting delta would move the call side down, but the call side is the tested one.
    out = deltaideas.suggest(_book(), [_condor()], _condor_snapshot(), {}, SESSION, _rules_with())
    assert out["cut"] == [] and out["together"]["cut"] is None


def test_a_credit_at_mid_that_is_a_debit_at_the_natural_is_not_offered():
    wide = _condor_snapshot(spread=2.0)
    out = deltaideas.suggest(_book(), [_condor()], wide, {}, SESSION, _rules_with())
    assert out["add"] == []


FRONT, NEXT, LONG = "2026-11-13", "2026-11-20", "2026-12-18"


def _calendar(spot=110.0):
    # A put calendar at 100 with spot 10% above: short delta, and its short four days from expiry.
    return {
        "trade_id": "XYZ-261106-1",
        "underlying": "XYZ",
        "spot": spot,
        "shape": "calendar",
        "legs": [_leg(spot, 100, FRONT, "P", -1, iv=0.45), _leg(spot, 100, LONG, "P", 1, iv=0.40)],
        "fired": [{"rule": "risk.oversize", "action": "REVIEW", "reason": "3.0x the budget"}],
    }


def _roll_out(spot=110.0, expiries=(NEXT, LONG)):
    return {
        "roll_out_chains": {
            "chains": {
                f"XYZ|P|{e}": {"options": _chain(spot, e, "P", range(90, 116), iv=0.45)} for e in expiries
            }
        }
    }


def test_a_front_short_rolls_to_the_strike_that_adds_most_and_says_it_inverts():
    rules = _rules_with()
    metrics = {"XYZ": {"earnings_date": "2026-11-18"}}
    out = deltaideas.suggest(_book(), [_calendar()], _roll_out(), metrics, SESSION, rules)
    (idea,) = out["add"]
    assert idea["kind"] == "front_roll" and idea["new_expiry"] == NEXT  # never the long's own expiry
    new = idea["legs"][1]
    assert new["action"] == "STO" and abs(new["delta"]) <= 0.40
    k = float(new["label"].split()[1][:-1])
    assert k > 100  # above the long put: an inverted spread, collateral stated
    assert idea["buying_power_change"] == (k - 100) * 100
    keys = {f["key"] for f in idea["flags"]}
    assert keys == {"oversize", "inverts", "earnings"}
    # The plain roll (same strike, next week) moves delta far less than the idea.
    assert idea["plain_bwd_change"] is not None and idea["bwd_change"] > idea["plain_bwd_change"]


def test_a_short_further_out_than_front_days_is_not_rolled():
    out = deltaideas.suggest(_book(), [_calendar()], _roll_out(), {}, SESSION, _rules_with(front_days=3))
    assert out["add"] == [] and out["unquoted"] == []


def test_a_roll_with_no_quoted_chain_is_named_not_dropped():
    out = deltaideas.suggest(_book(), [_calendar()], {}, {}, SESSION, _rules_with())
    assert out["add"] == []
    assert out["unquoted"] == [
        {
            "trade_id": "XYZ-261106-1",
            "underlying": "XYZ",
            "why": "no quoted expiry to roll the 11-13 100P short to",
        }
    ]


def test_drivers_are_largest_first_with_beta_weighting():
    book = {**_book(), "underlyings": [{"underlying": "XYZ", "beta": 2.0}]}
    out = deltaideas.suggest(book, [_condor(), _calendar()], {}, {}, SESSION, _rules_with())
    sizes = [abs(d["bwd"]) for d in out["drivers"]]
    assert sizes == sorted(sizes, reverse=True)
    calendar = next(d for d in out["drivers"] if d["trade_id"] == "XYZ-261106-1")
    legs = _calendar()["legs"]
    raw = sum(leg["quantity"] * 100 * leg["delta"] for leg in legs)
    assert calendar["bwd"] == round(raw * 2.0 * 110.0 / 100.0, 2)
    assert calendar["reason"] == "the 100P short is 9.1% below spot (110)"


def test_turned_off_there_is_no_block():
    r = _rules_with()
    r["portfolio"]["delta_ideas"]["enabled"] = False
    assert deltaideas.suggest(_book(), [_condor()], {}, {}, SESSION, r) is None


PMCC_SHORT, PMCC_NEXT, PMCC_LONG = "2026-12-18", "2027-01-15", "2027-06-17"


def _pmcc_trade(spot=100.0):
    # A deep long call and a monthly short 39 days out: well past `front_days`.
    return {
        "trade_id": "XYZ-261001-1",
        "underlying": "XYZ",
        "spot": spot,
        "shape": "diagonal",
        "legs": [_leg(spot, 80, PMCC_LONG, "C", 1), _leg(spot, 108, PMCC_SHORT, "C", -1)],
        "pmcc": {"recognized": True},
        "fired": [],
    }


def _pmcc_view(net_cost_per_share):
    return {
        "positions": [
            {
                "underlying": "XYZ",
                "recognized": True,
                "reconciled": True,
                "div_yield": 0.0,
                "width": {"net_cost_per_share": net_cost_per_share},
            }
        ]
    }


def _pmcc_snapshot(spot=100.0):
    chains = {}
    for e in (PMCC_SHORT, PMCC_NEXT):
        chains[f"XYZ|C|{e}"] = {
            "underlying": "XYZ",
            "right": "C",
            "expiration": e,
            "options": _chain(spot, e, "C", range(95, 121)),
        }
    return {"roll_out_chains": {"chains": chains}}


def test_a_pmcc_is_named_as_a_stock_substitute_and_rolled_by_its_own_rules():
    rules = _rules_with()
    out = deltaideas.suggest(
        _book(), [_pmcc_trade()], _pmcc_snapshot(), {}, SESSION, rules, pmcc=_pmcc_view(19.0)
    )
    (driver,) = out["drivers"]
    assert driver["pmcc"] is True and driver["reason"].startswith("PMCC, a stock substitute")
    # Cutting delta rolls the short down and out, though it is 39 days from expiry (no front-week gate).
    (idea,) = out["cut"]
    assert idea["kind"] == "pmcc_roll" and idea["new_expiry"] == PMCC_NEXT
    new = idea["legs"][1]
    k = float(new["label"].split()[1][:-1])
    assert new["delta"] <= rules["pmcc"]["roll"]["max_delta"]
    assert idea["price"] >= -rules["pmcc"]["roll"]["max_debit"]
    # The width check holds: the short less the long still beats what the PMCC has cost.
    assert k - 80 > 19.0 - idea["price"]
    assert any(f["key"] in ("width", "thin") for f in idea["flags"])


def test_a_pmcc_roll_never_breaks_the_width_check():
    rules = _rules_with()
    loose = deltaideas.suggest(
        _book(), [_pmcc_trade()], _pmcc_snapshot(), {}, SESSION, rules, pmcc=_pmcc_view(0.0)
    )
    tight = deltaideas.suggest(
        _book(), [_pmcc_trade()], _pmcc_snapshot(), {}, SESSION, rules, pmcc=_pmcc_view(28.0)
    )
    strike = lambda out: float(out["cut"][0]["legs"][1]["label"].split()[1][:-1])  # noqa: E731
    # With room to spare the short goes as low as its delta cap allows; a costlier PMCC stops it higher.
    assert strike(tight) > strike(loose)
    assert strike(tight) - 80 > 28.0 - tight["cut"][0]["price"]


def test_an_unrecognised_diagonal_stays_on_the_time_spread_path():
    trade = {**_pmcc_trade(), "pmcc": {"recognized": False}}
    out = deltaideas.suggest(
        _book(), [trade], _pmcc_snapshot(), {}, SESSION, _rules_with(), pmcc=_pmcc_view(19.0)
    )
    assert out["drivers"][0]["pmcc"] is False
    # Its short is 39 days out, past `front_days`: no roll offered.
    assert [i["kind"] for i in out["add"] + out["cut"]] == []
