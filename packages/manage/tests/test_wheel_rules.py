"""The wheel rules: buy-to-close, the tested short's rolls, the next leg after an assignment, and the
review an unreconciled cycle gets instead. Synthetic histories, as in test_wheel.py."""

import copy
from datetime import date

import pytest
from test_wheel import assigned, occ, opt_leg, rd, share_leg, tx

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rollcheck
from cherrypick.manage import rules as _rules

SESSION = "2026-10-13"
PUT = occ("2026-11-20", "P", 50)
CALL = occ("2026-11-20", "C", 55)
LATER = "2026-12-18"
OPENED = [tx(1, "2026-10-01T15:00:00Z", "Sell to Open", PUT, 1, 1.00)]


def quote(strike, right, bid, ask):
    return {"strike": strike, "right": right, "bid": bid, "ask": ask, "option": occ(LATER, right, strike)}


INCOME = {
    "taken_at": "2026-10-12T18:45:00Z",
    "error": None,
    "symbols": {
        "XYZ": {
            "expiry": LATER,
            "options": [
                quote(51, "P", 3.70, 3.90),
                quote(50, "P", 3.20, 3.40),
                quote(49, "P", 2.60, 2.80),
                quote(48, "P", 2.10, 2.30),
                quote(47, "P", 1.70, 1.90),
                quote(55, "C", 1.10, 1.20),
                quote(56, "C", 0.80, 0.90),
                quote(57, "C", 0.55, 0.65),
            ],
        }
    },
}


def evaluate(history, legs, spot, orders=None, income=None, bands=None, overrides=None, metrics=None):
    acct = {
        "account": "****1234",
        "designated": True,
        "underlyings": [{"underlying": "XYZ", "legs": legs}],
        "balances": {"net_liquidating_value": 100_000},
    }
    snap = {
        "session": SESSION,
        "positions": {"ok": True, "accounts": [acct]},
        "metrics": {"XYZ": {"last": spot, **(metrics or {})}},
        "transactions": {"XYZ": history},
        "working_orders": orders or [],
        "income": income,
        "skate_bands": bands,
    }
    out = ev.evaluate(snap, _rules.effective(overrides)[0])
    return out["accounts"][0]


def trade_of(account, symbol):
    return next(t for t in account["trades"] if any(leg["symbol"] == symbol for leg in t["legs"]))


def fired(trade):
    return {f["rule"]: f for f in trade["fired"]}


# --------------------------------------------------------------------------- wheel.take_profit
def test_a_short_marked_at_its_buy_to_close_is_taken():
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.03, 1.00)], 60.0), PUT)
    f = fired(t)["wheel.take_profit"]
    assert f["action"] == ev.TAKE_PROFIT and f["confidence"] == 80
    assert "0.03 buy-to-close (the default)" in f["reason"]
    assert t["wheel"][0]["take_profit"][0]["hit"] is True


def test_a_short_above_its_buy_to_close_is_not():
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.04, 1.00)], 60.0), PUT)
    assert "wheel.take_profit" not in fired(t)
    assert t["wheel"][0]["take_profit"][0] == {
        "leg": "2026-11-20 50P",
        "mid": 0.04,
        "btc_price": 0.03,
        "source": "the default",
        "hit": False,
    }


def test_a_wide_market_buys_back_at_a_nickel():
    leg = {**opt_leg(PUT, -1, 0.04, 1.00), "wide": True}
    t = trade_of(evaluate(OPENED, [leg], 60.0), PUT)
    assert "wheel.take_profit" in fired(t)
    assert t["wheel"][0]["take_profit"][0]["btc_price"] == 0.05


def test_a_resting_buy_to_close_sets_the_price():
    order = {
        "id": 9,
        "status": "Live",
        "price": "0.10",
        "legs": [{"symbol": PUT, "action": "Buy to Close", "quantity": 1, "remaining_quantity": 1}],
    }
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.08, 1.00)], 60.0, orders=[order]), PUT)
    f = fired(t)["wheel.take_profit"]
    assert "the resting buy-to-close" in f["reason"]
    # A filled or cancelled order is not resting: back to the default, which 0.08 is above.
    done = {**order, "status": "Filled"}
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.08, 1.00)], 60.0, orders=[done]), PUT)
    assert "wheel.take_profit" not in fired(t)


