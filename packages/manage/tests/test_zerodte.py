"""The hand 0DTE view: today's excluded legs split into the loops' and the hand's (`zerodte.py`).

Every strike, price and id here is made up."""

from datetime import date

from test_evaluate import leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules
from cherrypick.manage import stream_request, zerodte

SESSION = "2026-10-13"


def fill(order_id, action, right, strike, price, at="2026-10-13T18:00:00Z", qty=1, exp=SESSION):
    """One broker Trade row as the account-wide history lists it; fees 0.50 a contract."""
    occ = f"{'SPXW':<6}{exp.replace('-', '')[2:]}{right}{int(strike * 1000):08d}"
    value = price * 100 * qty * (1 if action.startswith("Sell") else -1)
    return {
        "order_id": order_id,
        "executed_at": at,
        "transaction_type": "Trade",
        "action": action,
        "symbol": occ,
        "underlying_symbol": "SPX",
        "instrument_type": "Equity Option",
        "quantity": str(qty),
        "price": str(price),
        "value": str(value),
        "net_value": str(value - 0.50 * qty),
    }


# The loop's IC (order 100): its put side stopped out by order 101, its call side still held.
LOOP = [
    fill(100, "Sell to Open", "P", 5000, 2.00),
    fill(100, "Buy to Open", "P", 4990, 1.00),
    fill(100, "Sell to Open", "C", 5100, 1.50),
    fill(100, "Buy to Open", "C", 5110, 0.50),
    fill(101, "Buy to Close", "P", 5000, 6.00, at="2026-10-13T18:50:00Z"),
    fill(101, "Sell to Close", "P", 4990, 2.50, at="2026-10-13T18:50:00Z"),
]
# A hand IC (order 200), all four legs still held.
HAND = [
    fill(200, "Sell to Open", "P", 4980, 2.50, at="2026-10-13T18:35:00Z"),
    fill(200, "Buy to Open", "P", 4970, 1.20, at="2026-10-13T18:35:00Z"),
    fill(200, "Sell to Open", "C", 5080, 1.30, at="2026-10-13T18:35:00Z"),
    fill(200, "Buy to Open", "C", 5090, 0.40, at="2026-10-13T18:35:00Z"),
]
OLDER = [fill(9, "Sell to Open", "P", 4500, 1.0, at="2026-10-01T15:00:00Z", exp="2026-10-01")]
HISTORY = [*reversed(HAND + LOOP), *OLDER]  # newest first, as the broker lists it

HELD = [
    leg(-1, "C", 5100, SESSION, 1.50, 0.10, root="SPXW", underlying="SPX"),
    leg(1, "C", 5110, SESSION, 0.50, 0.05, root="SPXW", underlying="SPX"),
    leg(-1, "P", 4980, SESSION, 2.50, 0.50, root="SPXW", underlying="SPX"),
    leg(1, "P", 4970, SESSION, 1.20, 0.20, root="SPXW", underlying="SPX"),
    leg(-1, "C", 5080, SESSION, 1.30, 0.30, root="SPXW", underlying="SPX"),
    leg(1, "C", 5090, SESSION, 0.40, 0.10, root="SPXW", underlying="SPX"),
]
KNOWN = {"meic": {"at": "2026-10-13T15:00:00-04:00", "order_ids": ["100", "101"]}}


def read(held=HELD, history=HISTORY, known=KNOWN, known_error=None, tx_error=None):
    return zerodte.reading(
        session=date.fromisoformat(SESSION),
        names={"SPX"},
        held=held,
        transactions=history,
        transactions_error=tx_error,
        known=known,
        known_error=known_error,
    )


def test_the_hand_ic_is_split_from_the_loops():
    out = read()
    assert out["read"] is True and out["unreconciled"] == []
    (order,) = out["orders"]
    assert order["order_id"] == "200" and order["kind"] == "open" and len(order["legs"]) == 4
    # signed cash: 250 - 120 + 130 - 40 = +220 credit; fees 4 x 0.50
    assert order["cash"] == 220.0 and order["fees"] == 2.0
    assert out["loop_fills"] == 6
    assert {leg["symbol"][-9:] for leg in out["open_legs"]} == {
        "P04980000",
        "P04970000",
        "C05080000",
        "C05090000",
    }
    # the mark of the four hand legs at mid: -50 + 20 - 30 + 10 = -50
    assert out["mark"] == -50.0
    assert out["gross"] == 170.0
    assert out["settlement"] is None  # still held: the cash settlement is not booked yet
    assert out["net"] == 168.0 and out["net_basis"].startswith("at mark")
    assert out["after_publish"] == []


