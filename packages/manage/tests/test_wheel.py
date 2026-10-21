"""The wheel view: cycle linking, accounting and today's check, on synthetic histories.

Rows imitate the broker's shapes (a Receive Deliver option row and a share row per assignment, the
row's own signed `value` as its cash), with made-up names, prices and ids.
"""

from datetime import date

import pytest

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules
from cherrypick.manage import wheel

FEE = -1.10  # each trade row's fees: commission -1.00, clearing -0.10


def occ(exp, right, strike, root="XYZ"):
    return f"{root:<6}{exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}"


def tx(order, when, action, symbol, qty, price, kind="Equity Option"):
    sign = -1 if action.startswith("Buy") else 1
    mult = 100 if kind == "Equity Option" else 1
    return {
        "id": f"{order}-{symbol}-{action}",
        "order_id": order,
        "executed_at": when,
        "transaction_type": "Trade",
        "transaction_sub_type": action,
        "action": action,
        "instrument_type": kind,
        "symbol": symbol,
        "underlying_symbol": "XYZ",
        "quantity": str(qty),
        "price": str(price),
        "value": str(sign * qty * price * mult),
        "commission": "-1.0",
        "clearing_fees": "-0.1",
        "regulatory_fees": "0.0",
        "proprietary_index_option_fees": "0.0",
    }


def rd(when, sub, action, symbol, qty, price=None, value=0.0, kind="Equity Option", rid=None):
    """A Receive Deliver row: no order, the broker's cash in `value`."""
    return {
        "id": rid or f"rd-{when}-{symbol}-{sub}",
        "order_id": None,
        "executed_at": when,
        "transaction_type": "Receive Deliver",
        "transaction_sub_type": sub,
        "action": action,
        "instrument_type": kind,
        "symbol": symbol,
        "underlying_symbol": "XYZ",
        "quantity": str(qty),
        "price": None if price is None else str(price),
        "value": str(value),
        "commission": None,
        "clearing_fees": "-5.0" if sub == "Assignment" and kind == "Equity Option" else None,
        "regulatory_fees": None,
        "proprietary_index_option_fees": None,
    }


def assigned(when, symbol, contracts, strike, put=True, share_day=None):
    """A short option assigned: its option row and the matching share row."""
    shares = contracts * 100
    share_when = share_day or when
    return [
        rd(when, "Assignment", "Buy to Close", symbol, contracts),
        rd(
            share_when,
            "Buy to Open" if put else "Sell to Close",
            "Buy to Open" if put else "Sell to Close",
            "XYZ",
            shares,
            strike,
            (-1 if put else 1) * shares * strike,
            kind="Equity",
        ),
    ]


def opt_leg(symbol, qty, mid, opened):
    exp = f"20{symbol[6:8]}-{symbol[8:10]}-{symbol[10:12]}"
    return {
        "symbol": symbol,
        "underlying": "XYZ",
        "instrument_type": "Equity Option",
        "quantity": qty,
        "expiration": exp,
        "right": symbol[12],
        "strike": int(symbol[13:]) / 1000,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": qty * (mid - opened) * 100,
    }


def share_leg(qty, mid, opened):
    return {
        "symbol": "XYZ",
        "underlying": "XYZ",
        "instrument_type": "Equity",
        "quantity": qty,
        "expiration": None,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid,
        "open_pl": qty * (mid - opened),
    }


def build(history, legs=(), session=date(2026, 10, 13), spot=None, orders=None, bands=None):
    under = [{"underlying": "XYZ", "legs": list(legs)}] if legs else []
    metrics = {"XYZ": {"last": spot}} if spot else {}
    return wheel.build(under, {"XYZ": history}, session, metrics, orders, bands)


PUT50 = occ("2026-08-21", "P", 50)
CALL52 = occ("2026-09-18", "C", 52)

# Sell a put, get assigned, write a call against the shares, get called away.
PLAIN = [
    tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
    *assigned("2026-08-21T22:00:00Z", PUT50, 1, 50, share_day="2026-08-24T12:00:00Z"),
    tx(2, "2026-08-25T15:00:00Z", "Sell to Open", CALL52, 1, 0.80),
    *assigned("2026-09-18T22:00:00Z", CALL52, 1, 52, put=False),
]