def test_the_take_profit_buys_back_the_call_never_sells_the_shares():
    history = [
        tx(1, "2026-10-01T15:00:00Z", "Buy to Open", "XYZ", 100, 50.0, kind="Equity"),
        tx(2, "2026-10-01T15:01:00Z", "Sell to Open", CALL, 1, 0.80),
    ]
    legs = [share_leg(100, 52.0, 50.0), opt_leg(CALL, -1, 0.02, 0.80)]
    # Grouped by underlying, the shares and the call are one trade; the buy-back is the call alone.
    overrides = {"trade_grouping": "underlying", "positions": {"XYZ": {"disable": ["credit.take_profit"]}}}
    t = trade_of(evaluate(history, legs, 52.0, overrides=overrides), CALL)
    assert len(t["legs"]) == 2
    assert t["recommendation"]["rule"] == "wheel.take_profit"
    assert [(row["side"], row["leg"]) for row in t["order"]["legs"]] == [("BTC", "-1 2026-11-20 55C")]


# --------------------------------------------------------------------------- wheel.tested
def test_a_tested_put_alone_is_rolled_or_closed_with_both_rolls_priced():
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 1.20, 1.00)], 50.0, income=INCOME), PUT)
    f = fired(t)["wheel.tested"]
    assert f["action"] == ev.ROLL_OR_CLOSE and f["confidence"] == 50
    assert t["recommendation"]["rule"] == "wheel.tested"
    assert [row["side"] for row in t["order"]["legs"]] == ["BTC"]
    (roll,) = t["wheel"][0]["rolls"]
    # Net-zero: the furthest put that pays the 1.20 buy-back, capped at 50 + 1.00 - 1.20 = 49.80.
    nz = roll["net_zero"]
    assert nz["open"]["strike"] == 47 and nz["net"] == pytest.approx(0.60)
    assert nz["check"]["verdict"] in ("ok", "risky")
    # Premium gambit: the buy-back rests at the 1.00 it was sold for; the same strike, a month out.
    pg = roll["premium_gambit"]
    assert pg["close_price"] == 1.00 and pg["open"]["strike"] == 50
    assert pg["check"]["credit"] == pytest.approx((3.30 - 1.00) * 100)
    assert "net-zero" in f["reason"] and "premium gambit" in f["reason"]


def test_an_untested_put_is_not():
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.80, 1.00)], 51.0, income=INCOME), PUT)
    assert "wheel.tested" not in fired(t)
    assert t["wheel"][0]["rolls"] == []


def test_the_net_zero_roll_never_moves_toward_the_money_and_is_capped():
    # A 2.40 buy-back: 49 and 50 pay it but sit above the 48.60 cap; 48 pays only 2.20.
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 2.40, 1.00)], 48.0, income=INCOME), PUT)
    (roll,) = t["wheel"][0]["rolls"]
    assert roll["net_zero"] is None and "48.6" in roll["net_zero_why"]
    # The 51 put pays far more, and is never offered: it is toward the money.
    assert all(r["open"]["strike"] <= 50 for r in (roll["net_zero"], roll["premium_gambit"]) if r)


def test_a_quoted_chain_no_later_than_the_short_is_no_roll():
    same = {**INCOME, "symbols": {"XYZ": {**INCOME["symbols"]["XYZ"], "expiry": "2026-11-20"}}}
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 1.20, 1.00)], 50.0, income=same), PUT)
    (roll,) = t["wheel"][0]["rolls"]
    assert roll["net_zero"] is None and roll["premium_gambit"] is None
    assert "not later than the short's" in roll["why"]


def test_no_quoted_chain_means_no_roll_priced_never_a_guess():
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 1.20, 1.00)], 50.0, income=None), PUT)
    f = fired(t)["wheel.tested"]
    assert "No roll priced" in f["reason"]
    assert t["wheel"][0]["rolls"][0]["net_zero"] is None


