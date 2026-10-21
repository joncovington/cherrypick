"""Roll candidates on the snapshot's roll-out chains (v14 `roll_out_chains`): the next expiries after
a short, quoted that session. A PMCC's short and a wheel's tested short each find a roll on an expiry
the income reading never quoted, and a snapshot without the field is judged exactly as before.
Synthetic names, prices and histories, as in test_pmcc.py and test_wheel_rules.py."""

import copy

import pytest
import test_pmcc as P
import test_wheel_rules as W
from test_wheel import opt_leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules
from cherrypick.manage import wheel

TAKEN = "2026-10-13T14:30:00-0400"


def chains(*entries):
    """A roll_out_chains block: each entry (underlying, right, expiry, [(strike, bid, ask), ...])."""
    out = {}
    for name, right, exp, book in entries:
        out[f"{name}|{right}|{exp}"] = {
            "underlying": name,
            "right": right,
            "expiration": exp,
            "options": [
                {"strike": float(k), "right": right, "bid": b, "ask": a, "mid": (b + a) / 2}
                for k, b, a in book
            ],
            "error": None,
        }
    return {"taken_at": TAKEN, "chains": out}


# --------------------------------------------------------------------------- PMCC
def _pmcc_snap(roll_out=None):
    """The narrow PMCC of test_pmcc (a 45 short bought back at 0.80, spot 43, width failing at 7.62),
    with an income reading whose December quotes cannot pay for the roll: a dime is all the 60 pays."""
    snap = P.snapshot(
        P.history(short=P.NARROW, short_price=6.00),
        P.legs(short=P.NARROW, short_price=6.00, short_mid=0.80, long_mid=8.0),
        spot=43.0,
        income=P.income(P.quote(60, 0.10, 0.20)),
    )
    if roll_out is not None:
        snap["roll_out_chains"] = roll_out
    return snap


def test_a_pmcc_roll_is_found_on_an_expiry_the_income_reading_lacked():
    old = P.view_of(P.evaluate(_pmcc_snap()))
    assert old["rolls"]["candidates"] == [] and old["rolls"]["quoted_expiries"] == [P.DEC]
    # The chain read for the short: the week after it quotes a 48 that pays the buy-back.
    ro = chains(("XYZ", "C", "2026-11-27", [(47, 1.05, 1.15), (48, 0.80, 0.90), (50, 0.30, 0.40)]))
    a = P.evaluate(_pmcc_snap(ro))
    v = P.view_of(a)
    assert v["rolls"]["quoted_expiries"] == ["2026-11-27", P.DEC]
    (c,) = v["rolls"]["candidates"]
    assert c["expiration"] == "2026-11-27" and c["open"]["strike"] == 48.0 and c["delta"] <= 0.40
    assert c["net"] == pytest.approx(0.05)
    # Still judged by check-roll, as every candidate is.
    assert c["check"]["verdict"] in ("ok", "risky") and c["legs"][1].startswith("STO 1 2026-11-27 48C")
    assert v["rolls"]["chains_taken_at"] == TAKEN
    assert "2026-11-27 48C" in P.fired(P.trade_of(a, P.NARROW))["pmcc.width"]["reason"]


def test_a_pmcc_reads_only_its_own_name_and_calls():
    ro = chains(
        ("ABC", "C", "2026-11-27", [(48, 0.80, 0.90)]),
        ("XYZ", "P", "2026-11-27", [(48, 0.80, 0.90)]),
    )
    assert P.view_of(P.evaluate(_pmcc_snap(ro)))["rolls"]["candidates"] == []


# --------------------------------------------------------------------------- wheel
def _wheel_eval(income=None, roll_out=None):
    acct = {
        "account": "****1234",
        "designated": True,
        "underlyings": [{"underlying": "XYZ", "legs": [opt_leg(W.PUT, -1, 1.20, 1.00)]}],
        "balances": {"net_liquidating_value": 100_000},
    }
    snap = {
        "session": W.SESSION,
        "positions": {"ok": True, "accounts": [acct]},
        "metrics": {"XYZ": {"last": 50.0}},
        "transactions": {"XYZ": W.OPENED},
        "working_orders": [],
        "income": income,
        "skate_bands": None,
    }
    if roll_out is not None:
        snap["roll_out_chains"] = roll_out
    out = ev.evaluate(snap, _rules.effective(None)[0])["accounts"][0]
    t = W.trade_of(out, W.PUT)
    return t, t["wheel"][0]["rolls"][0]


