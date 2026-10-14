from datetime import date

import pytest

from cherrypick.manage import chains
from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules


def occ(root, exp, right, strike):
    return f"{root:<6}{exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}"


def tx(order, when, action, symbol, qty, price, kind="Trade"):
    sign = -1 if action.startswith("Buy") else 1
    return {
        "id": f"{order}-{symbol}-{action}",
        "order_id": order,
        "executed_at": when,
        "transaction_type": kind,
        "action": action,
        "symbol": symbol,
        "quantity": str(qty),
        "price": str(price),
        "value": str(sign * qty * price * 100),
        "commission": "-1.0",
        "clearing_fees": "-0.1",
        "regulatory_fees": "-0.02",
        "proprietary_index_option_fees": "0.0",
    }


L410 = occ("GLD", "2026-11-20", "C", 410)
S430, L450 = occ("GLD", "2026-10-16", "C", 430), occ("GLD", "2026-10-16", "C", 450)
S425, L445 = occ("GLD", "2026-10-23", "C", 425), occ("GLD", "2026-10-23", "C", 445)
S415, L435 = occ("GLD", "2026-11-20", "C", 415), occ("GLD", "2026-11-20", "C", 435)

# GLD's real chain: opened 9/02 for 8.33 db, rolled 9/16 for 2.50 cr and 10/09 for 3.90 cr.
GLD_HISTORY = [
    # an unrelated, closed August trade on the same name stays out of the chain
    tx(1, "2026-08-07T14:48:50Z", "Sell to Open", occ("GLD", "2026-09-18", "C", 403), 1, 11.57),
    tx(1, "2026-08-07T14:48:50Z", "Buy to Open", occ("GLD", "2026-09-18", "C", 405), 1, 10.77),
    tx(2, "2026-08-10T16:07:33Z", "Buy to Close", occ("GLD", "2026-09-18", "C", 403), 1, 12.02),
    tx(2, "2026-08-10T16:07:33Z", "Sell to Close", occ("GLD", "2026-09-18", "C", 405), 1, 11.15),
    tx(10, "2026-09-02T16:30:30Z", "Buy to Open", L410, 1, 15.17),
    tx(10, "2026-09-02T16:30:30Z", "Sell to Open", S430, 2, 4.42),
    tx(10, "2026-09-02T16:30:30Z", "Buy to Open", L450, 1, 2.00),
    tx(11, "2026-09-16T18:07:19Z", "Sell to Open", S425, 2, 4.27),
    tx(11, "2026-09-16T18:07:19Z", "Buy to Open", L445, 1, 1.80),
    tx(11, "2026-09-16T18:07:19Z", "Buy to Close", S430, 2, 2.68),
    tx(11, "2026-09-16T18:07:19Z", "Sell to Close", L450, 1, 1.12),
    tx(12, "2026-10-09T14:34:48Z", "Buy to Close", S425, 2, 0.14),
    tx(12, "2026-10-09T14:34:48Z", "Sell to Close", L445, 1, 0.05),
    tx(12, "2026-10-09T14:34:48Z", "Sell to Open", S415, 2, 2.57),
    tx(12, "2026-10-09T14:34:48Z", "Buy to Open", L435, 1, 1.01),
]


def pos(symbol, qty, opened, mid, underlying="GLD"):
    code = symbol.split()[1]
    return {
        "symbol": symbol,
        "underlying": underlying,
        "quantity": qty,
        "expiration": f"20{code[:2]}-{code[2:4]}-{code[4:6]}",
        "right": code[6],
        "strike": int(code[7:]) / 1000,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": qty * (mid - opened) * 100,
    }


GLD_LEGS = [pos(L410, 1, 15.17, 2.365), pos(S415, -2, 2.57, 1.825), pos(L435, 1, 1.01, 0.705)]


def test_a_rolled_trade_is_one_chain_whose_cash_includes_every_roll():
    out = chains.build(GLD_LEGS, GLD_HISTORY)
    assert out["unchained"] == []
    (chain,) = out["chains"]
    assert chain["reconciled"]
    assert [o["role"] for o in chain["orders"]] == ["open", "roll", "roll"]
    assert [round(o["net"]) for o in chain["orders"]] == [-833, 250, 390]
    assert chain["cash"] == pytest.approx(-193)
    assert chain["fees"] == pytest.approx(-11 * 1.12)