# --------------------------------------------------------------------------- a plain wheel
def test_a_plain_wheel_is_one_complete_cycle():
    out = build(PLAIN)
    (c,) = out["cycles"]
    assert c["status"] == "complete" and c["reconciled"] and c["flat"]
    assert (c["start"], c["end"]) == ("2026-08-03", "2026-09-18")
    assert c["days"] == 46
    assert (c["assignments"], c["called_away"]) == (1, 1)
    # The broker's cash: +100 premium, -5,000 for the shares, +80 premium, +5,200 called away.
    assert c["net_cash"] == pytest.approx(380)
    assert c["credits"] == pytest.approx(5380) and c["debits"] == pytest.approx(-5000)
    assert c["net_premium"] == pytest.approx(180)
    assert c["fees"] == pytest.approx(2 * FEE - 10.0)  # two trades, two assignment fees
    assert c["option_pnl"] == {"realised": pytest.approx(180), "unrealised": 0.0}
    assert c["stock_pnl"] == {"realised": pytest.approx(200), "unrealised": 0.0}
    assert c["pnl_net"] == pytest.approx(380 + 2 * FEE - 10.0)
    assert c["today"] == []
    assert out["flagged"] == []


def test_a_running_wheel_has_both_bases_a_breakeven_and_today_s_check():
    history = PLAIN[:4]  # put, assignment, call written
    legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    orders = [
        {
            "id": 77,
            "status": "Live",
            "underlying_symbol": "XYZ",
            "price": "0.05",
            "time_in_force": "GTC",
            "legs": [{"symbol": CALL52, "action": "Buy to Close", "quantity": 1, "remaining_quantity": 1}],
        },
        # A filled order is history, not a resting close.
        {
            "id": 78,
            "status": "Filled",
            "price": "0.10",
            "legs": [{"symbol": CALL52, "action": "Buy to Close"}],
        },
    ]
    bands = {
        "day": "2026-08-31",
        "names": {
            "XYZ": {
                "spot": 50.0,
                "rows": [{"side": "C", "expiry": "2026-09-18", "bands": {"fixed": {"band": 53.0}}}],
            }
        },
    }
    out = build(history, legs, session=date(2026, 9, 1), spot=51.0, orders=orders, bands=bands)
    (c,) = out["cycles"]
    assert c["status"] == "open" and c["reconciled"] and not c["flat"]
    assert c["tax_basis"] == pytest.approx(50.0)
    fees = 2 * FEE - 5.0
    # (5,000 for the shares - 100 - 80 premium - fees) / 100
    assert c["wheel_basis"] == pytest.approx((5000 - 180 - fees) / 100)
    assert c["breakeven"]["price"] == pytest.approx(c["wheel_basis"])
    assert c["option_pnl"]["realised"] == pytest.approx(100)
    assert c["option_pnl"]["unrealised"] == pytest.approx(80 - 50)
    assert c["stock_pnl"] == {"realised": 0.0, "unrealised": pytest.approx(100)}
    assert c["basis_known"] and c["start_known"]
    # Capital: 5,000 secured by the put from 8/03 to 8/24, then the shares' 5,000: 5,000 throughout.
    assert c["skate_yield"]["capital"] == pytest.approx(5000)
    assert c["skate_yield"]["value"] == pytest.approx(c["pnl_net"] / 5000 * 365 / 29)
    (t,) = c["today"]
    assert t["leg"] == "2026-09-18 52C" and t["tested"] is False
    assert t["extrinsic"] == pytest.approx(0.50) and t["extrinsic_dollars"] == pytest.approx(50)
    assert t["band"]["fixed"] == pytest.approx(54.0)  # moved up with the price
    assert t["beyond_band"] == {"fixed": False}
    assert [r["order_id"] for r in t["resting_btc"]] == [77]
    assert t["btc_price"] == pytest.approx(0.05) and t["mark_at_or_under_btc"] is False


def test_a_tested_call_and_no_band_are_said_so():
    legs = [share_leg(100, 53.0, 50.0), opt_leg(CALL52, -1, 1.20, 0.80)]
    out = build(PLAIN[:4], legs, session=date(2026, 9, 1), spot=53.0)
    (t,) = out["cycles"][0]["today"]
    assert t["tested"] is True
    assert t["intrinsic"] == pytest.approx(1.0) and t["extrinsic"] == pytest.approx(0.20)
    assert t["band"] is None and t["band_missing"] and out["bands_missing"]
    assert t["resting_btc"] == [] and t["mark_at_or_under_btc"] is None