def test_a_stop_still_outranks_the_wheel_roll():
    # Down 1.70 on a 1.00 credit: the credit stop (a close) ranks above the wheel's roll-or-close.
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 2.70, 1.00)], 48.0, income=INCOME), PUT)
    names = fired(t)
    assert "wheel.tested" in names and "credit.stop" in names
    assert t["recommendation"]["action"] in (ev.CLOSE, ev.ROLL_FOR_CREDIT)
    assert t["recommendation"]["rule"] != "wheel.tested"


def test_a_roll_through_earnings_is_rejected_by_check_roll():
    t = trade_of(
        evaluate(
            OPENED,
            [opt_leg(PUT, -1, 1.20, 1.00)],
            50.0,
            income=INCOME,
            metrics={"earnings_date": "2026-12-01"},
        ),
        PUT,
    )
    roll = t["wheel"][0]["rolls"][0]
    assert roll["net_zero"]["check"]["verdict"] == "reject"


def test_check_roll_counts_shares_behind_a_covered_call():
    legs = [
        {
            "quantity": -1,
            "expiration": "2026-11-20",
            "strike": 55.0,
            "right": "C",
            "mid": 1.50,
            "years": 38 / 365,
            "iv": 0.30,
        }
    ]
    trade = {"legs": legs, "cost": -80.0, "shape": "naked call"}
    close = [{"quantity": -1, "expiration": "2026-11-20", "strike": 55.0, "right": "C", "price": 1.50}]
    opens = [{"quantity": -1, "expiration": LATER, "strike": 56.0, "right": "C", "price": 1.80}]
    kw = {"session": date(2026, 10, 13), "spot": 55.5, "rules": _rules.effective(None)[0]}
    naked = rollcheck.check(trade, close, opens, **kw)
    covered = rollcheck.check(trade, close, opens, **kw, shares=100)
    assert naked["verdict"] == "reject"
    assert covered["verdict"] == "ok", covered["problems"]
    # What it can lose is the shares falling to the grid's floor, less the premium: 5,550 x 0.99 - 80
    # before; the roll's 30 credit takes 30 off after.
    assert covered["max_loss_before"] == pytest.approx(5550 * 0.99 - 80, abs=1)
    assert covered["max_loss_after"] == pytest.approx(5550 * 0.99 - 110, abs=1)


# --------------------------------------------------------------------------- wheel.next_leg
ASSIGNED = [
    tx(1, "2026-09-01T15:00:00Z", "Sell to Open", occ("2026-10-09", "P", 50), 1, 1.00),
    *assigned("2026-10-09T22:00:00Z", occ("2026-10-09", "P", 50), 1, 50),
]
BANDS = {
    "day": "2026-10-12",
    "names": {
        "XYZ": {
            "spot": 49.0,
            "rows": [
                {
                    "side": "C",
                    "expiry": LATER,
                    "bands": {"fixed": {"band": 55.4}, "by_expiry": {"band": 54.0}},
                }
            ],
        }
    },
}


def test_after_an_assignment_the_next_leg_waits_for_the_band():
    acct = evaluate(ASSIGNED, [share_leg(100, 49.0, 50.0)], 49.0, income=INCOME)
    t = trade_of(acct, "XYZ")
    f = fired(t)["wheel.next_leg"]
    assert f["action"] == ev.REVIEW and f["confidence"] == 50
    assert "band not in the snapshot" in f["reason"]
    assert t["wheel"][0]["next_leg"]["strike"] is None


def test_after_an_assignment_the_next_leg_is_the_first_strike_past_the_call_band():
    acct = evaluate(ASSIGNED, [share_leg(100, 50.0, 50.0)], 50.0, income=INCOME, bands=BANDS)
    nl = trade_of(acct, "XYZ")["wheel"][0]["next_leg"]
    # The band moves up with the price: 55.40 + (50 - 49) = 56.40, so the 57 call.
    assert nl["band"] == pytest.approx(56.4)
    assert nl["strike"] == 57 and nl["expiration"] == LATER and nl["contracts"] == 1
    assert nl["below_wheel_basis"] is False
    by_expiry = evaluate(
        ASSIGNED,
        [share_leg(100, 50.0, 50.0)],
        50.0,
        income=INCOME,
        bands=BANDS,
        overrides={"wheel": {"next_leg": {"band": "by_expiry"}}},
    )
    assert trade_of(by_expiry, "XYZ")["wheel"][0]["next_leg"]["strike"] == 55