def test_the_evaluation_takes_the_chain_as_the_basis_not_the_open_legs():
    snap = {
        "session": "2026-10-13",
        "positions": {
            "ok": True,
            "accounts": [{"balances": {}, "underlyings": [{"underlying": "GLD", "legs": GLD_LEGS}]}],
        },
        "metrics": {"GLD": {"last": 381.65}},
        "transactions": {"GLD": GLD_HISTORY},
        "transactions_error": None,
    }
    (t,) = ev.evaluate(snap, _rules.effective(None)[0])["accounts"][0]["trades"]
    assert t["basis"] == "order_chain"
    assert t["cost"] == pytest.approx(193)
    assert t["pnl_gross"] == pytest.approx(-58 - 193)
    assert t["pnl_net"] == pytest.approx(-58 - 193 - 11 * 1.12)
    assert t["opening"]["net_price"] == pytest.approx(8.33)
    assert t["chain"]["rolls"] == 2
    assert (
        "You opened this on Sep 2 and paid $833, then adjusted it twice, taking in $640, "
        "so you are in for $193 net" in t["explanation"]
    )


def test_without_history_the_trade_falls_back_to_the_open_legs_and_says_so():
    snap = {
        "session": "2026-10-13",
        "positions": {
            "ok": True,
            "accounts": [{"balances": {}, "underlyings": [{"underlying": "GLD", "legs": GLD_LEGS}]}],
        },
        "metrics": {"GLD": {"last": 381.65}},
    }
    out = ev.evaluate(snap, _rules.effective(None)[0])
    (t,) = out["accounts"][0]["trades"]
    assert t["basis"] == "open_legs"
    assert t["cost"] == pytest.approx(1104)
    assert "no trade history" in out["transactions_error"]


def test_independent_orders_on_one_name_are_separate_trades():
    put = occ("SOFI", "2026-11-20", "P", 16)
    c18, c20 = occ("SOFI", "2026-11-20", "C", 18), occ("SOFI", "2026-11-20", "C", 20)
    c20_old = occ("SOFI", "2026-10-16", "C", 20)
    history = [
        tx(1, "2026-09-11T15:00:00Z", "Sell to Open", put, 1, 1.01),
        tx(2, "2026-09-11T15:05:00Z", "Buy to Open", c18, 1, 1.46),
        tx(2, "2026-09-11T15:05:00Z", "Sell to Open", c20_old, 1, 0.29),
        tx(3, "2026-10-08T15:00:00Z", "Buy to Close", c20_old, 1, 0.02),
        tx(3, "2026-10-08T15:00:00Z", "Sell to Open", c20, 1, 0.17),
    ]
    legs = [
        pos(put, -1, 1.01, 1.445, "SOFI"),
        pos(c18, 1, 1.46, 0.28, "SOFI"),
        pos(c20, -1, 0.17, 0.115, "SOFI"),
    ]
    out = chains.build(legs, history)
    cash = sorted(round(c["cash"]) for c in out["chains"])
    assert cash == [-102, 101]
    groups = ev.group_trades(legs, "order_chain", history, True)
    assert sorted(len(g) for g, _ in groups) == [1, 2]


def test_history_that_starts_after_the_position_cannot_explain_it():
    # The 9/02 opening order is outside the fetched window: the rolls alone do not reconcile.
    out = chains.build(GLD_LEGS, [r for r in GLD_HISTORY if r["order_id"] != 10])
    assert out["unchained"] == sorted(" ".join(leg["symbol"].split()) for leg in GLD_LEGS)
    groups = ev.group_trades(GLD_LEGS, "order_chain", [r for r in GLD_HISTORY if r["order_id"] != 10], True)
    assert all(chain is None for _, chain in groups)


def test_a_leg_moved_without_an_order_breaks_the_reconciliation():
    legs = [pos(L410, 2, 15.17, 2.365), pos(S415, -2, 2.57, 1.825), pos(L435, 1, 1.01, 0.705)]
    out = chains.build(legs, GLD_HISTORY)
    assert not out["chains"][0]["reconciled"]


def test_partial_fills_of_one_leg_are_one_line():
    a = occ("AMZN", "2026-10-30", "C", 285)
    rows = [
        tx(5, "2026-09-16T15:00:00Z", "Sell to Open", a, 1, 2.61),
        tx(5, "2026-09-16T15:00:00Z", "Sell to Open", a, 1, 2.62),
    ]
    (chain,) = chains.build([pos(a, -2, 2.615, 2.325, "AMZN")], rows)["chains"]
    (fill,) = chain["orders"][0]["legs"]
    assert fill["quantity"] == 2 and fill["price"] == pytest.approx(2.615)


def test_session_dates_are_unaffected():
    assert ev._exit_day(date(2026, 11, 20), 21) == "2026-10-30"