def test_a_tested_put_rolls_to_an_expiry_the_income_reading_lacked():
    # Before: no income quote for the name, so no roll priced.
    t, roll = _wheel_eval(income=None)
    assert roll["net_zero"] is None and "No roll priced" in W.fired(t)["wheel.tested"]["reason"]
    # The chains read for the short put: a week out nothing pays the 1.20 buy-back at or under the
    # 49.80 cap; two weeks out the 49 does. A call chain on the name is not this short's.
    ro = chains(
        ("XYZ", "C", "2026-11-24", [(55, 1.30, 1.40)]),
        ("XYZ", "P", "2026-11-27", [(50, 1.30, 1.40), (49, 0.95, 1.05)]),
        ("XYZ", "P", "2026-12-04", [(50, 1.70, 1.80), (49, 1.25, 1.35), (48, 0.90, 1.00)]),
    )
    t, roll = _wheel_eval(income=None, roll_out=ro)
    assert roll["expiration"] == "2026-12-04" and roll["source"] == "roll-out chains"
    assert roll["considered"] == [
        {"expiration": "2026-11-27", "net_zero": False},
        {"expiration": "2026-12-04", "net_zero": True},
    ]
    assert roll["net_zero"]["open"]["strike"] == 49 and roll["net_zero"]["net"] == pytest.approx(0.10)
    assert roll["premium_gambit"]["open"]["strike"] == 50
    # Each still judged by check-roll.
    assert roll["net_zero"]["check"]["verdict"] in ("ok", "risky")
    assert roll["premium_gambit"]["check"] is not None
    reason = W.fired(t)["wheel.tested"]["reason"]
    assert "Rolls to 2026-12-04 at this session's quotes" in reason


def test_a_wheel_roll_with_no_net_zero_anywhere_takes_the_nearest_quoted_expiry():
    ro = chains(
        ("XYZ", "P", "2026-12-04", [(50, 1.10, 1.20)]),
        ("XYZ", "P", "2026-11-27", [(50, 0.90, 1.00)]),
    )
    _, roll = _wheel_eval(roll_out=ro)
    assert roll["expiration"] == "2026-11-27" and roll["net_zero"] is None
    assert roll["premium_gambit"]["open"]["strike"] == 50
    assert [c["expiration"] for c in roll["considered"]] == ["2026-11-27", "2026-12-04"]


def test_chains_that_quote_nothing_usable_fall_back_to_the_income_reading():
    # Calls only (the wrong right), and puts only toward the money: neither prices a put roll.
    ro = chains(("XYZ", "C", "2026-11-27", [(50, 1.0, 1.1)]), ("XYZ", "P", "2026-11-27", [(52, 3.0, 3.1)]))
    _, with_chains = _wheel_eval(income=W.INCOME, roll_out=ro)
    _, without = _wheel_eval(income=W.INCOME)
    assert with_chains == without and with_chains["expiration"] == W.LATER


def test_without_chains_the_roll_is_the_income_reading_s_as_before():
    today = {"right": "P", "strike": 50.0, "expiration": "2026-11-20", "mid": 1.20, "open_price": 1.00}
    entry = {**W.INCOME["symbols"]["XYZ"], "taken_at": W.INCOME["taken_at"]}
    assert wheel.roll_candidates(today, entry) == wheel.roll_candidates(today, entry, [])
    assert wheel.roll_candidates(today, copy.deepcopy(entry), None) == wheel._roll_on(today, entry)
    assert wheel.roll_out_for(None, "XYZ", "P") == [] and wheel.roll_out_for({}, "XYZ", "P") == []