def test_a_held_leg_todays_fills_cannot_explain_makes_the_mark_and_net_null():
    """The reconciliation guard. Shown to fail by dropping the loop's 5100 call from the fills: the
    held short call is then unexplained and nothing is part-counted."""
    history = [r for r in HISTORY if not r["symbol"].endswith("C05100000")]
    out = read(history=history)
    (bad,) = out["unreconciled"]
    assert bad["symbol"].endswith("C05100000") and bad["held"] == -1.0 and bad["loop_fills"] == 0.0
    assert out["mark"] is None and out["gross"] is None and out["net"] is None
    assert out["cash"] == 220.0  # what the fills say is still stated


def test_a_closed_hand_trade_is_realised_with_no_settlement():
    close = [
        fill(201, "Buy to Close", "P", 4980, 0.40, at="2026-10-13T19:30:00Z"),
        fill(201, "Sell to Close", "P", 4970, 0.10, at="2026-10-13T19:30:00Z"),
        fill(201, "Buy to Close", "C", 5080, 0.20, at="2026-10-13T19:30:00Z"),
        fill(201, "Sell to Close", "C", 5090, 0.05, at="2026-10-13T19:30:00Z"),
    ]
    held = [h for h in HELD if h["strike"] in (5100, 5110)]
    out = read(held=held, history=[*reversed(close), *HISTORY])
    assert out["open_legs"] == [] and out["unreconciled"] == []
    # +220 in, -45 out; fees 8 x 0.50
    assert out["cash"] == 175.0 and out["mark"] == 0.0 and out["fees"] == 4.0
    assert out["settlement"] == 0.0 and out["net"] == 171.0 and out["net_basis"].startswith("realised")
    assert [o["kind"] for o in out["orders"]] == ["open", "close"]


def test_without_the_loops_ids_every_fill_is_by_hand_and_it_says_so():
    out = read(known={})
    assert {o["order_id"] for o in out["orders"]} == {"100", "101", "200"}
    assert any("no live loop has published" in c for c in out["caveats"])


def test_a_fill_newer_than_the_newest_publish_is_listed():
    known = {"meic": {"at": "2026-10-13T14:00:00-04:00", "order_ids": ["100", "101"]}}  # 18:00Z
    out = read(known=known)
    assert out["after_publish"] == ["200"]


def test_an_older_snapshot_has_no_view():
    out = read(known=None)
    assert out["read"] is False and "v16" in out["error"]
    out = read(tx_error="broker said no")
    assert out["read"] is False and out["error"] == "broker said no"


def test_a_history_holding_only_today_is_flagged_as_maybe_short():
    out = read(history=list(reversed(HAND + LOOP)))
    assert any("may not all be in it" in c for c in out["caveats"])


def test_the_evaluation_carries_the_view_and_the_stream_request_its_legs():
    snapshot = {
        "session": SESSION,
        "positions": {
            "ok": True,
            "accounts": [{"balances": {}, "underlyings": [{"underlying": "SPX", "legs": HELD}]}],
        },
        "metrics": {"SPX": {"last": 5040.0}},
        "account_transactions": HISTORY,
        "account_transactions_error": None,
        "live_known_orders": KNOWN,
        "live_known_orders_error": None,
    }
    out = ev.evaluate(snapshot, _rules.effective(None)[0])
    account = out["accounts"][0]
    assert account["trades"] == [] and account["excluded"][0]["legs"] == 6
    assert account["zero_dte"]["net"] == 168.0
    symbols = stream_request.held_symbols(out)
    # the hand's leg is declared; the loop's is the loop's own to declare
    assert ".SPXW261013P4980" in symbols and ".SPXW261013C5100" not in symbols


def test_no_view_when_the_ruleset_excludes_nothing():
    rules, _ = _rules.effective({"exclude": {"zero_dte_underlyings": []}})
    snapshot = {
        "session": SESSION,
        "positions": {"ok": True, "accounts": [{"balances": {}, "underlyings": []}]},
        "metrics": {},
    }
    assert ev.evaluate(snapshot, rules)["accounts"][0]["zero_dte"] is None
