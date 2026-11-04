"""The hand 0DTE view: today's excluded legs split into the loops' and the hand's (`zerodte.py`)."""

from test_evaluate import leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules
from cherrypick.manage import stream_request, zerodte

SESSION = "2026-10-13"


def fill(order_id, action, right, strike, price, at="2026-10-13T18:00:00Z", qty=1, exp=SESSION):
    """One broker Trade row as the account-wide history lists it; fees 0.72 a contract."""
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
        "net_value": str(value - 0.72 * qty),
    }


# The loop's IC (order 100): its put side stopped out by order 101, its call side still held.
LOOP = [
    fill(100, "Sell to Open", "P", 7570, 2.18),
    fill(100, "Buy to Open", "P", 7560, 1.12),
    fill(100, "Sell to Open", "C", 7605, 1.41),
    fill(100, "Buy to Open", "C", 7615, 0.57),
    fill(101, "Buy to Close", "P", 7570, 7.29, at="2026-10-13T18:50:00Z"),
    fill(101, "Sell to Close", "P", 7560, 3.34, at="2026-10-13T18:50:00Z"),
]
# A hand IC (order 200), all four legs still held.
HAND = [
    fill(200, "Sell to Open", "P", 7555, 2.12, at="2026-10-13T18:35:00Z"),
    fill(200, "Buy to Open", "P", 7545, 1.07, at="2026-10-13T18:35:00Z"),
    fill(200, "Sell to Open", "C", 7590, 1.17, at="2026-10-13T18:35:00Z"),
    fill(200, "Buy to Open", "C", 7600, 0.42, at="2026-10-13T18:35:00Z"),
]
OLDER = [fill(9, "Sell to Open", "P", 7000, 1.0, at="2026-10-01T15:00:00Z", exp="2026-10-01")]
HISTORY = [*reversed(HAND + LOOP), *OLDER]  # newest first, as the broker lists it

HELD = [
    leg(-1, "C", 7605, SESSION, 1.41, 0.10, root="SPXW", underlying="SPX"),
    leg(1, "C", 7615, SESSION, 0.57, 0.05, root="SPXW", underlying="SPX"),
    leg(-1, "P", 7555, SESSION, 2.12, 0.50, root="SPXW", underlying="SPX"),
    leg(1, "P", 7545, SESSION, 1.07, 0.20, root="SPXW", underlying="SPX"),
    leg(-1, "C", 7590, SESSION, 1.17, 0.30, root="SPXW", underlying="SPX"),
    leg(1, "C", 7600, SESSION, 0.42, 0.10, root="SPXW", underlying="SPX"),
]
KNOWN = {"meic": {"at": "2026-10-13T15:00:00-04:00", "order_ids": ["100", "101"]}}


def read(held=HELD, history=HISTORY, known=KNOWN, known_error=None, tx_error=None):
    from datetime import date

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
    # signed cash: 212 - 107 + 117 - 42 = +180 credit; fees 4 x 0.72
    assert order["cash"] == 180.0 and order["fees"] == 2.88
    assert out["loop_fills"] == 6
    assert {leg["symbol"][-9:] for leg in out["open_legs"]} == {
        "P07555000",
        "P07545000",
        "C07590000",
        "C07600000",
    }
    # the mark of the four hand legs at mid: -50 + 20 - 30 + 10 = -50
    assert out["mark"] == -50.0
    assert out["gross"] == 130.0
    assert out["settlement"] is None  # still held: the cash settlement is not booked yet
    assert out["net"] == 127.12 and out["net_basis"].startswith("at mark")
    assert out["after_publish"] == []


def test_a_held_leg_todays_fills_cannot_explain_makes_the_mark_and_net_null():
    """The reconciliation guard. Shown to fail by dropping the loop's 7605 call from the fills: the
    held short call is then unexplained and nothing is part-counted."""
    history = [r for r in HISTORY if not r["symbol"].endswith("C07605000")]
    out = read(history=history)
    (bad,) = out["unreconciled"]
    assert bad["symbol"].endswith("C07605000") and bad["held"] == -1.0 and bad["loop_fills"] == 0.0
    assert out["mark"] is None and out["gross"] is None and out["net"] is None
    assert out["cash"] == 180.0  # what the fills say is still stated


def test_a_closed_hand_trade_is_realised_with_no_settlement():
    close = [
        fill(201, "Buy to Close", "P", 7555, 0.40, at="2026-10-13T19:30:00Z"),
        fill(201, "Sell to Close", "P", 7545, 0.10, at="2026-10-13T19:30:00Z"),
        fill(201, "Buy to Close", "C", 7590, 0.20, at="2026-10-13T19:30:00Z"),
        fill(201, "Sell to Close", "C", 7600, 0.05, at="2026-10-13T19:30:00Z"),
    ]
    held = [h for h in HELD if h["strike"] in (7605, 7615)]
    out = read(held=held, history=[*reversed(close), *HISTORY])
    assert out["open_legs"] == [] and out["unreconciled"] == []
    # +180 in, -45 out; fees 8 x 0.72
    assert out["cash"] == 135.0 and out["mark"] == 0.0 and out["fees"] == 5.76
    assert out["settlement"] == 0.0 and out["net"] == 129.24 and out["net_basis"].startswith("realised")
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
        "metrics": {"SPX": {"last": 7580.0}},
        "account_transactions": HISTORY,
        "account_transactions_error": None,
        "live_known_orders": KNOWN,
        "live_known_orders_error": None,
    }
    out = ev.evaluate(snapshot, _rules.effective(None)[0])
    account = out["accounts"][0]
    assert account["trades"] == [] and account["excluded"][0]["legs"] == 6
    assert account["zero_dte"]["net"] == 127.12
    symbols = stream_request.held_symbols(out)
    assert ".SPXW261013P7555" in symbols and ".SPXW261013C7605" not in symbols  # the loop's leg is its own


def test_no_view_when_the_ruleset_excludes_nothing():
    rules, _ = _rules.effective({"exclude": {"zero_dte_underlyings": []}})
    snapshot = {
        "session": SESSION,
        "positions": {"ok": True, "accounts": [{"balances": {}, "underlyings": []}]},
        "metrics": {},
    }
    assert ev.evaluate(snapshot, rules)["accounts"][0]["zero_dte"] is None