def test_shares_already_written_against_need_no_next_leg():
    call = occ("2026-11-20", "C", 55)
    history = [*ASSIGNED, tx(3, "2026-10-12T15:00:00Z", "Sell to Open", call, 1, 0.80)]
    legs = [share_leg(100, 50.0, 50.0), opt_leg(call, -1, 0.70, 0.80)]
    acct = evaluate(history, legs, 50.0, income=INCOME, bands=BANDS)
    for t in acct["trades"]:
        assert "wheel.next_leg" not in fired(t)


def test_shares_from_before_the_history_are_no_assignment():
    legs = [share_leg(100, 50.0, 40.0)]
    acct = evaluate([], legs, 50.0, income=INCOME, bands=BANDS)
    assert acct["wheel"]["cycles"]
    assert all("wheel.next_leg" not in fired(t) for t in acct["trades"])


# --------------------------------------------------------------------------- failing closed
def test_an_unreconciled_cycle_gets_a_review_and_no_wheel_rule():
    # A put the history never traded: the cycle cannot be replayed.
    t = trade_of(evaluate([], [opt_leg(PUT, -1, 0.02, 1.00)], 50.0, income=INCOME), PUT)
    names = fired(t)
    assert names["wheel_data"]["action"] == ev.REVIEW
    assert "does not reconcile" in names["wheel_data"]["reason"]
    assert not any(r.startswith("wheel.") for r in names)
    assert "take_profit" not in t["wheel"][0]


def test_a_delivery_nothing_explains_fails_closed_too():
    history = [
        rd("2026-10-01T12:00:00Z", "Buy to Open", "Buy to Open", "XYZ", 100, 40, -4000, "Equity"),
        tx(2, "2026-10-02T15:00:00Z", "Sell to Open", CALL, 1, 0.80),
    ]
    acct = evaluate(history, [share_leg(100, 41.0, 40.0), opt_leg(CALL, -1, 0.02, 0.80)], 41.0)
    for t in acct["trades"]:
        assert not any(r.startswith("wheel.") for r in fired(t))
    assert any("wheel_data" in fired(t) for t in acct["trades"])


def test_each_wheel_rule_can_be_turned_off():
    for rule, overrides in (
        ("wheel.take_profit", {"positions": {"XYZ": {"disable": ["wheel.take_profit"]}}}),
        ("wheel.take_profit", {"wheel": {"take_profit": {"enabled": False}}}),
    ):
        t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 0.03, 1.00)], 60.0, overrides=overrides), PUT)
        assert rule not in fired(t)
    off = {"wheel": {"tested": {"enabled": False}}}
    t = trade_of(evaluate(OPENED, [opt_leg(PUT, -1, 1.20, 1.00)], 50.0, income=INCOME, overrides=off), PUT)
    assert "wheel.tested" not in fired(t)
    off = {"wheel": {"next_leg": {"enabled": False}}}
    acct = evaluate(ASSIGNED, [share_leg(100, 49.0, 50.0)], 49.0, overrides=off)
    assert "wheel.next_leg" not in fired(trade_of(acct, "XYZ"))


def test_the_wheel_rules_wait_for_an_earnings_trade_s_call():
    assert "wheel" in ev.HELD_FOR_EARNINGS


def test_rule_ids_are_known_to_overrides():
    for rule in ("wheel.take_profit", "wheel.tested", "wheel.next_leg"):
        assert rule in _rules.RULE_IDS
    _, warnings = _rules.effective({"positions": {"XYZ": {"disable": ["wheel.tested"]}}})
    assert warnings == []
    assert copy.deepcopy(_rules.DEFAULT_RULES["wheel"]["take_profit"]["btc_price"]) == 0.03