# --------------------------------------------------------------------------- a roll
def test_a_rolled_put_is_one_cycle_with_the_roll_counted():
    put48 = occ("2026-09-18", "P", 48)
    history = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
        tx(2, "2026-08-18T15:00:00Z", "Buy to Close", PUT50, 1, 2.00),
        tx(2, "2026-08-18T15:00:00Z", "Sell to Open", put48, 1, 2.30),
        rd("2026-09-18T22:00:00Z", "Expiration", "Buy to Close", put48, 1),
    ]
    (c,) = build(history)["cycles"]
    assert c["status"] == "complete"
    assert [e["what"] for e in c["events"]] == ["open", "roll", "option delivery"]
    assert c["net_cash"] == pytest.approx(100 - 200 + 230)
    assert c["assignments"] == 0


def test_a_net_debit_roll_raises_the_wheel_basis():
    put48 = occ("2026-09-18", "P", 48)
    credit_roll = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
        tx(2, "2026-08-18T15:00:00Z", "Buy to Close", PUT50, 1, 2.00),
        tx(2, "2026-08-18T15:00:00Z", "Sell to Open", put48, 1, 2.30),
        *assigned("2026-09-18T22:00:00Z", put48, 1, 48),
    ]
    debit_roll = [dict(r) for r in credit_roll]
    debit_roll[2] = tx(2, "2026-08-18T15:00:00Z", "Sell to Open", put48, 1, 1.50)
    legs = [share_leg(100, 47.0, 48.0)]
    a = build(credit_roll, legs, session=date(2026, 9, 21))["cycles"][0]
    b = build(debit_roll, legs, session=date(2026, 9, 21))["cycles"][0]
    assert a["reconciled"] and b["reconciled"]
    assert b["wheel_basis"] - a["wheel_basis"] == pytest.approx(0.80)
    assert a["tax_basis"] == b["tax_basis"] == pytest.approx(48.0)


def test_a_cycle_flat_at_a_loss_is_not_complete_and_the_next_put_continues_it():
    put45 = occ("2026-10-16", "P", 45)
    history = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
        tx(2, "2026-08-18T15:00:00Z", "Buy to Close", PUT50, 1, 3.00),  # closed for a loss
        tx(3, "2026-09-01T15:00:00Z", "Sell to Open", put45, 1, 0.90),
    ]
    (c,) = build(history, [opt_leg(put45, -1, 0.40, 0.90)])["cycles"]
    assert c["status"] == "open" and c["reconciled"]
    assert c["net_cash"] == pytest.approx(100 - 300 + 90)
    # Only the 2026-10-16 put is open: its strike less the cycle's net cash per share secured.
    assert c["breakeven"]["price"] == pytest.approx(45 - (-110 + 3 * FEE) / 100)


# --------------------------------------------------------------------------- assignment matching
def test_assignment_matching_rules_each_refuse_a_wrong_row():
    opt = rd("2026-08-21T22:00:00Z", "Assignment", "Buy to Close", PUT50, 1)

    def share(**kw):
        row = {
            "when": "2026-08-24T12:00:00Z",
            "action": "Buy to Open",
            "qty": 100,
            "price": 50,
            "value": -5000,
        }
        row.update(kw)
        return rd(
            row["when"], row["action"], row["action"], "XYZ", row["qty"], row["price"], row["value"], "Equity"
        )

    good = share()
    assert wheel.match_deliveries([opt], [good])[0] == [(opt, good)]
    for bad in (
        share(price=49, value=-4900),  # not at the strike
        share(qty=200, value=-10000),  # not contracts x 100
        share(when="2026-08-27T12:00:00Z"),  # outside the window
        share(action="Sell to Close", value=5000),  # the wrong side: a put assigned buys shares
    ):
        pairs, lonely, loose = wheel.match_deliveries([opt], [bad])
        assert pairs == [] and lonely == [opt] and loose == [bad]


def test_a_share_row_is_claimed_once():
    a = rd("2026-08-21T22:00:00Z", "Assignment", "Buy to Close", PUT50, 1, rid="a")
    b = rd("2026-08-21T22:00:00Z", "Assignment", "Buy to Close", PUT50, 1, rid="b")
    sh = rd("2026-08-21T22:00:00Z", "Buy to Open", "Buy to Open", "XYZ", 100, 50, -5000, "Equity")
    pairs, lonely, loose = wheel.match_deliveries([a, b], [sh])
    assert len(pairs) == 1 and len(lonely) == 1 and loose == []


def test_an_unmatched_delivery_is_flagged_and_its_cycle_unreconciled():
    history = [rd("2026-08-24T12:00:00Z", "Buy to Open", "Buy to Open", "XYZ", 100, 40, -4000, "Equity")]
    out = build(history, [share_leg(100, 41.0, 40.0)])
    (c,) = out["cycles"]
    assert c["status"] == "unreconciled" and not c["reconciled"]
    assert any("no assignment or exercise explains" in p for p in c["problems"])
    assert [f["kind"] for f in out["flagged"]] == ["share delivery"]


def test_an_assignment_with_no_share_row_is_flagged():
    history = [PLAIN[0], PLAIN[1]]  # the option row, its share row missing
    out = build(history, [share_leg(100, 51.0, 50.0)], session=date(2026, 8, 25))
    assert [f["kind"] for f in out["flagged"]] == ["assignment without shares"]
    c = out["cycles"][0]
    assert c["status"] == "unreconciled"
    assert any("no share delivery matches it" in p for p in c["problems"])
    # The 100 shares held are ones the history never delivered: a lot with no basis, never the strike.
    assert c["basis_known"] is False and c["tax_basis"] is None


def test_shares_that_left_without_a_row_break_the_replay():
    # Assigned and a call written, but nothing held today and no call-away row in the history.
    out = build(PLAIN[:4], [], session=date(2026, 9, 25))
    (c,) = out["cycles"]
    assert c["status"] == "unreconciled"
    assert any("replays to 100 shares, 0 held" in p for p in c["problems"])
    assert any("held" in s for s in out["outside"]["XYZ"])


# --------------------------------------------------------------------------- history that starts late
def test_shares_held_before_the_history_have_no_basis():
    history = [tx(2, "2026-08-25T15:00:00Z", "Sell to Open", CALL52, 1, 0.80)]
    legs = [share_leg(100, 51.0, 30.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    (c,) = build(history, legs, session=date(2026, 9, 1), spot=51.0)["cycles"]
    assert c["reconciled"] and c["status"] == "open"
    assert c["basis_known"] is False and c["start_known"] is False
    # The positions' 30.00 open price is not the history's word, and is never used.
    assert c["tax_basis"] is None and c["wheel_basis"] is None and c["breakeven"] is None
    assert c["stock_pnl"]["unrealised"] is None and c["pnl_net"] is None
    assert c["skate_yield"]["value"] is None
    assert c["option_pnl"]["unrealised"] == pytest.approx(30)  # the call's own money is known
    assert c["lots"][0]["source"] == "before the history"


def test_a_put_opened_before_the_history_is_unreconciled():
    (c,) = build([], [opt_leg(PUT50, -1, 0.40, 1.00)], session=date(2026, 8, 10))["cycles"]
    assert c["status"] == "unreconciled"
    assert any("history never traded it" in p for p in c["problems"])


def test_a_close_whose_opening_predates_the_history_breaks_the_cycle():
    history = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
        tx(2, "2026-08-05T15:00:00Z", "Buy to Close", PUT50, 2, 0.50),  # one more than it opened
    ]
    (c,) = build(history)["cycles"]
    assert c["status"] == "unreconciled"
    assert any("predates the history" in p for p in c["problems"])


# --------------------------------------------------------------------------- partial quantities
def test_partial_assignment_keeps_the_cycle_running():
    history = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 2, 1.00),
        *assigned("2026-08-21T22:00:00Z", PUT50, 1, 50),
        rd("2026-08-21T22:00:00Z", "Expiration", "Buy to Close", PUT50, 1),
        tx(2, "2026-08-25T15:00:00Z", "Sell to Open", CALL52, 1, 0.80),
    ]
    legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    (c,) = build(history, legs, session=date(2026, 9, 1))["cycles"]
    assert c["reconciled"] and c["shares"] == 100 and c["assignments"] == 1
    assert c["net_premium"] == pytest.approx(280)

    # The same history against two short calls held: the replay says one, so it is unreconciled.
    wrong = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -2, 0.50, 0.80)]
    (c,) = build(history, wrong, session=date(2026, 9, 1))["cycles"]
    assert c["status"] == "unreconciled"
    assert any("replays to -1, -2 held" in p for p in c["problems"])


def test_shares_called_away_in_part_realise_their_first_lot():
    put48 = occ("2026-09-18", "P", 48)
    call55 = occ("2026-10-16", "C", 55)
    history = [
        *PLAIN[:3],  # 100 shares at 50
        tx(3, "2026-08-26T15:00:00Z", "Sell to Open", put48, 1, 0.70),
        *assigned("2026-09-18T22:00:00Z", put48, 1, 48),  # 100 more at 48
        tx(4, "2026-09-21T15:00:00Z", "Sell to Open", call55, 1, 0.60),
        *assigned("2026-10-16T22:00:00Z", call55, 1, 55, put=False),  # 100 away at 55
    ]
    legs = [share_leg(100, 50.0, 49.0)]
    (c,) = build(history, legs, session=date(2026, 10, 19))["cycles"]
    assert c["reconciled"] and c["shares"] == 100
    assert c["stock_pnl"]["realised"] == pytest.approx(500)  # first in, first out: the 50 lot
    assert c["tax_basis"] == pytest.approx(48.0)
    assert c["stock_pnl"]["unrealised"] == pytest.approx(200)


# --------------------------------------------------------------------------- what is not a wheel
def test_spreads_and_naked_calls_are_not_wheel_legs():
    l45 = occ("2026-08-21", "P", 45)
    c60 = occ("2026-08-21", "C", 60)
    history = [
        tx(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT50, 1, 1.00),
        tx(1, "2026-08-03T15:00:00Z", "Buy to Open", l45, 1, 0.30),
        tx(2, "2026-08-04T15:00:00Z", "Sell to Open", c60, 1, 0.40),  # no shares behind it
    ]
    legs = [opt_leg(PUT50, -1, 0.5, 1.0), opt_leg(l45, 1, 0.1, 0.3), opt_leg(c60, -1, 0.2, 0.4)]
    out = build(history, legs, session=date(2026, 8, 10))
    assert out["cycles"] == []
    assert any("a call with no shares" in s for s in out["outside"]["XYZ"])


def test_none_is_never_zero_for_an_unpriced_leg():
    legs = [
        share_leg(100, 51.0, 50.0),
        {**opt_leg(CALL52, -1, 0.5, 0.8), "priced": False, "value": None, "mid": None},
    ]
    (c,) = build(PLAIN[:4], legs, session=date(2026, 9, 1), spot=51.0)["cycles"]
    assert c["option_pnl"]["unrealised"] is None and c["pnl_net"] is None
    assert c["today"][0]["extrinsic"] is None


# --------------------------------------------------------------------------- in the evaluation
def _snapshot(accounts, history):
    return {
        "session": "2026-09-01",
        "positions": {"ok": True, "accounts": accounts},
        "metrics": {"XYZ": {"last": 51.0}},
        "transactions": {"XYZ": history},
        "working_orders": [],
    }


def test_the_evaluation_carries_the_designated_account_s_cycles():
    legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    acct = {"account": "****1234", "designated": True, "underlyings": [{"underlying": "XYZ", "legs": legs}]}
    other = {"account": "****5678", "designated": False, "underlyings": [{"underlying": "XYZ", "legs": legs}]}
    out = ev.evaluate(_snapshot([acct, other], PLAIN[:4]), _rules.effective(None)[0])
    mine, theirs = out["accounts"]
    (c,) = mine["wheel"]["cycles"]
    assert c["status"] == "open" and c["trade_ids"]
    assert set(c["trade_ids"]) <= {t["trade_id"] for t in mine["trades"]}
    assert theirs["wheel"]["cycles"] == [] and theirs["wheel"]["error"]


def test_no_history_means_no_wheel_view():
    legs = [share_leg(100, 51.0, 50.0)]
    acct = {"account": "****1234", "designated": True, "underlyings": [{"underlying": "XYZ", "legs": legs}]}
    snap = _snapshot([acct], [])
    snap["transactions_error"] = "HTTP 503"
    (a,) = ev.evaluate(snap, _rules.effective(None)[0])["accounts"]
    assert a["wheel"]["cycles"] == [] and a["wheel"]["error"]
